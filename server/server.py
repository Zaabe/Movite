"""Movite — server HTTP: API JSON + file statici dell'interfaccia web.

Avvio:  python server/server.py   (poi apri http://localhost:8080)
"""
import bisect
import math
import gzip
import errno
import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gtfs_static as gs  # noqa: E402
import planner  # noqa: E402
import walknet  # noqa: E402
from realtime import OCCUPANCY, STATUS, Realtime  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
DATA = os.environ.get("MOVITE_DATA") or os.path.join(ROOT, "data")
ZIP = os.path.join(DATA, "rome_static_gtfs.zip")
PORT = int(os.environ.get("MOVITE_PORT") or os.environ.get("PORT") or "8080")
# 127.0.0.1 = solo questo PC; nel container si usa 0.0.0.0 per accettare connessioni dall'esterno
HOST = os.environ.get("MOVITE_HOST") or "127.0.0.1"
# tile della mappa: "stadia" richiede su un dominio pubblico una chiave o il dominio registrato su stadiamaps.com
TILES = {"provider": os.environ.get("MOVITE_TILES", "auto"), "key": os.environ.get("MOVITE_STADIA_KEY", "")}
if TILES["key"] and TILES["provider"] == "auto":
    TILES["provider"] = "stadia"
# rete pedonale da OpenStreetMap: MOVITE_WALKNET=0 la disattiva (meno memoria, cammini in linea d'aria)
WALKNET = os.environ.get("MOVITE_WALKNET", "1") != "0"
PLAN_SLOTS = threading.BoundedSemaphore(2)    # calcoli percorso contemporanei (il resto dell'app resta reattivo)
mimetypes.add_type("application/manifest+json", ".webmanifest")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class State:
    data = None
    status = "Avvio…"
    error = None
    walk = None                 # (rete pedonale, fermate agganciate) quando pronta
    walk_status = "In attesa dei dati"
    walk_busy = False


state = State()


def log(msg):
    state.status = msg
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


load_lock = threading.Lock()


def install(g):
    g.stops_key = walknet.stops_key(g)
    state.data = g
    state.error = None
    log("Pronto")
    if not g.conns_ready:
        threading.Thread(target=finish_day, args=(g,), daemon=True, name="connessioni").start()


def finish_day(g):
    """Connessioni del calcolo percorsi (~2 s), dopo i primi secondi dall'avvio."""
    time.sleep(1.5)        # prima la pagina e i primi bus, poi il lavoro pesante
    gs.build_connections(g)


def explain(e):
    """Messaggio per la schermata iniziale."""
    if getattr(e, "errno", None) == errno.ENOSPC:
        return ("disco del server pieno: Movite non riesce a salvare gli orari in %s. "
                "Libera spazio sul server (vedi README, sezione Docker); Movite riprova da solo." % DATA)
    return str(e)


def refresh():
    """(Ri)carica il dataset se è cambiato il giorno o il GTFS sul disco."""
    with load_lock:
        g = state.data
        today = gs.ymd_str(*gs.rome_date(time.time()))
        if g is not None and g.base_date == today and g.zip_key == gs.zip_key(ZIP):
            return
        try:
            new = gs.load(ZIP, DATA, log=log)
        except ValueError as e:              # il GTFS locale non copre oggi: serve quello nuovo
            log("GTFS locale non valido per oggi (%s): lo riscarico" % e)
            gs.ensure_gtfs(ZIP, max_age=0, log=log)
            new = gs.load(ZIP, DATA, log=log)
        install(new)


def update_gtfs():
    """Controlla se c'è un GTFS più recente; la parte lenta gira in un processo separato."""
    quiet = lambda m: print(time.strftime("[%H:%M:%S] ") + m, flush=True)
    if not gs.ensure_gtfs(ZIP, log=quiet):
        return
    quiet("Preparo i nuovi orari in background…")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run([sys.executable, "-I", gs.__file__, "base", ZIP, DATA], timeout=900, creationflags=flags)
    refresh()


def loader():
    """Avvio immediato con i dati già presenti; aggiornamenti e cambi di giorno in background."""
    os.makedirs(DATA, exist_ok=True)
    while True:
        try:
            if not os.path.exists(ZIP):
                gs.ensure_gtfs(ZIP, log=log)  # primissimo avvio: senza orari non si può partire
            refresh()
        except Exception as e:
            state.error = explain(e)
            log("Errore nel caricamento dati: %s" % state.error)
            if state.data is None:            # finché non si parte, riprova ogni minuto
                time.sleep(60)
                continue
        try:
            update_gtfs()
        except Exception as e:
            print("Aggiornamento GTFS non riuscito: %s" % e, flush=True)
        g = state.data
        if WALKNET and g is not None and not state.walk_busy and (state.walk is None or state.walk[1].key != g.stops_key):
            state.walk_busy = True
            threading.Thread(target=walk_loader, args=(g,), daemon=True, name="walknet").start()
        # ricontrolla ogni 5 minuti, e subito dopo la mezzanotte
        now = time.time()
        to_midnight = gs.service_midnight(*gs.shift_day(*gs.rome_date(now), 1)) - now + 5
        time.sleep(max(5, min(300, to_midnight)))


def walk_loader(g):
    """Prepara la rete pedonale (al primo avvio la scarica: può volerci qualche minuto)."""
    def progress(msg):
        state.walk_status = msg
        print(time.strftime("[%H:%M:%S] ") + msg, flush=True)
    time.sleep(5)          # la rete pedonale non serve nei primi secondi: niente concorrenza all'avvio
    try:
        state.walk = walknet.ensure(g, DATA, progress)
        state.walk_status = "Pronta"
        progress("Rete pedonale pronta: i tratti a piedi seguono le strade")
    except Exception as e:
        state.walk_status = "non ancora pronta (%s): riprovo tra qualche minuto" % e
        print(time.strftime("[%H:%M:%S] ") + state.walk_status, flush=True)
    finally:
        state.walk_busy = False


rt = Realtime(lambda: state.data, log=lambda m: print(time.strftime("[%H:%M:%S] ") + m, flush=True))


class _NoRT:
    delays, rt_conns, rt_conns_arr, rt_insts, inst_vehicle, vehicles, alerts = {}, [], [], set(), {}, {}, []


def live(g):
    """Stato realtime solo se coerente con il dataset corrente."""
    return rt if rt.g is g else _NoRT


# ------------------------------------------------------------------ risposte in cache

class Cached:
    """Risposta JSON già serializzata e compressa, con ETag per le richieste condizionali."""

    def __init__(self, obj):
        self.body = json.dumps(obj, separators=(",", ":")).encode()
        self.gz = gzip.compress(self.body, 6)
        # crc32 e non hash(): l'hash di Python cambia a ogni avvio e invaliderebbe la cache del browser
        self.etag = '"%08x-%d"' % (zlib.crc32(self.body), len(self.body))


_cache = {}


def cached(key, build):
    c = _cache.get(key)
    if c is None:
        if len(_cache) > 64:
            _cache.clear()
        c = _cache[key] = Cached(build())
    return c


# ------------------------------------------------------------------ helpers

def routes_json(g, R):
    counts = {}
    for v in R.vehicles.values():
        if v["route"] is not None:
            counts[v["route"]] = counts.get(v["route"], 0) + 1
    return [dict(rt_, i=i, live=counts.get(i, 0), has_pattern=i in g.patterns) for i, rt_ in enumerate(g.routes)]


def stops_json(g):
    types, metro = [], {}
    for s in range(len(g.stop_ids)):
        mask = 0
        for r in g.stop_routes[s]:
            mask |= 1 << g.routes[r]["type"]
            if g.routes[r]["type"] == 1:
                metro.setdefault(s, []).append(r)
        types.append(mask)
    return {"ids": g.stop_ids, "codes": g.stop_codes, "names": g.stop_names,
            "lat": list(g.stop_lat), "lon": list(g.stop_lon), "types": types, "metro": metro}


def stop_brief(g, s):
    return {"i": s, "id": g.stop_ids[s], "code": g.stop_codes[s], "name": g.stop_names[s],
            "lat": g.stop_lat[s], "lon": g.stop_lon[s], "routes": g.stop_routes[s]}


def adj_times(g, R, inst):
    k = g.inst_trip[inst]
    off = g.inst_off[inst]
    dl = R.delays.get(inst)
    times = g.trip_times[k]
    if dl:
        return [times[p] + off + dl[p] for p in range(len(times))], True
    return [t + off for t in times], False


def estimate_position(g, inst, times, t):
    """Posizione interpolata dall'orario (per corse senza GPS)."""
    k = g.inst_trip[inst]
    stops = g.trip_stops[k]
    p = bisect.bisect_right(times, t) - 1
    if p < 0 or p >= len(stops) - 1:
        return None
    a, b = stops[p], stops[p + 1]
    span = times[p + 1] - times[p]
    frac = (t - times[p]) / span if span > 0 else 0
    lat = g.stop_lat[a] + (g.stop_lat[b] - g.stop_lat[a]) * frac
    lon = g.stop_lon[a] + (g.stop_lon[b] - g.stop_lon[a]) * frac
    dist = gs.haversine(g.stop_lat[a], g.stop_lon[a], g.stop_lat[b], g.stop_lon[b])
    dy = g.stop_lat[b] - g.stop_lat[a]
    dx = (g.stop_lon[b] - g.stop_lon[a]) * math.cos(math.radians(g.stop_lat[a]))
    bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360 if (dx or dy) else None
    speed = dist / span * 3.6 if span > 0 else 0
    return lat, lon, bearing, min(speed, 90), p + 1


_est_cache = {"t": 0, "key": None, "val": []}


def estimated_vehicles(g, R, include_bus):
    key = (id(g), include_bus)
    now = time.time()
    if _est_cache["key"] == key and now - _est_cache["t"] < 4:
        return _est_cache["val"]
    t = now - g.base_midnight
    out = []
    types = {0, 1, 2, 7} | ({3} if include_bus else set())
    by_start = getattr(g, "inst_by_start", None)
    if by_start is None:   # corse ordinate per partenza: si guardano solo quelle partite da poco
        by_start = g.inst_by_start = sorted(range(len(g.inst_trip)), key=g.inst_start.__getitem__)
        g.inst_start_sorted = [g.inst_start[i] for i in by_start]
        g.max_trip_len = max(g.inst_end[i] - g.inst_start[i] for i in range(len(g.inst_trip)))
    lo = bisect.bisect_left(g.inst_start_sorted, t - 60 - g.max_trip_len)
    hi = bisect.bisect_right(g.inst_start_sorted, t + 60)
    for inst in by_start[lo:hi]:
        if g.inst_end[inst] < t - 60:
            continue
        if inst in R.inst_vehicle or g.inst_type[inst] not in types:
            continue
        times, has_rt = adj_times(g, R, inst)
        est = estimate_position(g, inst, times, t)
        if not est:
            continue
        k = g.inst_trip[inst]
        out.append({"id": "est-%d" % inst, "r": g.trip_route[k], "lat": round(est[0], 6), "lon": round(est[1], 6),
                    "b": None if est[2] is None else round(est[2]), "s": round(est[3]), "src": "orario",
                    "h": g.trip_head[k], "est": True, "ts": int(now)})
    _est_cache.update(t=now, key=key, val=out)
    return out


def vehicle_list(g, R, include_bus_est):
    out = []
    for v in list(R.vehicles.values()):
        head = g.trip_head[g.inst_trip[v["inst"]]] if v["inst"] is not None else ""
        out.append({"id": v["id"], "r": v["route"], "lat": round(v["lat"], 6), "lon": round(v["lon"], 6),
                    "b": None if v["bearing"] is None else round(v["bearing"]),
                    "s": None if v["speed"] is None else round(v["speed"]),
                    "src": v["speed_src"], "h": head, "est": False, "ts": v["ts"], "l": v["label"]})
    return out + estimated_vehicles(g, R, include_bus_est)


def trip_detail(g, R, inst, current_pos=None):
    k = g.inst_trip[inst]
    times, has_rt = adj_times(g, R, inst)
    sched = [t + g.inst_off[inst] for t in g.trip_times[k]]
    now_rel = time.time() - g.base_midnight
    if current_pos is None:
        current_pos = bisect.bisect_right(times, now_rel)
    stops = []
    for p, s in enumerate(g.trip_stops[k]):
        stops.append({"i": s, "name": g.stop_names[s], "lat": g.stop_lat[s], "lon": g.stop_lon[s],
                      "time": int(g.base_midnight + times[p]), "sched": int(g.base_midnight + sched[p]),
                      "passed": p < current_pos})
    rid = g.trip_route[k]
    return {"route": rid, "headsign": g.trip_head[k], "trip_id": g.trip_ids[k], "realtime": has_rt,
            "delay": (R.delays[inst][min(current_pos, len(times) - 1)] if has_rt else None),
            "stops": stops, "current": current_pos,
            "shape": g.shapes.get(g.trip_shape[k]) or [[s["lat"], s["lon"]] for s in stops]}


def departures(g, R, s, horizon=7200, limit=40):
    now = time.time()
    t = now - g.base_midnight
    visits = g.stop_visits[s]
    out = []
    for j in range(0, len(visits), 2):
        k, pos = visits[j], visits[j + 1]
        if pos == len(g.trip_stops[k]) - 1:
            continue  # capolinea d'arrivo: nessuna partenza
        for inst in g.inst_by_trip.get(g.trip_ids[k], ()):
            if g.inst_trip[inst] != k:
                continue
            sched = g.trip_times[k][pos] + g.inst_off[inst]
            if sched < t - 3600 or sched > t + horizon:
                continue
            dl = R.delays.get(inst)
            pred = sched + (dl[pos] if dl else 0)
            if pred < t - 30 or pred > t + horizon:
                continue
            rid = g.trip_route[k]
            out.append({"route": rid, "headsign": g.trip_head[k], "time": int(g.base_midnight + pred),
                        "sched": int(g.base_midnight + sched), "realtime": dl is not None,
                        "delay": dl[pos] if dl else None, "vehicle": R.inst_vehicle.get(inst), "inst": inst,
                        "first": pos == 0})
    out.sort(key=lambda d: d["time"])
    return out[:limit]


def route_alerts(R, route=None, stop=None):
    return [a for a in R.alerts if (route is not None and route in a["routes"]) or
            (stop is not None and stop in a["stops"])]


# ------------------------------------------------------------------ API

def api(path, q):
    g = state.data
    if path == "/api/status":
        R = live(g) if g else _NoRT
        return {"ready": g is not None, "message": state.status, "error": state.error,
                "base_date": g.base_date if g else None, "tiles": TILES,
                "counts": {"stops": len(g.stop_ids), "routes": len(g.routes), "trips": len(g.inst_trip),
                           "vehicles": len(R.vehicles)} if g else None,
                "feeds": rt.feed_ts, "rt_error": rt.last_error, "server_time": time.time(),
                "walk": {"ready": state.walk is not None, "message": state.walk_status, "disabled": not WALKNET},
                "planner_ready": bool(g and g.conns_ready)}
    if g is None:
        raise ApiError(503, state.status)
    R = live(g)

    if path == "/api/routes":
        return cached(("routes", id(g), rt.version), lambda: routes_json(g, R))

    if path == "/api/stops":
        return cached(("stops", id(g)), lambda: stops_json(g))

    if path == "/api/vehicles":
        bus_est = q.get("bus_est") == "1"
        # le posizioni stimate dall'orario cambiano col tempo: si ricalcolano al più ogni 4 s
        return cached(("veh", id(g), rt.version, bus_est, int(time.time() / 4)),
                      lambda: {"ts": rt.feed_ts["vehicles"], "vehicles": vehicle_list(g, R, bus_est)})

    if path == "/api/route":
        i = int(q["i"])
        route = g.routes[i]
        dirs = []
        for p in g.patterns.get(i, []):
            dirs.append({"dir": p["dir"], "headsign": p["headsign"],
                         "stops": [stop_brief(g, s) for s in p["stops"]],
                         "shape": g.shapes.get(p["shape"]) or
                         [[g.stop_lat[s], g.stop_lon[s]] for s in p["stops"]]})
        vehicles = [v for v in vehicle_list(g, R, True) if v["r"] == i]
        return {"route": dict(route, i=i), "directions": dirs, "vehicles": vehicles,
                "alerts": route_alerts(R, route=i)}

    if path == "/api/stop":
        s = int(q["i"]) if "i" in q else g.stop_index[q["id"]]
        deps = departures(g, R, s)
        return {"stop": stop_brief(g, s), "departures": deps, "alerts": route_alerts(R, stop=s)}

    if path == "/api/vehicle":
        vid = q["id"]
        if vid.startswith("est-"):
            inst = int(vid[4:])
            k = g.inst_trip[inst]
            times, _ = adj_times(g, R, inst)
            est = estimate_position(g, inst, times, time.time() - g.base_midnight)
            detail = trip_detail(g, R, inst, est[4] if est else None)
            detail.update(id=vid, estimated=True, lat=est[0] if est else None, lon=est[1] if est else None,
                          speed=round(est[3]) if est else None, speed_src="orario", bearing=est[2] if est else None,
                          ts=int(time.time()), history=[], speed_history=[], label=None, occupancy=None,
                          status=None, mode=g.routes[g.trip_route[k]]["type"])
            return detail
        v = R.vehicles.get(vid)
        if not v:
            raise ApiError(404, "Veicolo non più presente nel feed")
        base = {"id": vid, "estimated": False, "label": v["label"], "plate": v["plate"],
                "lat": v["lat"], "lon": v["lon"], "bearing": v["bearing"],
                "speed": None if v["speed"] is None else round(v["speed"], 1), "speed_src": v["speed_src"],
                "ts": v["ts"], "odometer": v["odometer"],
                "occupancy": OCCUPANCY.get(v["occupancy"]), "status": STATUS.get(v["status"]),
                "history": [[la, lo] for _, la, lo in v["history"]],
                "speed_history": list(v["speed_history"]),
                "route": v["route"], "mode": g.routes[v["route"]]["type"] if v["route"] is not None else 3}
        if v["inst"] is not None:
            k = g.inst_trip[v["inst"]]
            cur = None
            if v["seq"] is not None:
                seqs = g.trip_seqs[k]
                p = bisect.bisect_left(seqs, v["seq"])
                if p < len(seqs):
                    cur = p
            detail = trip_detail(g, R, v["inst"], cur)
            detail.update(base)
            return detail
        return base

    if path == "/api/plan":
        def coord(x):
            lat, lon = x.split(",")
            return float(lat), float(lon)
        origin, dest = coord(q["from"]), coord(q["to"])
        when = float(q.get("time") or time.time())
        modes = q.get("modes")
        allowed = {int(m) for m in modes.split(",") if m != ""} if modes else None
        if allowed is not None and 2 in allowed:
            allowed.add(7)
        t0 = time.time()
        # subito dopo l'avvio le connessioni si stanno ancora preparando (1-2 s): si aspetta
        while not g.conns_ready and time.time() - t0 < 20:
            time.sleep(0.05)
        if not g.conns_ready:
            raise ApiError(503, "Sto preparando il calcolo percorsi, riprova tra qualche secondo")
        if not PLAN_SLOTS.acquire(timeout=15):
            raise ApiError(503, "Troppe richieste di percorso in corso, riprova tra poco")
        try:
            res = planner.plan(g, R, state.walk, origin, dest, when, arrive=q.get("arrive") == "1", allowed=allowed,
                               prefer_rail=q.get("rail") == "1", avoid_nogps=q.get("gps") == "1")
        finally:
            PLAN_SLOTS.release()
        walk_ok = state.walk is not None and state.walk[1].key == g.stops_key
        return {"journeys": res, "elapsed": round(time.time() - t0, 2), "walk_network": walk_ok}

    if path == "/api/search":
        text = gs.normalize(q.get("q", "").strip())
        if not text:
            return {"stops": [], "routes": []}
        routes = []
        for i, rt_ in enumerate(g.routes):
            sn = rt_["short"].lower()
            if sn == text:
                routes.append((0, i))
            elif sn.startswith(text):
                routes.append((1, i))
            elif text in g.route_search[i]:
                routes.append((2, i))
        routes.sort(key=lambda x: (x[0], gs._route_sort_key(g.routes[x[1]]["short"])))
        stops, seen = [], set()
        for s, name in enumerate(g.stop_search):
            if text in name or g.stop_codes[s] == text:
                score = 0 if name.startswith(text) or g.stop_codes[s] == text else 1
                stops.append((score, len(name), s))
        stops.sort()
        out_stops = []
        for _, _, s in stops:
            key = (g.stop_names[s], round(g.stop_lat[s], 3), round(g.stop_lon[s], 3))
            if key in seen:
                continue
            seen.add(key)
            out_stops.append(stop_brief(g, s))
            if len(out_stops) >= 12:
                break
        return {"stops": out_stops, "routes": [dict(g.routes[i], i=i) for _, i in routes[:12]]}

    if path == "/api/nearby":
        lat, lon = float(q["lat"]), float(q["lon"])
        res = []
        for s, d in gs.nearby_stops(g, lat, lon, float(q.get("r", 500)))[:20]:
            b = stop_brief(g, s)
            b["dist"] = round(d)
            res.append(b)
        return res

    if path == "/api/alerts":
        return R.alerts

    if path == "/api/geocode":
        return geocode(q.get("q", ""))

    raise ApiError(404, "Endpoint sconosciuto")


_geo_cache = {}


def geocode(text):
    text = text.strip()
    if len(text) < 3:
        return []
    if text in _geo_cache:
        return _geo_cache[text]
    params = urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 6, "countrycodes": "it",
                                     "viewbox": "11.9,42.25,13.1,41.55", "bounded": 1,
                                     "accept-language": "it"})
    req = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + params,
                                 headers={"User-Agent": "Movite/1.0 (app trasporti Roma, uso personale)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []
    out = [{"name": d.get("display_name", "").split(", Roma")[0], "full": d.get("display_name"),
            "lat": float(d["lat"]), "lon": float(d["lon"])} for d in data]
    _geo_cache[text] = out
    return out


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class Handler(BaseHTTPRequestHandler):
    server_version = "Movite/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, cache="no-store", gz=None, etag=None):
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)                 # il browser ha già questa versione
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return
        if "gzip" in self.headers.get("Accept-Encoding", "") and len(body) > 1024:
            body = gz or gzip.compress(body, 1)     # livello 1: comprime bene ed è molto più rapido
            self.send_response(code)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path.startswith("/api/"):
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                res = api(url.path, q)
                if isinstance(res, Cached):
                    self._send(200, res.body, "application/json; charset=utf-8", "no-cache", res.gz, res.etag)
                    return
                self._send(200, json.dumps(res, separators=(",", ":")).encode(), "application/json; charset=utf-8")
            except ApiError as e:
                self._send(e.code, json.dumps({"error": str(e)}).encode(), "application/json")
            except (KeyError, ValueError, IndexError) as e:
                self._send(400, json.dumps({"error": "Richiesta non valida: %s" % e}).encode(), "application/json")
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
            return
        rel = url.path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            self._send(404, b"Not found", "text/plain")
            return
        st = os.stat(full)
        key = (full, st.st_mtime_ns, st.st_size)
        f = _static.get(full)
        if f is None or f[0] != key:            # file statici in memoria, già compressi
            with open(full, "rb") as fh:
                body = fh.read()
            f = _static[full] = (key, body, gzip.compress(body, 6), '"%x-%x"' % (st.st_mtime_ns, st.st_size))
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f[1], ctype, "no-cache", f[2], f[3])


_static = {}


class Server4(ThreadingHTTPServer):
    # su Windows SO_REUSEADDR permette a due server di occupare la stessa porta senza errori
    allow_reuse_address = os.name != "nt"


class Server6(Server4):
    address_family = socket.AF_INET6


def main():
    try:
        httpd = Server4((HOST, PORT), Handler)
    except OSError:
        print("La porta %d è già in uso: Movite è forse già aperto in un'altra finestra?\n"
              "Apri http://localhost:%d oppure chiudi l'altra istanza." % (PORT, PORT), flush=True)
        sys.exit(1)
    threading.Thread(target=loader, daemon=True, name="loader").start()
    rt.start()
    # su Windows "localhost" prova prima IPv6: ascoltiamo anche su ::1 per evitare 2s di attesa
    if HOST == "127.0.0.1":
        try:
            httpd6 = Server6(("::1", PORT), Handler)
            threading.Thread(target=httpd6.serve_forever, daemon=True).start()
        except OSError:
            pass
    url = "http://localhost:%d" % PORT
    where = url if HOST == "127.0.0.1" else "%s:%d (tutte le interfacce)" % (HOST, PORT) if HOST == "0.0.0.0" else "http://%s:%d" % (HOST, PORT)
    print("Movite in esecuzione su %s  (Ctrl+C per fermare)" % where, flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
