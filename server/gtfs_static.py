"""Caricamento e indicizzazione del GTFS statico di Roma Mobilità.

Struttura dati principale:
- fermate (array paralleli indicizzati da un intero)
- linee (routes) con i loro "pattern" (sequenza di fermate per direzione)
- corse canoniche (trip) con fermate/orari, e "istanze" di corsa per i giorni
  ieri/oggi/domani, con orari espressi in secondi dalla mezzanotte di oggi
- connessioni ordinate per orario di partenza (per il Connection Scan Algorithm)
- percorsi pedonali tra fermate vicine
"""
import array
import bisect
import calendar
import csv
import errno
import io
import math
import os
import pickle
import time
import unicodedata
import urllib.request
import zipfile
import zlib
from collections import Counter, defaultdict

GTFS_URL = "https://romamobilita.it/wp-content/uploads/shared/rome_static_gtfs.zip"
CACHE_VERSION = 6

WALK_SPEED = 1.2        # m/s
WALK_DETOUR = 1.25      # le strade non sono linee rette
FOOTPATH_RADIUS = 350   # metri, per i cambi a piedi tra fermate

ROUTE_TYPE_NAMES = {0: "tram", 1: "metro", 2: "treno", 3: "bus", 7: "funicolare"}
# Colori ufficiali dove noti, altrimenti un colore per tipo di mezzo
DEFAULT_COLORS = {0: "F2A900", 1: "D7263D", 2: "5B5F97", 3: "2D7FF9"}
METRO_COLORS = {"MEA": "E27439", "MEB": "0570B5", "MEB1": "0570B5", "MEC": "008456"}


# ---------------------------------------------------------------- tempo (Roma)

def _last_sunday(year, month):
    last = calendar.monthrange(year, month)[1]
    wd = calendar.weekday(year, month, last)  # lunedì=0 … domenica=6
    return last - (wd + 1) % 7


def rome_offset(ts):
    """Offset UTC di Europe/Rome (regole UE), senza dipendere da tzdata."""
    year = time.gmtime(ts).tm_year
    start = calendar.timegm((year, 3, _last_sunday(year, 3), 1, 0, 0))
    end = calendar.timegm((year, 10, _last_sunday(year, 10), 1, 0, 0))
    return 7200 if start <= ts < end else 3600


def rome_date(ts):
    """(anno, mese, giorno) locale di Roma per un timestamp."""
    g = time.gmtime(ts + rome_offset(ts))
    return g.tm_year, g.tm_mon, g.tm_mday


def service_midnight(y, m, d):
    """Epoch della 'mezzanotte di servizio' GTFS (mezzogiorno - 12h)."""
    noon = calendar.timegm((y, m, d, 12, 0, 0))
    noon -= rome_offset(noon)
    return noon - 43200


def ymd_str(y, m, d):
    return "%04d%02d%02d" % (y, m, d)


def shift_day(y, m, d, delta):
    t = calendar.timegm((y, m, d, 12, 0, 0)) + delta * 86400
    g = time.gmtime(t)
    return g.tm_year, g.tm_mon, g.tm_mday


def parse_hms(s):
    return int(s[:-6]) * 3600 + int(s[-5:-3]) * 60 + int(s[-2:])


# ---------------------------------------------------------------- geometria

def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_seconds(meters):
    return int(meters * WALK_DETOUR / WALK_SPEED)


def normalize(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# ---------------------------------------------------------------- download

def ensure_gtfs(path, max_age=12 * 3600, log=print):
    """Scarica il GTFS statico se manca o se è più vecchio di max_age."""
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        return False
    log("Scarico il GTFS statico di Roma Mobilità…")
    req = urllib.request.Request(GTFS_URL, headers={"User-Agent": "Movite/1.0"})
    if os.path.exists(path):
        req.add_header("If-Modified-Since", time.strftime(
            "%a, %d %b %Y %H:%M:%S GMT", time.gmtime(os.path.getmtime(path))))
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
        tmp = path + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        log("GTFS scaricato (%.1f MB)" % (len(data) / 1e6))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 304:
            os.utime(path, None)
            log("GTFS già aggiornato")
            return False
        elif os.path.exists(path):
            log("Download GTFS fallito (%s), uso la copia locale" % e)
            return False
        else:
            raise
    except OSError as e:
        if os.path.exists(path):
            log("Download GTFS fallito (%s), uso la copia locale" % e)
            return False
        else:
            raise


# ---------------------------------------------------------------- dataset
#
# Tre livelli, dal più lento al più veloce:
#   base        tutto ciò che non dipende dal giorno (fermate, linee, orari di tutte le corse,
#               tracciati): si legge dal CSV una volta per versione del GTFS e si salva su disco
#   giorno      le corse di ieri/oggi/domani con orari relativi a oggi: si ricava dalla base in ~1 s
#   connessioni le ~1,2 milioni di connessioni per il calcolo percorsi: si preparano in background

class GTFSData:
    pass


def _reader(z, name):
    return csv.reader(io.TextIOWrapper(z.open(name), "utf-8-sig"))


def zip_key(zip_path):
    st = os.stat(zip_path)
    return "%d-%d" % (int(st.st_mtime), st.st_size)


def build_base(zip_path, log=print):
    b = GTFSData()
    b.zip_key = zip_key(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        r = _reader(z, "agency.txt")
        h = next(r)
        agencies = {row[h.index("agency_id")]: row[h.index("agency_name")] for row in r}

        # ---- fermate
        log("Leggo le fermate…")
        r = _reader(z, "stops.txt")
        h = next(r)
        ci = {k: h.index(k) for k in ("stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon")}
        b.stop_ids, b.stop_codes, b.stop_names = [], [], []
        b.stop_lat, b.stop_lon = array.array("d"), array.array("d")
        for row in r:
            b.stop_ids.append(row[ci["stop_id"]])
            b.stop_codes.append(row[ci["stop_code"]])
            b.stop_names.append(row[ci["stop_name"]].strip())
            b.stop_lat.append(float(row[ci["stop_lat"]]))
            b.stop_lon.append(float(row[ci["stop_lon"]]))
        b.stop_index = {sid: i for i, sid in enumerate(b.stop_ids)}
        b.stop_search = [normalize(n) for n in b.stop_names]

        # ---- linee
        r = _reader(z, "routes.txt")
        h = next(r)
        b.routes = []
        for row in r:
            rec = dict(zip(h, row))
            rtype = int(rec.get("route_type") or 3)
            color = (rec.get("route_color") or "").strip()
            rid = rec["route_id"]
            if rid in METRO_COLORS:
                color = METRO_COLORS[rid]
            if not color:
                color = DEFAULT_COLORS.get(rtype, "2D7FF9")
            short = rec.get("route_short_name") or rid
            if rtype == 1 and short.startswith("ME"):
                short = "M" + short[2:]          # MEA -> MA
            b.routes.append({
                "id": rid, "short": short, "long": rec.get("route_long_name") or "", "type": rtype,
                "color": color, "text": (rec.get("route_text_color") or "FFFFFF"),
                "agency": agencies.get(rec.get("agency_id"), rec.get("agency_id", "")),
                "url": rec.get("route_url") or "",
            })
        b.route_index = {rt["id"]: i for i, rt in enumerate(b.routes)}
        b.route_search = [normalize(rt["short"] + " " + rt["long"]) for rt in b.routes]

        # ---- calendario (tutte le date: il giorno sceglie le sue)
        b.service_dates = defaultdict(set)     # data -> servizi attivi
        removed = defaultdict(set)
        r = _reader(z, "calendar_dates.txt")
        next(r)
        for row in r:
            (b.service_dates if row[2] == "1" else removed)[row[1]].add(row[0])
        b.service_removed = dict(removed)
        b.service_dates = dict(b.service_dates)
        b.calendar = []
        if "calendar.txt" in z.namelist():
            r = _reader(z, "calendar.txt")
            h = next(r)
            b.calendar = [dict(zip(h, row)) for row in r]

        # ---- corse
        log("Leggo le corse…")
        r = _reader(z, "trips.txt")
        h = next(r)
        ix = {k: h.index(k) for k in ("route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id")}
        b.trip_ids, b.trip_service, b.trip_head, b.trip_shape = [], [], [], []
        b.trip_route, b.trip_dir = array.array("i"), array.array("b")
        for row in r:
            b.trip_ids.append(row[ix["trip_id"]])
            b.trip_route.append(b.route_index.get(row[ix["route_id"]], -1))
            b.trip_service.append(row[ix["service_id"]])
            b.trip_head.append(row[ix["trip_headsign"]].strip())
            b.trip_dir.append(int(row[ix["direction_id"]] or 0))
            b.trip_shape.append(row[ix["shape_id"]])
        trip_index = {tid: k for k, tid in enumerate(b.trip_ids)}

        # ---- orari di tutte le corse, in array compatti (il file è raggruppato per corsa)
        log("Leggo gli orari (stop_times, ~5 milioni di righe)…")
        T = len(b.trip_ids)
        first = array.array("i", [-1]) * T
        count = array.array("i", [0]) * T
        st_stop, st_time, st_seq = array.array("i"), array.array("i"), array.array("i")
        r = _reader(z, "stop_times.txt")
        h = next(r)
        i_tid, i_arr, i_dep, i_stop, i_seq = (h.index(k) for k in
                                              ("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"))
        stop_index = b.stop_index
        tcache = {"": -1}
        prev_tid, k, last_seq, ordered = None, -1, -1, True
        for row in r:
            s = stop_index.get(row[i_stop])
            if s is None:
                continue
            tid = row[i_tid]
            if tid != prev_tid:
                prev_tid = tid
                k = trip_index.get(tid, -1)
                last_seq = -1
                if k >= 0:
                    if first[k] >= 0:
                        ordered = False          # corsa spezzata: si riordina dopo
                    else:
                        first[k] = len(st_stop)
            if k < 0:
                continue
            q = int(row[i_seq])
            if q <= last_seq:
                ordered = False
            last_seq = q
            hms = row[i_arr] or row[i_dep]
            t = tcache.get(hms)
            if t is None:
                t = tcache[hms] = parse_hms(hms)
            st_stop.append(s)
            st_time.append(t)
            st_seq.append(q)
            count[k] += 1
        if not ordered:
            raise ValueError("stop_times.txt non è ordinato per corsa e sequenza: formato inatteso")
        # offset per corsa (le corse senza orari hanno lunghezza 0)
        b.st_off = array.array("i", [0]) * (T + 1)
        for k in range(T):
            b.st_off[k] = first[k] if first[k] >= 0 else (b.st_off[k - 1] + count[k - 1] if k else 0)
            if first[k] < 0:
                count[k] = 0
        b.st_len = count
        # riempie gli orari mancanti (fermate non "timepoint")
        for k in range(T):
            a, n = b.st_off[k], count[k]
            for i in range(a, a + n):
                if st_time[i] < 0:
                    st_time[i] = st_time[i - 1] if i > a else next((x for x in st_time[a:a + n] if x >= 0), 0)
        b.st_stop, b.st_time, b.st_seq = st_stop, st_time, st_seq

        # ---- pattern delle linee: per ogni linea e direzione il percorso più frequente
        pattern_count, pattern_rep = Counter(), {}
        for k in range(T):
            route = b.trip_route[k]
            if route < 0 or not count[k]:
                continue
            key = (route, b.trip_dir[k], b.trip_shape[k])
            pattern_count[key] += 1
            pattern_rep.setdefault(key, k)
        best = {}
        for (route, direction, shape), n in pattern_count.items():
            kk = (route, direction)
            if kk not in best or n > pattern_count[(route, direction, best[kk])]:
                best[kk] = shape
        b.patterns = defaultdict(list)
        for (route, direction), shape in best.items():
            k = pattern_rep[(route, direction, shape)]
            a = b.st_off[k]
            b.patterns[route].append({"dir": direction, "headsign": b.trip_head[k], "shape": shape,
                                      "stops": b.st_stop[a:a + count[k]]})
        for route in b.patterns:
            b.patterns[route].sort(key=lambda p: p["dir"])
        b.patterns = dict(b.patterns)

        # ---- tracciati delle linee
        log("Leggo i tracciati delle linee…")
        needed = {p["shape"] for pats in b.patterns.values() for p in pats}
        r = _reader(z, "shapes.txt")
        h = next(r)
        i_sid, i_lat, i_lon, i_seq = (h.index(k) for k in ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"))
        pts = defaultdict(list)
        for row in r:
            if row[i_sid] in needed:
                pts[row[i_sid]].append((int(row[i_seq]), float(row[i_lat]), float(row[i_lon])))
        b.shapes = {}
        for sid, lst in pts.items():
            lst.sort()
            b.shapes[sid] = [[round(a, 6), round(c, 6)] for _, a, c in lst]

    # ---- griglia spaziale e cambi a piedi in linea d'aria (ripiego se manca la rete stradale)
    b.grid = defaultdict(list)
    for i in range(len(b.stop_ids)):
        b.grid[_cell(b.stop_lat[i], b.stop_lon[i])].append(i)
    b.grid = dict(b.grid)
    b.footpaths = []
    for i in range(len(b.stop_ids)):
        b.footpaths.append([(j, walk_seconds(dist)) for j, dist in nearby_stops(b, b.stop_lat[i], b.stop_lon[i],
                                                                                FOOTPATH_RADIUS) if j != i])
    return b


# attributi della base condivisi col dataset del giorno
SHARED = ("zip_key", "stop_ids", "stop_codes", "stop_names", "stop_lat", "stop_lon", "stop_index", "stop_search",
          "routes", "route_index", "route_search", "patterns", "shapes", "grid", "footpaths")


def build_day(b, now=None, log=print):
    """Corse di ieri/oggi/domani, con orari in secondi dalla mezzanotte di oggi."""
    now = now or time.time()
    y, m, d = rome_date(now)
    days = {-1: shift_day(y, m, d, -1), 0: (y, m, d), 1: shift_day(y, m, d, 1)}
    g = GTFSData()
    for name in SHARED:
        setattr(g, name, getattr(b, name))
    g.base_date = ymd_str(y, m, d)
    g.base_midnight = service_midnight(y, m, d)
    g.built_at = now
    g.conns_ready = False

    active = {}
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for off, (yy, mm, dd) in days.items():
        ds = ymd_str(yy, mm, dd)
        act = set(b.service_dates.get(ds, ()))
        for rec in b.calendar:
            if rec["start_date"] <= ds <= rec["end_date"] and rec[wd[calendar.weekday(yy, mm, dd)]] == "1":
                act.add(rec["service_id"])
        active[off] = act - b.service_removed.get(ds, set())

    log("Preparo le corse del giorno…")
    g.trip_ids, g.trip_route, g.trip_head, g.trip_dir, g.trip_shape = [], array.array("i"), [], array.array("b"), []
    g.trip_stops, g.trip_times, g.trip_seqs = [], [], []
    g.inst_trip, g.inst_off, g.inst_date = array.array("i"), array.array("i"), []
    g.inst_start, g.inst_end = array.array("i"), array.array("i")
    g.inst_by_key = {}            # "trip_id|yyyymmdd" -> istanza
    g.inst_by_trip = defaultdict(list)
    dates = {off: ymd_str(*v) for off, v in days.items()}
    st_off, st_len, st_time = b.st_off, b.st_len, b.st_time
    for kb, sid in enumerate(b.trip_service):
        offs = [off for off in (-1, 0, 1) if sid in active[off]]
        if not offs or b.trip_route[kb] < 0 or st_len[kb] < 2:
            continue
        a, n = st_off[kb], st_len[kb]
        start, end = st_time[a], st_time[a + n - 1]
        k = None
        tid = b.trip_ids[kb]
        for off in offs:
            shift = off * 86400
            if off == -1 and end + shift <= 0:
                continue
            if off == 1 and start + shift > 86400 + 4 * 3600:
                continue
            if k is None:
                k = len(g.trip_ids)
                g.trip_ids.append(tid)
                g.trip_route.append(b.trip_route[kb])
                g.trip_head.append(b.trip_head[kb])
                g.trip_dir.append(b.trip_dir[kb])
                g.trip_shape.append(b.trip_shape[kb])
                g.trip_stops.append(b.st_stop[a:a + n])
                g.trip_times.append(st_time[a:a + n])
                g.trip_seqs.append(b.st_seq[a:a + n])
            inst = len(g.inst_trip)
            g.inst_trip.append(k)
            g.inst_off.append(shift)
            g.inst_date.append(dates[off])
            g.inst_start.append(start + shift)
            g.inst_end.append(end + shift)
            g.inst_by_key[tid + "|" + dates[off]] = inst
            g.inst_by_trip[tid].append(inst)
    g.inst_by_trip = dict(g.inst_by_trip)
    if not g.inst_trip:
        raise ValueError("il GTFS non contiene corse per il %s" % g.base_date)

    # ---- indice fermata -> (corsa, posizione) e linee per fermata
    g.stop_visits = [array.array("i") for _ in g.stop_ids]   # coppie (k, pos) appiattite
    stop_routes = [set() for _ in g.stop_ids]
    for k, stops in enumerate(g.trip_stops):
        route = g.trip_route[k]
        for pos, s in enumerate(stops):
            sv = g.stop_visits[s]
            sv.append(k)
            sv.append(pos)
            stop_routes[s].add(route)
    for route, pats in g.patterns.items():
        for p in pats:
            for s in p["stops"]:
                stop_routes[s].add(route)
    g.stop_routes = [sorted(rs, key=lambda i: (g.routes[i]["type"] != 1, g.routes[i]["type"] != 0,
                                              _route_sort_key(g.routes[i]["short"]))) for rs in stop_routes]
    g.inst_type = array.array("b", (g.routes[g.trip_route[g.inst_trip[i]]]["type"] for i in range(len(g.inst_trip))))
    log("Dati pronti: %d fermate, %d linee, %d corse" % (len(g.stop_ids), len(g.routes), len(g.inst_trip)))
    return g


def build_connections(g):
    """Connessioni ordinate per partenza (e per arrivo) per il calcolo percorsi."""
    dep, arr, frm, to, ins, pos = (array.array("i") for _ in range(6))
    for inst in range(len(g.inst_trip)):
        k = g.inst_trip[inst]
        off = g.inst_off[inst]
        st, tm = g.trip_stops[k], g.trip_times[k]
        n = len(st) - 1
        if off:
            tm = array.array("i", [x + off for x in tm])
        dep.extend(tm[:-1])
        arr.extend(tm[1:])
        frm.extend(st[:-1])
        to.extend(st[1:])
        ins.extend(array.array("i", [inst]) * n)
        pos.extend(range(n))
    order = sorted(range(len(dep)), key=dep.__getitem__)
    g.c_dep = array.array("i", map(dep.__getitem__, order))
    g.c_arr = array.array("i", map(arr.__getitem__, order))
    g.c_from = array.array("i", map(frm.__getitem__, order))
    g.c_to = array.array("i", map(to.__getitem__, order))
    g.c_inst = array.array("i", map(ins.__getitem__, order))
    g.c_pos = array.array("i", map(pos.__getitem__, order))
    # stesse connessioni ordinate per arrivo (ricerca "arriva entro")
    g.c_by_arr = array.array("i", sorted(range(len(g.c_arr)), key=g.c_arr.__getitem__))
    g.c_arr_sorted = array.array("i", map(g.c_arr.__getitem__, g.c_by_arr))
    g.conns_ready = True


def _route_sort_key(short):
    digits = "".join(ch for ch in short if ch.isdigit())
    return (0 if short[:1].isdigit() else 1, int(digits) if digits else 0, short)


# ---------------------------------------------------------------- query spaziali

CELL = 0.005  # ~550 m in latitudine, ~410 m in longitudine a Roma


def _cell(lat, lon):
    return int(lat / CELL), int(lon / CELL)


def nearby_stops(g, lat, lon, radius):
    cy, cx = _cell(lat, lon)
    span = int(radius / 400) + 1
    out = []
    for yy in range(cy - span, cy + span + 1):
        for xx in range(cx - span, cx + span + 1):
            for i in g.grid.get((yy, xx), ()):
                dist = haversine(lat, lon, g.stop_lat[i], g.stop_lon[i])
                if dist <= radius:
                    out.append((i, dist))
    out.sort(key=lambda t: t[1])
    return out


# ---------------------------------------------------------------- cache su disco
#
# Le cache sono pickle compressi (zlib livello 1: ~5 volte più piccole, +0,1 s a caricarle).
# Scriverle è un'ottimizzazione: se il disco è pieno si avvisa e si continua dalla memoria.

def write_pickle(path, obj, log=print):
    """Salva obj compresso; False (senza errori) se non c'è spazio o il disco non è scrivibile."""
    tmp = path + ".part"
    try:
        data = zlib.compress(pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL), 1)
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        log("Cache non salvata (%s): continuo senza" % disk_error(e))
        return False


def read_pickle(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:1] == b"x":            # intestazione zlib
            data = zlib.decompress(data)
        return pickle.loads(data)
    except Exception:
        return None


def disk_error(e):
    if getattr(e, "errno", None) == errno.ENOSPC:
        return "disco pieno"
    return str(e)


def remove_files(cache_dir, prefix, keep=None):
    """Elimina le cache superate (prima di scriverne di nuove, così lo spazio si libera subito)."""
    for name in os.listdir(cache_dir):
        if name.startswith(prefix) and (name.endswith(".pickle") or name.endswith(".part")) and name != keep:
            try:
                os.remove(os.path.join(cache_dir, name))
            except OSError:
                pass


def _read_cache(path, key):
    obj = read_pickle(path)
    if obj and obj[0] == CACHE_VERSION and obj[1] == key:
        return obj[2]
    return None


def base_path(cache_dir, zip_path):
    return os.path.join(cache_dir, "base-%s.pickle" % zip_key(zip_path))


def load_base(zip_path, cache_dir, log=print):
    path = base_path(cache_dir, zip_path)
    b = _read_cache(path, zip_key(zip_path))
    if b is None:
        remove_files(cache_dir, "base-")
        b = build_base(zip_path, log=log)
        write_pickle(path, (CACHE_VERSION, b.zip_key, b), log=log)
    return b


def load(zip_path, cache_dir, log=print):
    """Dataset del giorno, ricavato dalla base (~0,5 s): non serve salvarlo a parte."""
    remove_files(cache_dir, "dataset-")    # cache del giorno delle versioni precedenti
    return build_day(load_base(zip_path, cache_dir, log), log=log)


def time_bisect(g, t):
    return bisect.bisect_left(g.c_dep, t)


if __name__ == "__main__":
    # uso: python gtfs_static.py base <zip> <cartella cache>
    # (prepara la cache "base" in un processo separato, senza rallentare il server)
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "base":
        load_base(sys.argv[2], sys.argv[3], log=lambda m: None)
