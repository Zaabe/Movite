"""Calcolo percorsi: Connection Scan Algorithm "a giri" con profili e margini prudenti.

Ogni ricerca tiene, per ogni fermata, l'orario migliore raggiungibile usando al massimo
1, 2, … K mezzi: così in una sola passata si ottengono le soluzioni ottimali per numero di
cambi (più veloce / meno cambi). Una seconda ricerca con meno cammino dà il profilo
"meno a piedi"; le preferenze (metro e tram, bus con GPS) aggiungono ricerche dedicate e
pesano nel confronto finale. "Arriva entro" usa la stessa ricerca all'indietro.

Orari: quelli previsti in tempo reale dove ci sono, altrimenti quelli programmati, con
margini prudenti per salire, scendere e cambiare mezzo.
"""
import bisect
import time
from operator import itemgetter

from gtfs_static import haversine, nearby_stops
from gtfs_static import walk_seconds as air_walk_seconds
import walknet

INF = 1 << 40
K = 4                       # mezzi massimi in una soluzione (3 cambi)
WINDOW = 4 * 3600           # orizzonte di ricerca
SLACK = 1800                # oltre "più veloce + 30 min" le alternative non interessano

ACCESS_MAX = 1100           # metri a piedi (su strada) verso/da una fermata
LESS_WALK_ACCESS = 450      # profilo "meno a piedi"
LESS_WALK_FOOT = 125        # secondi massimi di un cambio a piedi nel profilo "meno a piedi"
DIRECT_WALK_MAX = 3000      # oltre questa distanza non proponiamo "solo a piedi"

# margini prudenti, in secondi
BOARD_RAIL = 120            # entrare in stazione e arrivare in banchina
ALIGHT_RAIL = 150           # dalla banchina alla strada
BOARD_LIVE = 60             # alla fermata almeno 1 min prima del passaggio previsto
BOARD_SCHED = 90            # bus senza GPS: possono passare in anticipo
ALIGHT_BUS = 60             # scendere (cambio bus-bus alla stessa fermata: 2 min in tutto)
SCHED_PAD = 60              # bus senza GPS: spesso in ritardo, più margine sulle coincidenze

RAIL_SEARCH_PENALTY = 360   # "preferisci metro e tram": nella ricerca un bus "costa" 6 min in più
RAIL_SCORE_PENALTY = 180    # … e nel confronto finale 3 min per tratto in bus
NOGPS_SCORE_PENALTY = 480   # "evita bus senza GPS": 8 min per tratto su un bus in viaggio senza GPS

RAIL_TYPES = (1, 2, 7)


class Variant:
    def __init__(self, allowed, access_max=ACCESS_MAX, foot_max=10 ** 9, no_gps=False, bus_pen=0):
        self.allowed, self.access_max, self.foot_max = allowed, access_max, foot_max
        self.no_gps, self.bus_pen = no_gps, bus_pen


class Ctx:
    """Dati condivisi da tutte le ricerche di una richiesta."""

    def __init__(self, g, rt, walker):
        self.g, self.rt, self.walker = g, rt, walker
        self.now_rel = time.time() - g.base_midnight

    def params(self, inst, var):
        """(margine per salire, margine dopo essere scesi) o None se la corsa è esclusa."""
        g = self.g
        typ = g.inst_type[inst]
        if typ in RAIL_TYPES:
            return BOARD_RAIL, ALIGHT_RAIL
        live = inst in self.rt.rt_insts
        if typ == 3 and var.no_gps and not live and g.inst_start[inst] <= self.now_rel:
            return None      # bus già in viaggio ma senza GPS
        return (BOARD_LIVE if live else BOARD_SCHED,
                ALIGHT_BUS + (0 if live else SCHED_PAD) + (var.bus_pen if typ == 3 else 0))

    def is_nogps_bus(self, inst):
        g = self.g
        return (g.inst_type[inst] == 3 and inst not in self.rt.rt_insts
                and g.inst_start[inst] <= self.now_rel)


# ------------------------------------------------------------------ cammino

class Walker:
    """Tratti a piedi sulla rete stradale; in linea d'aria se la rete non è pronta o copre poco."""

    def __init__(self, g, walk):
        self.g = g
        self.net, self.sw = walk if walk else (None, None)
        # la rete vale solo se le fermate agganciate sono quelle del GTFS di oggi
        self.network = self.sw is not None and self.sw.key == getattr(g, "stops_key", None)
        self.footpaths = self.sw.footpaths if self.network else g.footpaths

    def around(self, lat, lon, max_m):
        """Fermate raggiungibili a piedi: (lista [(fermata, secondi, metri)], albero per i percorsi)."""
        g = self.g
        out = []
        tree = walknet.Tree(self.net, lat, lon, max_m) if self.network else None
        if tree is not None and tree.sn is None:
            tree = None
        vias = {}
        for s, air in nearby_stops(g, lat, lon, max_m):
            if tree is not None and self.sw.snap[s] is not None:
                d, via = tree.to_point(self.sw.snap[s])
                if d is None or d > max_m:
                    continue
                vias[s] = via
                out.append((s, walknet.walk_seconds(d), d))
            else:
                d = air * 1.25
                if d <= max_m:
                    out.append((s, air_walk_seconds(air), d))
        return out, (tree, vias)

    def stop_path(self, handle, s, lat, lon):
        """Polilinea dal punto dell'albero alla fermata s."""
        tree, vias = handle
        if tree is not None and s in vias:
            return [list(p) for p in tree.path_to(self.sw.snap[s], lat, lon, vias[s])]
        return [[tree.lat, tree.lon] if tree else None, [lat, lon]]

    def between(self, a, b):
        """(metri, polilinea) a piedi tra due punti."""
        if self.network:
            r = walknet.path_between(self.net, a, b)
            if r:
                return r[0], [list(p) for p in r[1]]
        return haversine(a[0], a[1], b[0], b[1]) * 1.25, [list(a), list(b)]


# ------------------------------------------------------------------ connessioni

def _forward_conns(g, rt, t, allowed):
    """Connessioni per partenza crescente, con gli orari realtime dove disponibili.

    Unione a due puntatori delle connessioni programmate (senza le corse che hanno orari
    realtime) e di quelle realtime: molto più rapida di heapq.merge su ~100 mila elementi.
    """
    rt_insts = rt.rt_insts
    lo = bisect.bisect_left(g.c_dep, t)
    hi = bisect.bisect_left(g.c_dep, t + WINDOW)
    c_dep, c_arr, c_from, c_to, c_inst, c_pos = g.c_dep, g.c_arr, g.c_from, g.c_to, g.c_inst, g.c_pos
    inst_type = g.inst_type
    live = rt.rt_conns
    if allowed is not None:
        live = [c for c in live if inst_type[c[4]] in allowed]
    j = bisect.bisect_left(live, (t,))
    nj = len(live)
    nd = live[j][0] if j < nj else INF
    for i in range(lo, hi):
        dep = c_dep[i]
        while nd <= dep:
            yield live[j]
            j += 1
            nd = live[j][0] if j < nj else INF
        inst = c_inst[i]
        if inst in rt_insts or (allowed is not None and inst_type[inst] not in allowed):
            continue
        yield dep, c_arr[i], c_from[i], c_to[i], inst, c_pos[i]
    end = t + WINDOW
    while j < nj and live[j][0] < end:
        yield live[j]
        j += 1


def _backward_conns(g, rt, T, allowed):
    """Connessioni per arrivo decrescente (per "arriva entro"), stessa unione a due puntatori."""
    rt_insts = rt.rt_insts
    hi = bisect.bisect_right(g.c_arr_sorted, T)
    lo = bisect.bisect_left(g.c_arr_sorted, T - WINDOW)
    order = g.c_by_arr
    c_dep, c_arr, c_from, c_to, c_inst, c_pos = g.c_dep, g.c_arr, g.c_from, g.c_to, g.c_inst, g.c_pos
    inst_type = g.inst_type
    live = rt.rt_conns_arr
    if allowed is not None:
        live = [c for c in live if inst_type[c[4]] in allowed]
    j = bisect.bisect_right(live, T, key=itemgetter(1)) - 1
    na = live[j][1] if j >= 0 else -INF
    for k in range(hi - 1, lo - 1, -1):
        i = order[k]
        arr = c_arr[i]
        while na >= arr:
            yield live[j]
            j -= 1
            na = live[j][1] if j >= 0 else -INF
        inst = c_inst[i]
        if inst in rt_insts or (allowed is not None and inst_type[inst] not in allowed):
            continue
        yield c_dep[i], arr, c_from[i], c_to[i], inst, c_pos[i]
    start = T - WINDOW
    while j >= 0 and live[j][1] >= start:
        yield live[j]
        j -= 1


# ------------------------------------------------------------------ ricerca in avanti

def _forward(ctx, t, access, egress, var):
    g = ctx.g
    n = len(g.stop_ids)
    fp = ctx.walker.footpaths
    ready = [[INF] * n for _ in range(K + 1)]   # ready[r][s]: in strada a s avendo usato r mezzi
    best = [INF] * n
    lab = [{} for _ in range(K + 1)]
    for s, sec, m in access:
        v = t + sec
        for r in range(K + 1):
            if v < ready[r][s]:
                ready[r][s] = v
                lab[r][s] = ("O",)
        best[s] = min(best[s], v)
    eg = {s: sec for s, sec, m in egress}
    target = [INF] * (K + 1)
    tstop = [None] * (K + 1)
    trips, params = {}, {}
    foot_max = var.foot_max

    def reach(r0, s, v, label):
        if v >= ready[r0][s]:
            return False
        for r in range(r0, K + 1):
            if v < ready[r][s]:
                ready[r][s] = v
                lab[r][s] = label
            else:
                break
        if v < best[s]:
            best[s] = v
        e = eg.get(s)
        if e is not None:
            for r in range(r0, K + 1):
                if v + e < target[r]:
                    target[r] = v + e
                    tstop[r] = s
                else:
                    break
        return True

    for dep, arr, a, b, inst, pos in _forward_conns(g, ctx.rt, t, var.allowed):
        if dep >= target[1] or dep > target[K] + SLACK:
            break
        tr = trips.get(inst)
        if tr is None or tr[0] > 0:
            p = params.get(inst, 0)
            if p == 0:
                p = params[inst] = ctx.params(inst, var)
            if p is None:
                continue
            lim = dep - p[0]
            if best[a] <= lim:
                ra = next(r for r in range(K + 1) if ready[r][a] <= lim)
                if ra < K and (tr is None or ra < tr[0]):
                    tr = trips[inst] = (ra, a, pos, p[1])
        if tr is None:
            continue
        legs = tr[0] + 1
        rd = arr + tr[3]
        if reach(legs, b, rd, ("T", inst, tr[0], tr[1], tr[2], pos + 1)):
            for nb, w in fp[b]:
                if w <= foot_max:
                    reach(legs, nb, rd + w, ("F", b))

    out = []
    for r in range(1, K + 1):
        if tstop[r] is not None and target[r] < target[r - 1]:
            sol = _rebuild_forward(lab, r, tstop[r])
            if sol:
                out.append(sol)
    return out


def _rebuild_forward(lab, r, s):
    """Sequenza di parti dal punto di partenza: [("ride", inst, bpos, apos, bstop, astop) | ("xfer", a, b)]."""
    parts = []
    for _ in range(60):
        L = lab[r][s]
        if L[0] == "O":
            return {"first": s, "parts": parts[::-1]}
        if L[0] == "F":
            parts.append(("xfer", L[1], s))
            s = L[1]
            continue
        _, inst, rb, bstop, bpos, apos = L
        parts.append(("ride", inst, bpos, apos, bstop, s))
        s, r = bstop, rb
    return None   # catena di etichette anomala: si scarta solo questa soluzione


# ------------------------------------------------------------------ ricerca all'indietro

def _backward(ctx, T, access, egress, var):
    g = ctx.g
    n = len(g.stop_ids)
    fp = ctx.walker.footpaths
    NEG = -INF
    latest = [[NEG] * n for _ in range(K + 1)]   # ultimo orario in strada a s per arrivare entro T
    best = [NEG] * n
    lab = [{} for _ in range(K + 1)]
    for s, sec, m in egress:
        v = T - sec
        for r in range(K + 1):
            if v > latest[r][s]:
                latest[r][s] = v
                lab[r][s] = ("D",)
        best[s] = max(best[s], v)
    acc = {s: sec for s, sec, m in access}
    start = [NEG] * (K + 1)
    sstop = [None] * (K + 1)
    trips, params = {}, {}
    foot_max = var.foot_max

    def reach(r0, s, v, label):
        if v <= latest[r0][s]:
            return False
        for r in range(r0, K + 1):
            if v > latest[r][s]:
                latest[r][s] = v
                lab[r][s] = label
            else:
                break
        if v > best[s]:
            best[s] = v
        e = acc.get(s)
        if e is not None:
            for r in range(r0, K + 1):
                if v - e > start[r]:
                    start[r] = v - e
                    sstop[r] = s
                else:
                    break
        return True

    for dep, arr, a, b, inst, pos in _backward_conns(g, ctx.rt, T, var.allowed):
        if arr <= start[1] or arr < start[K] - SLACK:
            break
        tr = trips.get(inst)
        p = params.get(inst, 0)
        if p == 0:
            p = params[inst] = ctx.params(inst, var)
        if p is None:
            continue
        if tr is None or tr[0] > 0:
            need = arr + p[1]
            if best[b] >= need:
                rb = next(r for r in range(K + 1) if latest[r][b] >= need)
                if rb < K and (tr is None or rb < tr[0]):
                    tr = trips[inst] = (rb, b, pos + 1)
        if tr is None or pos >= tr[2]:      # non si sale dopo il punto in cui si scende
            continue
        legs = tr[0] + 1
        sd = dep - p[0]
        if sd < ctx.now_rel - 60:
            continue      # non si può partire nel passato
        if reach(legs, a, sd, ("T", inst, tr[0], pos, tr[1], tr[2])):
            for nb, w in fp[a]:
                if w <= foot_max:
                    reach(legs, nb, sd - w, ("F", a))

    out = []
    for r in range(1, K + 1):
        if sstop[r] is not None and start[r] > start[r - 1]:
            sol = _rebuild_backward(lab, r, sstop[r])
            if sol:
                out.append(sol)
    return out


def _rebuild_backward(lab, r, s):
    first = s
    parts = []
    for _ in range(60):
        L = lab[r][s]
        if L[0] == "D":
            return {"first": first, "parts": parts}
        if L[0] == "F":
            parts.append(("xfer", s, L[1]))
            s = L[1]
            continue
        _, inst, ra, bpos, astop, apos = L
        parts.append(("ride", inst, bpos, apos, s, astop))
        s, r = astop, ra
    return None   # catena di etichette anomala: si scarta solo questa soluzione


# ------------------------------------------------------------------ montaggio delle soluzioni

def _stop_obj(g, s):
    return {"id": g.stop_ids[s], "name": g.stop_names[s], "code": g.stop_codes[s],
            "lat": g.stop_lat[s], "lon": g.stop_lon[s]}


def _ride_times(g, rt, inst):
    k = g.inst_trip[inst]
    off = g.inst_off[inst]
    dl = rt.delays.get(inst)
    times = g.trip_times[k]
    base = g.base_midnight
    return [base + times[p] + off + (dl[p] if dl else 0) for p in range(len(times))], dl


def _assemble(ctx, sol, origin, dest, o_walk, d_walk):
    """Costruisce i tratti e ricalcola gli orari con i margini reali (senza le penalità di ricerca)."""
    g, rt, walker = ctx.g, ctx.rt, ctx.walker
    parts = list(sol["parts"])
    # un cambio a piedi subito prima del primo mezzo o dopo l'ultimo si fonde col cammino
    # da/verso il punto di partenza o di arrivo: un solo tratto a piedi invece di due
    while parts and parts[0][0] == "xfer":
        parts.pop(0)
    while parts and parts[-1][0] == "xfer":
        parts.pop()
    if not parts:
        return None
    first_stop, last_stop = parts[0][4], parts[-1][5]
    o_sec, o_m, o_path = _end_walk(walker, o_walk, first_stop, origin, g)
    d_sec, d_m, d_path = _end_walk(walker, d_walk, last_stop, dest, g)
    d_path = d_path[::-1]

    legs = []
    for part in parts:
        if part[0] == "xfer":
            a, b = part[1], part[2]
            meters, path = walker.between((g.stop_lat[a], g.stop_lon[a]), (g.stop_lat[b], g.stop_lon[b]))
            legs.append({"type": "walk", "from": _stop_obj(g, a), "to": _stop_obj(g, b), "distance": round(meters),
                         "seconds": walknet.walk_seconds(meters), "path": path})
            continue
        _, inst, bpos, apos, bstop, astop = part
        if apos <= bpos:
            return None
        k = g.inst_trip[inst]
        route = g.routes[g.trip_route[k]]
        times, dl = _ride_times(g, rt, inst)
        sched = [g.base_midnight + t + g.inst_off[inst] for t in g.trip_times[k]]
        rail = g.inst_type[inst] in RAIL_TYPES
        live = inst in rt.rt_insts
        stops = [{"name": g.stop_names[g.trip_stops[k][p]], "lat": g.stop_lat[g.trip_stops[k][p]],
                  "lon": g.stop_lon[g.trip_stops[k][p]], "time": times[p]} for p in range(bpos, apos + 1)]
        legs.append({
            "type": "transit", "mode": route["type"], "route": g.trip_route[k],
            "short": route["short"], "color": route["color"], "text": route["text"],
            "headsign": g.trip_head[k], "from": _stop_obj(g, bstop), "to": _stop_obj(g, astop),
            "dep": times[bpos], "arr": times[apos], "sched_dep": sched[bpos], "sched_arr": sched[apos],
            "realtime": dl is not None, "delay": dl[bpos] if dl else None,
            "nogps": ctx.is_nogps_bus(inst),
            "board_margin": BOARD_RAIL if rail else (BOARD_LIVE if live else BOARD_SCHED),
            "alight_margin": ALIGHT_RAIL if rail else ALIGHT_BUS,
            "be_at": times[bpos] - (BOARD_RAIL if rail else (BOARD_LIVE if live else BOARD_SCHED)),
            "vehicle": rt.inst_vehicle.get(inst), "inst": inst, "stops": stops,
            "shape": _shape_slice(g, g.trip_shape[k], stops),
        })

    # orari: a ritroso dal primo mezzo per la partenza, in avanti per cambi e arrivo
    transit = [lg for lg in legs if lg["type"] == "transit"]
    first = transit[0]
    o_arr = first["dep"] - first["board_margin"]
    i_first = legs.index(first)
    for lg in reversed(legs[:i_first]):        # cambi a piedi prima del primo mezzo
        lg["arr"] = o_arr
        lg["dep"] = o_arr - lg["seconds"]
        o_arr = lg["dep"]
    out = [{"type": "walk", "from": {"name": "Partenza", "lat": origin[0], "lon": origin[1]},
            "to": _stop_obj(g, first_stop), "distance": round(o_m), "dep": o_arr - o_sec, "arr": o_arr,
            "path": o_path}]
    free = None
    for lg in legs[i_first:]:
        if lg["type"] == "walk":
            lg["dep"] = free
            lg["arr"] = free + lg["seconds"]
            free = lg["arr"]
        else:
            free = lg["arr"] + lg["alight_margin"]
    out += legs
    out.append({"type": "walk", "from": _stop_obj(g, last_stop),
                "to": {"name": "Destinazione", "lat": dest[0], "lon": dest[1]},
                "distance": round(d_m), "dep": free, "arr": free + d_sec, "path": d_path})
    # tratti a piedi trascurabili (es. si parte esattamente da una fermata)
    out = [lg for lg in out if not (lg["type"] == "walk" and lg["distance"] < 15)]
    for lg in out:
        lg.pop("seconds", None)
    walk_m = sum(lg["distance"] for lg in out if lg["type"] == "walk")
    j = {
        "dep": out[0]["dep"] if out[0]["type"] == "walk" else o_arr,
        "arr": out[-1]["arr"] if out[-1]["type"] == "walk" else free,
        "transfers": len(transit) - 1,
        "walk": walk_m,
        "realtime": any(lg["realtime"] for lg in transit),
        "nogps": any(lg["nogps"] for lg in transit),
        "bus_legs": sum(1 for lg in transit if lg["mode"] == 3),
        "nogps_legs": sum(1 for lg in transit if lg["nogps"]),
        "legs": out,
    }
    j["duration"] = j["arr"] - j["dep"]
    j["sig"] = tuple((lg["inst"], lg["from"]["id"], lg["to"]["id"]) for lg in transit)
    return j


def _end_walk(walker, walk, stop, point, g):
    """(secondi, metri, polilinea punto->fermata) per il tratto a piedi iniziale o finale."""
    lst, handle = walk
    for s, sec, m in lst:
        if s == stop:
            path = walker.stop_path(handle, s, g.stop_lat[s], g.stop_lon[s])
            path[0] = [point[0], point[1]]
            return sec, m, path
    meters, path = walker.between(point, (g.stop_lat[stop], g.stop_lon[stop]))
    return walknet.walk_seconds(meters), meters, path


def _shape_slice(g, shape_id, stops):
    """Porzione del tracciato tra la fermata di salita e quella di discesa."""
    pts = g.shapes.get(shape_id)
    if not pts:
        return [[s["lat"], s["lon"]] for s in stops]

    def nearest(lat, lon, start):
        best_i, best_d = start, INF
        for i in range(start, len(pts)):
            d = (pts[i][0] - lat) ** 2 + (pts[i][1] - lon) ** 2
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    i0 = nearest(stops[0]["lat"], stops[0]["lon"], 0)
    i1 = nearest(stops[-1]["lat"], stops[-1]["lon"], i0)
    seg = pts[i0:i1 + 1]
    if len(seg) < 2 or (stops[-1]["lat"] - seg[-1][0]) ** 2 + (stops[-1]["lon"] - seg[-1][1]) ** 2 > 1e-5:
        return [[s["lat"], s["lon"]] for s in stops]
    return seg


def _walk_only(ctx, origin, dest, when, arrive):
    air = haversine(origin[0], origin[1], dest[0], dest[1])
    if air > DIRECT_WALK_MAX:
        return None
    meters, path = ctx.walker.between(origin, dest)
    if meters > DIRECT_WALK_MAX * 1.3:
        return None
    sec = walknet.walk_seconds(meters)
    dep = when - sec if arrive else when
    return {"dep": dep, "arr": dep + sec, "duration": sec, "transfers": 0, "walk": round(meters),
            "realtime": False, "walk_only": True, "bus_legs": 0, "nogps_legs": 0, "sig": ("walk",),
            "legs": [{"type": "walk", "from": {"name": "Partenza", "lat": origin[0], "lon": origin[1]},
                      "to": {"name": "Destinazione", "lat": dest[0], "lon": dest[1]},
                      "distance": round(meters), "dep": dep, "arr": dep + sec, "path": path}]}


# ------------------------------------------------------------------ richiesta

def plan(g, rt, walk, origin, dest, when, arrive=False, allowed=None, prefer_rail=False, avoid_nogps=False):
    ctx = Ctx(g, rt, Walker(g, walk))
    t = int(when - g.base_midnight)
    o_full = ctx.walker.around(origin[0], origin[1], ACCESS_MAX)
    d_full = ctx.walker.around(dest[0], dest[1], ACCESS_MAX)
    short = lambda w: ([x for x in w[0] if x[2] <= LESS_WALK_ACCESS], w[1])
    o_short, d_short = short(o_full), short(d_full)

    variants = [(Variant(allowed), o_full, d_full),
                (Variant(allowed, access_max=LESS_WALK_ACCESS, foot_max=LESS_WALK_FOOT), o_short, d_short)]
    if avoid_nogps and rt.rt_insts:
        variants.append((Variant(allowed, no_gps=True), o_full, d_full))
    if prefer_rail:
        variants.append((Variant(allowed, bus_pen=RAIL_SEARCH_PENALTY), o_full, d_full))

    sols = []
    for var, ow, dw in variants:
        if not ow[0] or not dw[0]:
            continue
        if arrive:
            sols += _backward(ctx, t, ow[0], dw[0], var)
        else:
            sols += _forward(ctx, t, ow[0], dw[0], var)

    journeys, seen = [], set()
    for sol in sols:
        j = _assemble(ctx, sol, origin, dest, o_full, d_full)
        if j and j["sig"] not in seen and (not arrive or j["arr"] <= when + 60) and (arrive or j["dep"] >= when - 60):
            seen.add(j["sig"])
            journeys.append(j)
    wo = _walk_only(ctx, origin, dest, when, arrive)
    if wo:
        journeys.append(wo)
    if not journeys:
        return []

    def penalty(j):
        return ((RAIL_SCORE_PENALTY * j["bus_legs"] if prefer_rail else 0) +
                (NOGPS_SCORE_PENALTY * j["nogps_legs"] if avoid_nogps else 0))

    # punteggio: in avanti conta arrivare presto, "arriva entro" conta partire tardi
    if arrive:
        raw = lambda j: -j["dep"] + j["duration"] / 1000
    else:
        raw = lambda j: j["arr"] - j["dep"] / 1000
    score = lambda j: raw(j) + penalty(j)
    best = min(journeys, key=score)
    fastest = min(journeys, key=raw)
    ref_score = score(best)
    picks = []
    if prefer_rail or avoid_nogps:
        picks.append((best, "Consigliato"))
    picks.append((fastest, "Più veloce"))
    near = [j for j in journeys if score(j) <= ref_score + SLACK]
    fewest = min(near, key=lambda j: (j["transfers"], score(j)))
    picks.append((fewest, "Meno cambi"))
    near_walk = [j for j in journeys if score(j) <= ref_score + 1200]
    least_walk = min(near_walk, key=lambda j: (j["walk"] // 100, score(j)))
    if least_walk["walk"] <= best["walk"] - 150 or least_walk is best:   # solo se si risparmia davvero
        picks.append((least_walk, "Meno a piedi"))

    # una partenza successiva (in avanti) per avere un'alternativa se si perde la prima
    if not arrive and not best.get("walk_only"):
        t2 = int(best["dep"] - g.base_midnight) + 60   # partire un minuto dopo: si perde quel mezzo
        later = []
        for sol in _forward(ctx, t2, o_full[0], d_full[0], variants[0][0]):
            j = _assemble(ctx, sol, origin, dest, o_full, d_full)
            if j and j["sig"] not in seen:
                later.append(j)
        if later:
            picks.append((min(later, key=score), "Partenza successiva"))
    # "arriva entro": la soluzione precedente, per chi vuole tenersi un margine
    if arrive and not best.get("walk_only"):
        t2 = int(best["arr"] - g.base_midnight) - 60
        earlier = []
        for sol in _backward(ctx, t2, o_full[0], d_full[0], variants[0][0]):
            j = _assemble(ctx, sol, origin, dest, o_full, d_full)
            if j and j["sig"] not in seen:
                earlier.append(j)
        if earlier:
            picks.append((min(earlier, key=score), "Partenza precedente"))

    result, by_sig = [], {}
    for j, tag in picks:
        if j["sig"] in by_sig:
            if tag not in by_sig[j["sig"]]["tags"]:
                by_sig[j["sig"]]["tags"].append(tag)
            continue
        j["tags"] = [tag]
        by_sig[j["sig"]] = j
        result.append(j)
    for j in result:
        j.pop("sig", None)
        j["fastest"] = "Più veloce" in j["tags"]
        j["recommended"] = j is result[0]
        for lg in j["legs"]:
            for k in ("inst", "board_margin", "alight_margin"):
                lg.pop(k, None)
    return result
