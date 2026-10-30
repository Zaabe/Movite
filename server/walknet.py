"""Rete pedonale di Roma da OpenStreetMap, per calcolare i tratti a piedi sulle strade vere.

- Scarica da Overpass le vie percorribili a piedi, a riquadri, solo dove ci sono fermate
  (una volta sola: i riquadri restano in data/osm/ e la rete compilata in data/).
- Compila un grafo compatto: i nodi sono gli incroci, gli archi i tratti di strada tra due
  incroci, con la loro geometria per disegnare il cammino.
- Calcola le distanze a piedi reali (Dijkstra) tra punti, fermate e i cambi tra fermate vicine.
"""
import array
import gzip
import hashlib
import heapq
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import gtfs_static as gs

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HIGHWAYS = ("footway|pedestrian|path|steps|living_street|residential|service|unclassified|tertiary|"
            "tertiary_link|secondary|secondary_link|primary|primary_link|track|cycleway|corridor|road")
AREA = (41.62, 12.15, 42.12, 12.90)   # Roma Capitale, Fiumicino e dintorni (sud, ovest, nord, est)
TILE = 0.05
NET_VERSION = 2
STOPS_VERSION = 1

WALK_SPEED = 1.2        # m/s sulla rete (la distanza è già quella vera, niente fattore di deviazione)
MAX_SNAP = 250          # oltre questa distanza dalla strada più vicina il punto è "fuori rete"
FOOT_RADIUS = 450       # metri a piedi massimi per un cambio tra fermate
CELL = 0.0015           # griglia per agganciare un punto alla strada più vicina
MIN_COMPONENT = 200     # pezzi di rete scollegati più piccoli di così non si usano per agganciare


def walk_seconds(meters):
    return int(meters / WALK_SPEED)


def _hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12742000.0 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- download

def _tiles_for(g):
    s, w, n, e = AREA
    keys = set()
    for i in range(len(g.stop_ids)):
        lat, lon = g.stop_lat[i], g.stop_lon[i]
        if s <= lat < n and w <= lon < e:
            keys.add((int((lat - s) / TILE), int((lon - w) / TILE)))
    return sorted(keys)


def _tile_bbox(key, sub=None):
    s, w = AREA[0] + key[0] * TILE, AREA[1] + key[1] * TILE
    return (s, w, s + TILE, w + TILE)


def _query(bbox):
    return ('[out:json][timeout:180];way["highway"~"^(%s)$"]["foot"!~"^(no|private)$"]'
            '["access"!~"^(no|private)$"](%.4f,%.4f,%.4f,%.4f)->.w;.w out skel qt;node(w.w);out skel qt;'
            % ((HIGHWAYS,) + bbox))


def _fetch_bbox(bbox, log):
    data = urllib.parse.urlencode({"data": _query(bbox)}).encode()
    wait = 10
    for attempt in range(8):
        ep = ENDPOINTS[attempt % len(ENDPOINTS)]
        req = urllib.request.Request(ep, data=data, headers={
            "User-Agent": "Movite/1.0 (app trasporti Roma, uso personale)", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            doc = json.loads(raw)
            if "remark" in doc and "error" in doc["remark"].lower():
                raise ValueError(doc["remark"][:120])
            return doc
        except (urllib.error.URLError, OSError, ValueError) as e:
            log("Overpass non disponibile (%s), riprovo tra %d s" % (str(e)[:80], wait))
            time.sleep(wait)
            wait = min(wait * 2, 120)
    raise RuntimeError("Overpass non raggiungibile")


def download(g, osm_dir, progress):
    """Scarica i riquadri mancanti (in parallelo a coppie, come consentito da Overpass)."""
    os.makedirs(osm_dir, exist_ok=True)
    tiles = _tiles_for(g)
    todo = [k for k in tiles if not os.path.exists(os.path.join(osm_dir, "%d_%d.json.gz" % k))]
    done = [len(tiles) - len(todo)]
    failed = []                               # (riquadro, motivo): un errore non ferma gli altri
    lock = threading.Lock()
    progress("Rete pedonale: scarico le strade (%d/%d riquadri)" % (done[0], len(tiles)))

    def worker():
        while True:
            with lock:
                if not todo:
                    return
                key = todo.pop(0)
            path = os.path.join(osm_dir, "%d_%d.json.gz" % key)
            try:
                doc = _fetch_bbox(_tile_bbox(key), progress)
                with gzip.open(path + ".part", "wt", encoding="utf-8") as f:
                    json.dump(doc["elements"], f, separators=(",", ":"))
                os.replace(path + ".part", path)
            except Exception as e:
                try:                          # niente riquadri a metà sul disco (es. disco pieno)
                    os.remove(path + ".part")
                except OSError:
                    pass
                with lock:
                    failed.append((key, gs.disk_error(e) if isinstance(e, OSError) else str(e)))
                continue
            with lock:
                done[0] += 1
                progress("Rete pedonale: scarico le strade (%d/%d riquadri)" % (done[0], len(tiles)))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if failed:
        # i riquadri già scaricati restano sul disco: il prossimo tentativo riparte da quelli mancanti
        raise RuntimeError("%d/%d riquadri non scaricati (%s)" % (len(failed), len(tiles), failed[0][1]))
    return tiles


# ---------------------------------------------------------------- compilazione grafo

class WalkNet:
    pass


def compile_net(osm_dir, tiles, progress):
    progress("Rete pedonale: compilo il grafo…")
    node_idx = {}
    nlat, nlon = array.array("d"), array.array("d")
    ways = {}
    for key in tiles:
        with gzip.open(os.path.join(osm_dir, "%d_%d.json.gz" % key), "rt", encoding="utf-8") as f:
            elements = json.load(f)
        for el in elements:
            if el["type"] == "node":
                if el["id"] not in node_idx:
                    node_idx[el["id"]] = len(nlat)
                    nlat.append(el["lat"])
                    nlon.append(el["lon"])
            elif el["id"] not in ways:
                ways[el["id"]] = el["nodes"]
        del elements

    # incroci: nodi usati da più vie o estremi di una via
    use = bytearray(len(nlat))
    way_nodes = []
    for refs in ways.values():
        idx = [node_idx[r] for r in refs if r in node_idx]
        if len(idx) < 2:
            continue
        way_nodes.append(array.array("i", idx))
        for k, n in enumerate(idx):
            inc = 2 if k == 0 or k == len(idx) - 1 else 1
            use[n] = min(255, use[n] + inc)
    del ways, node_idx

    net = WalkNet()
    junc = array.array("i", [-1]) * len(nlat)
    net.jlat, net.jlon = array.array("d"), array.array("d")
    for n in range(len(nlat)):
        if use[n] >= 2:
            junc[n] = len(net.jlat)
            net.jlat.append(nlat[n])
            net.jlon.append(nlon[n])
    net.e_u, net.e_v, net.e_len, net.e_g0 = (array.array("i"), array.array("i"),
                                             array.array("f"), array.array("i"))
    net.glat, net.glon, net.gcum, net.gedge = (array.array("d"), array.array("d"),
                                               array.array("f"), array.array("i"))
    for idx in way_nodes:
        start = 0
        for k in range(1, len(idx)):
            if junc[idx[k]] < 0 and k < len(idx) - 1:
                continue
            seg = idx[start:k + 1]
            e = len(net.e_u)
            net.e_u.append(junc[seg[0]])
            net.e_v.append(junc[seg[-1]])
            net.e_g0.append(len(net.glat))
            cum = 0.0
            prev = None
            for n in seg:
                if prev is not None:
                    cum += _hav(nlat[prev], nlon[prev], nlat[n], nlon[n])
                net.glat.append(nlat[n])
                net.glon.append(nlon[n])
                net.gcum.append(cum)
                net.gedge.append(e)
                prev = n
            net.e_len.append(cum)
            start = k
    net.e_g0.append(len(net.glat))   # sentinella: la geometria dell'arco e è [e_g0[e], e_g0[e+1])
    del way_nodes, nlat, nlon, junc, use

    # adiacenza compatta (CSR): per ogni incrocio la lista degli archi
    J = len(net.jlat)
    deg = array.array("i", [0]) * (J + 1)
    for e in range(len(net.e_u)):
        deg[net.e_u[e] + 1] += 1
        deg[net.e_v[e] + 1] += 1
    for j in range(J):
        deg[j + 1] += deg[j]
    net.j_off = deg
    net.j_edges = array.array("i", [0]) * deg[J]
    fill = array.array("i", deg[:J])
    for e in range(len(net.e_u)):
        for j in (net.e_u[e], net.e_v[e]):
            net.j_edges[fill[j]] = e
            fill[j] += 1

    # componenti connesse: i frammenti isolati (cortili, sentieri interni, vie tagliate dal bordo
    # dei dati) non devono "catturare" un punto, altrimenti da lì non si raggiunge nulla
    comp = array.array("i", [-1]) * J
    sizes = []
    for j0 in range(J):
        if comp[j0] >= 0:
            continue
        c = len(sizes)
        comp[j0] = c
        stack, size = [j0], 0
        while stack:
            j = stack.pop()
            size += 1
            for k in range(net.j_off[j], net.j_off[j + 1]):
                e = net.j_edges[k]
                o = net.e_v[e] if net.e_u[e] == j else net.e_u[e]
                if comp[o] < 0:
                    comp[o] = c
                    stack.append(o)
        sizes.append(size)

    # griglia per agganciare un punto al vertice di strada più vicino (solo rete principale)
    cells = {}
    for p in range(len(net.glat)):
        if sizes[comp[net.e_u[net.gedge[p]]]] < MIN_COMPONENT:
            continue
        cells.setdefault((int(net.glat[p] / CELL), int(net.glon[p] / CELL)), []).append(p)
    net.grid = {k: array.array("i", v) for k, v in cells.items()}
    progress("Rete pedonale: %d incroci, %d tratti di strada" % (J, len(net.e_u)))
    return net


# ---------------------------------------------------------------- ricerca sulla rete

def snap(net, lat, lon):
    """Aggancia un punto alla rete: (vertice, distanza in linea d'aria) o None se troppo lontano."""
    cy, cx = int(lat / CELL), int(lon / CELL)
    best, bd = None, MAX_SNAP
    for yy in (cy - 1, cy, cy + 1):
        for xx in (cx - 1, cx, cx + 1):
            for p in net.grid.get((yy, xx), ()):
                d = _hav(lat, lon, net.glat[p], net.glon[p])
                if d < bd:
                    best, bd = p, d
    return None if best is None else (best, bd)


def seeds(net, p, extra):
    """Incroci raggiungibili dal vertice p lungo il suo arco: [(incrocio, distanza)]."""
    e = net.gedge[p]
    d0 = net.gcum[p]
    return [(net.e_u[e], extra + d0), (net.e_v[e], extra + net.e_len[e] - d0)]


def dijkstra(net, start, limit):
    """start: [(incrocio, distanza)] -> (dist, pred) entro limit metri."""
    dist, pred = {}, {}
    heap = []
    for j, d in start:
        if d <= limit and d < dist.get(j, 1e18):
            dist[j] = d
            pred[j] = None
            heapq.heappush(heap, (d, j))
    j_off, j_edges, e_u, e_v, e_len = net.j_off, net.j_edges, net.e_u, net.e_v, net.e_len
    done = set()
    while heap:
        d, j = heapq.heappop(heap)
        if j in done:
            continue
        done.add(j)
        for k in range(j_off[j], j_off[j + 1]):
            e = j_edges[k]
            o = e_v[e] if e_u[e] == j else e_u[e]
            nd = d + e_len[e]
            if nd <= limit and nd < dist.get(o, 1e18):
                dist[o] = nd
                pred[o] = (j, e)
                heapq.heappush(heap, (nd, o))
    return dist, pred


def _edge_points(net, e, frm):
    """Punti dell'arco e percorso partendo dall'incrocio frm."""
    pts = [(net.glat[p], net.glon[p]) for p in range(net.e_g0[e], net.e_g0[e + 1])]
    return pts if net.e_u[e] == frm else pts[::-1]


def _partial(net, p, j):
    """Punti dal vertice p fino all'incrocio j lungo l'arco di p."""
    e = net.gedge[p]
    g0, g1 = net.e_g0[e], net.e_g0[e + 1]
    rng = range(p, g0 - 1, -1) if net.e_u[e] == j else range(p, g1)
    return [(net.glat[q], net.glon[q]) for q in rng]


class Tree:
    """Albero dei cammini minimi a piedi da un punto (partenza o destinazione)."""

    def __init__(self, net, lat, lon, limit):
        self.net, self.lat, self.lon = net, lat, lon
        self.sn = snap(net, lat, lon)
        if self.sn:
            self.dist, self.pred = dijkstra(net, seeds(net, self.sn[0], self.sn[1]), limit)
        else:
            self.dist, self.pred = {}, {}

    def to_point(self, sp):
        """Distanza (m) verso un punto agganciato sp=(vertice, scarto) e incrocio d'ingresso."""
        if not self.sn or not sp:
            return None, None
        net = self.net
        best, via = 1e18, None
        for j, d in seeds(net, sp[0], sp[1]):
            if j in self.dist and self.dist[j] + d < best:
                best, via = self.dist[j] + d, j
        if net.gedge[sp[0]] == net.gedge[self.sn[0]]:   # stessi tratto di strada: diretto
            direct = abs(net.gcum[sp[0]] - net.gcum[self.sn[0]]) + sp[1] + self.sn[1]
            if direct <= best:
                best, via = direct, "same"
        return (best, via) if via is not None else (None, None)

    def path_to(self, sp, lat, lon, via):
        """Polilinea dal punto d'origine dell'albero fino al punto agganciato sp."""
        net = self.net
        if via == "same":
            a, b = self.sn[0], sp[0]
            step = 1 if b >= a else -1
            mid = [(net.glat[q], net.glon[q]) for q in range(a, b + step, step)]
            return [(self.lat, self.lon)] + mid + [(lat, lon)]
        chain = []
        j = via
        while self.pred.get(j) is not None:
            pj, e = self.pred[j]
            chain.append((pj, e))
            j = pj
        pts = [(self.lat, self.lon)] + _partial(net, self.sn[0], j)
        for pj, e in reversed(chain):
            pts += _edge_points(net, e, pj)[1:]
        pts += _partial(net, sp[0], via)[::-1] + [(lat, lon)]
        return pts


def path_between(net, a, b):
    """(metri, polilinea) a piedi tra due punti (lat, lon); None se fuori rete."""
    air = _hav(a[0], a[1], b[0], b[1])
    tree = Tree(net, a[0], a[1], air * 1.8 + 400)
    sp = snap(net, b[0], b[1])
    dist, via = tree.to_point(sp)
    if dist is None:
        return None
    return dist, tree.path_to(sp, b[0], b[1], via)


# ---------------------------------------------------------------- fermate e cambi

class StopWalk:
    """Aggancio delle fermate alla rete e cambi a piedi tra fermate (distanze reali)."""
    pass


def stops_key(g):
    h = hashlib.sha1()
    for i in range(len(g.stop_ids)):
        h.update(("%s,%.6f,%.6f;" % (g.stop_ids[i], g.stop_lat[i], g.stop_lon[i])).encode())
    return h.hexdigest()[:16]


def build_stops(net, g, progress):
    sw = StopWalk()
    sw.key = stops_key(g)
    n = len(g.stop_ids)
    sw.snap = [snap(net, g.stop_lat[i], g.stop_lon[i]) for i in range(n)]
    sw.footpaths = []
    t0 = time.time()
    for i in range(n):
        if i % 500 == 0:
            progress("Rete pedonale: cambi tra fermate (%d%%)" % (100 * i // n))
        fp = []
        sp = sw.snap[i]
        tree = None
        for j, sec in g.footpaths[i]:      # candidati: fermate vicine in linea d'aria
            spj = sw.snap[j]
            if sp is None or spj is None:
                fp.append((j, sec))          # fuori rete: resta la stima in linea d'aria
                continue
            if tree is None:
                tree = _StopTree(net, sp)
            d = tree.dist_to(spj)
            if d is not None and d <= FOOT_RADIUS:
                fp.append((j, walk_seconds(d)))
        sw.footpaths.append(fp)
    progress("Rete pedonale: cambi calcolati in %.0f s" % (time.time() - t0))
    return sw


class _StopTree:
    def __init__(self, net, sp):
        self.net, self.sp = net, sp
        self.dist, _ = dijkstra(net, seeds(net, sp[0], sp[1]), FOOT_RADIUS)

    def dist_to(self, spj):
        net = self.net
        best = None
        for j, d in seeds(net, spj[0], spj[1]):
            if j in self.dist and (best is None or self.dist[j] + d < best):
                best = self.dist[j] + d
        if net.gedge[spj[0]] == net.gedge[self.sp[0]]:
            direct = abs(net.gcum[spj[0]] - net.gcum[self.sp[0]]) + spj[1] + self.sp[1]
            best = direct if best is None else min(best, direct)
        return best


# ---------------------------------------------------------------- orchestrazione

def _pid_alive(pid):
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_held(lock):
    """Il lucchetto conta solo se il processo che l'ha preso è vivo e lo tiene aggiornato."""
    try:
        with open(lock) as f:
            pid = int(f.read().strip() or 0)
        fresh = time.time() - os.path.getmtime(lock) < 180
    except (OSError, ValueError):
        return False
    return fresh and pid > 0 and pid != os.getpid() and _pid_alive(pid)


def ensure(g, data_dir, progress, max_age_days=60):
    """Ritorna (rete, fermate) pronti, scaricando/compilando quello che manca."""
    osm_dir = os.path.join(data_dir, "osm")
    net_path = os.path.join(data_dir, "walknet.pickle")
    net = None
    stale = os.path.exists(net_path) and time.time() - os.path.getmtime(net_path) > max_age_days * 86400
    if os.path.exists(net_path) and not stale:
        obj = gs.read_pickle(net_path)
        if obj and obj[0] == NET_VERSION:
            net = obj[1]
    if net is None:
        lock = os.path.join(data_dir, "walknet.lock")
        # un altro processo Movite sta già scaricando: aspetta il suo risultato invece di rifarlo
        while _lock_held(lock):
            progress("Rete pedonale: in preparazione da un'altra istanza di Movite…")
            time.sleep(30)
            if os.path.exists(net_path) and not os.path.exists(lock):
                return ensure(g, data_dir, progress, max_age_days)
        with open(lock, "w") as f:
            f.write(str(os.getpid()))
        keeper = threading.Event()

        def touch():                          # il lucchetto resta "fresco" finché lavoriamo
            while not keeper.wait(60):
                try:
                    os.utime(lock, None)
                except OSError:
                    pass
        threading.Thread(target=touch, daemon=True).start()
        try:
            if stale:                         # rete vecchia: riscarica i riquadri
                for name in os.listdir(osm_dir) if os.path.isdir(osm_dir) else ():
                    os.remove(os.path.join(osm_dir, name))
            tiles = download(g, osm_dir, progress)
            net = compile_net(osm_dir, tiles, progress)
            gs.write_pickle(net_path, (NET_VERSION, net), log=progress)   # se il disco è pieno resta in memoria
        finally:
            keeper.set()
            try:
                os.remove(lock)
            except OSError:
                pass

    key = stops_key(g)
    sw_path = os.path.join(data_dir, "walkstops.pickle")
    sw = None
    if os.path.exists(sw_path) and os.path.exists(net_path) and os.path.getmtime(sw_path) >= os.path.getmtime(net_path):
        obj = gs.read_pickle(sw_path)
        if obj and obj[0] == STOPS_VERSION and obj[1].key == key:
            sw = obj[1]
    if sw is None:
        sw = build_stops(net, g, progress)
        gs.write_pickle(sw_path, (STOPS_VERSION, sw), log=progress)
    return net, sw
