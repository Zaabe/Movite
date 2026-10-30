"""Polling dei feed GTFS-Realtime e stato live dei veicoli."""
import bisect
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from gtfsrt import parse_feed
from gtfs_static import haversine

BASE = "https://romamobilita.it/wp-content/uploads/shared/"
FEEDS = {
    "vehicles": BASE + "rome_rtgtfs_vehicle_positions_feed.pb",
    "trips": BASE + "rome_rtgtfs_trip_updates_feed.pb",
    "alerts": BASE + "rome_rtgtfs_service_alerts_feed.pb",
}
POLL_SECONDS = 10           # con le richieste condizionali un feed invariato non costa nulla
ALERTS_SECONDS = 120
STALE_SECONDS = 600

OCCUPANCY = {0: "Vuoto", 1: "Molti posti liberi", 2: "Pochi posti liberi", 3: "Solo in piedi",
             4: "Affollato", 5: "Pieno", 6: "Non accetta passeggeri"}
STATUS = {0: "In arrivo a", 1: "Fermo a", 2: "Diretto a"}


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Movite/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


class Realtime:
    def __init__(self, get_data, log=print):
        self.get_data = get_data          # funzione che ritorna il GTFSData corrente
        self.log = log
        self.lock = threading.Lock()
        self.g = None
        self.vehicles = {}               # id veicolo -> dict
        self.delays = {}                  # istanza -> array ritardi (s) per posizione
        self.rt_conns = []                # connessioni con orari realtime, per partenza
        self.rt_conns_arr = []            # le stesse, per arrivo
        self.rt_insts = set()
        self.inst_vehicle = {}            # istanza -> id veicolo
        self.alerts = []
        self.feed_ts = {"vehicles": 0, "trips": 0, "alerts": 0}
        self.last_error = None
        self.version = 0                  # cresce a ogni aggiornamento pubblicato (cache delle API)
        self._alerts_at = 0
        self._modified = {}               # feed -> Last-Modified, per le richieste condizionali
        self._feeds = {}                  # feed -> ultimo feed decodificato
        self._pool = ThreadPoolExecutor(3, thread_name_prefix="feed")

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="realtime").start()

    def _loop(self):
        while True:
            t0 = time.time()
            try:
                self.poll()
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                self.log("Errore feed realtime: %s" % e)
            time.sleep(max(1, POLL_SECONDS - (time.time() - t0)))

    # ------------------------------------------------------------------ polling

    def _get(self, name):
        """Scarica e decodifica il feed solo se è cambiato; True se ce n'è uno nuovo."""
        req = urllib.request.Request(FEEDS[name], headers={"User-Agent": "Movite/1.0"})
        if self._modified.get(name):
            req.add_header("If-Modified-Since", self._modified[name])
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                self._modified[name] = resp.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return False
            raise
        self._feeds[name] = parse_feed(raw)
        return True

    def poll(self):
        # i feed si scaricano in parallelo, anche mentre i dati statici si stanno ancora caricando
        fv = self._pool.submit(self._get, "vehicles")
        ft = self._pool.submit(self._get, "trips")
        fa = self._pool.submit(self._get, "alerts") if time.time() - self._alerts_at > ALERTS_SECONDS else None
        new_v = fv.result()
        g = self.get_data()
        while g is None:
            time.sleep(0.1)
            g = self.get_data()
        old = self.vehicles
        changed = g is not self.g         # nuovo dataset (cambio giorno): gli indici vanno ricalcolati
        if changed:
            with self.lock:
                self.g = g
                self.vehicles, self.inst_vehicle = {}, {}
                self.delays, self.rt_conns, self.rt_conns_arr, self.rt_insts = {}, [], [], set()

        # 1) posizioni dei mezzi: pubblicate subito, prima di elaborare i ritardi
        if (new_v or changed) and "vehicles" in self._feeds:
            vp = self._feeds["vehicles"]
            vehicles, inst_vehicle = self._update_vehicles(g, vp, old)
            with self.lock:
                self.vehicles = vehicles
                self.inst_vehicle = inst_vehicle
                self.feed_ts["vehicles"] = vp["timestamp"]
                self.version += 1

        # 2) orari previsti delle corse
        if (ft.result() or changed) and "trips" in self._feeds:
            tu = self._feeds["trips"]
            delays = self._build_delays(g, tu)
            rt_conns = []
            for inst, dl in delays.items():
                k = g.inst_trip[inst]
                off = g.inst_off[inst]
                stops, times = g.trip_stops[k], g.trip_times[k]
                for pos in range(len(stops) - 1):
                    rt_conns.append((times[pos] + off + dl[pos], times[pos + 1] + off + dl[pos + 1],
                                     stops[pos], stops[pos + 1], inst, pos))
            # a parità di orario conta la posizione nella corsa: con i ritardi due fermate
            # consecutive possono avere lo stesso orario previsto e non vanno invertite
            rt_conns.sort(key=lambda c: (c[0], c[5]))
            rt_conns_arr = sorted(rt_conns, key=lambda c: (c[1], c[5]))
            with self.lock:
                self.delays = delays
                self.rt_conns = rt_conns
                self.rt_conns_arr = rt_conns_arr
                self.rt_insts = set(delays)
                self.feed_ts["trips"] = tu["timestamp"]
                self.version += 1

        # 3) avvisi (più di rado)
        new_a = False
        if fa is not None:
            try:
                new_a = fa.result()
                self._alerts_at = time.time()
            except Exception as e:
                self.log("Avvisi non disponibili: %s" % e)
        if (new_a or changed) and "alerts" in self._feeds:
            alerts = self._feeds["alerts"]
            built = self._build_alerts(g, alerts)
            with self.lock:
                self.alerts = built
                self.feed_ts["alerts"] = alerts["timestamp"]

    def resolve_inst(self, g, trip):
        tid = trip.get("trip_id")
        if not tid:
            return None
        if trip.get("start_date"):
            inst = g.inst_by_key.get(tid + "|" + trip["start_date"])
            if inst is not None:
                return inst
        cands = g.inst_by_trip.get(tid)
        if not cands:
            return None
        now = time.time() - g.base_midnight
        return min(cands, key=lambda i: 0 if g.inst_start[i] - 1800 <= now <= g.inst_end[i] + 1800
                   else abs(g.inst_start[i] - now))

    def _build_delays(self, g, tu):
        delays = {}
        for ent in tu["entities"]:
            upd = ent.get("trip_update")
            if not upd or not upd.get("stop_time_update"):
                continue
            inst = self.resolve_inst(g, upd.get("trip", {}))
            if inst is None:
                continue
            k = g.inst_trip[inst]
            seqs = g.trip_seqs[k]
            sched = g.trip_times[k]
            base = g.base_midnight + g.inst_off[inst]
            known = {}
            for stu in upd["stop_time_update"]:
                ev = stu.get("arrival") or stu.get("departure")
                if not ev or "time" not in ev or "stop_sequence" not in stu:
                    continue
                pos = bisect.bisect_left(seqs, stu["stop_sequence"])
                if pos < len(seqs) and seqs[pos] == stu["stop_sequence"]:
                    known[pos] = ev["time"] - (base + sched[pos])
            if not known:
                continue
            # propaga: prima del primo noto usa il primo, dopo ogni noto usa l'ultimo
            first = min(known)
            cur = known[first]
            dl = []
            for pos in range(len(seqs)):
                if pos in known:
                    cur = known[pos]
                dl.append(cur)
            # gli orari previsti non devono mai andare all'indietro
            for pos in range(1, len(dl)):
                if sched[pos] + dl[pos] < sched[pos - 1] + dl[pos - 1]:
                    dl[pos] = sched[pos - 1] + dl[pos - 1] - sched[pos]
            delays[inst] = dl
        return delays

    def _update_vehicles(self, g, vp, old):
        now = time.time()
        vehicles, inst_vehicle = {}, {}
        for ent in vp["entities"]:
            v = ent.get("vehicle")
            if not v or "position" not in v:
                continue
            pos = v["position"]
            if "lat" not in pos or "lon" not in pos:
                continue
            desc = v.get("vehicle", {})
            vid = desc.get("id") or desc.get("label") or ent.get("id")
            ts = v.get("timestamp") or vp["timestamp"]
            if now - ts > STALE_SECONDS:
                continue
            trip = v.get("trip", {})
            inst = self.resolve_inst(g, trip)
            route = g.route_index.get(trip.get("route_id"))
            if route is None and inst is not None:
                route = g.trip_route[g.inst_trip[inst]]
            lat, lon = pos["lat"], pos["lon"]
            prev = old.get(vid)
            history = prev["history"] if prev else deque(maxlen=40)
            speed_hist = prev["speed_history"] if prev else deque(maxlen=40)

            speed = None
            speed_src = None
            if pos.get("speed") is not None:
                speed, speed_src = pos["speed"] * 3.6, "gps"
            elif prev and ts > prev["ts"]:
                dt = ts - prev["ts"]
                if pos.get("odometer") is not None and prev.get("odometer") is not None:
                    dist = pos["odometer"] - prev["odometer"]
                    if dist < 0:
                        dist = haversine(prev["lat"], prev["lon"], lat, lon)
                else:
                    dist = haversine(prev["lat"], prev["lon"], lat, lon)
                if 0 < dt <= 300:
                    raw = min(dist / dt * 3.6, 110.0)
                    # media mobile per evitare salti tra un aggiornamento e l'altro
                    speed = raw if prev.get("speed") is None else 0.6 * raw + 0.4 * prev["speed"]
                    speed_src = "calcolata"
            elif prev:
                speed, speed_src = prev.get("speed"), prev.get("speed_src")

            bearing = pos.get("bearing")
            if bearing is None and prev:
                if haversine(prev["lat"], prev["lon"], lat, lon) > 8:
                    bearing = _bearing(prev["lat"], prev["lon"], lat, lon)
                else:
                    bearing = prev.get("bearing")

            if not history or history[-1][0] != ts:
                history.append((ts, round(lat, 6), round(lon, 6)))
                if speed is not None:
                    speed_hist.append((ts, round(speed, 1)))

            stop = g.stop_index.get(v.get("stop_id")) if v.get("stop_id") else None
            rec = {
                "id": vid,
                "label": desc.get("label") or vid,
                "plate": desc.get("license_plate"),
                "lat": lat, "lon": lon,
                "bearing": bearing,
                "speed": speed, "speed_src": speed_src,
                "odometer": pos.get("odometer"),
                "ts": ts,
                "route": route,
                "inst": inst,
                "trip_id": trip.get("trip_id"),
                "stop": stop,
                "seq": v.get("current_stop_sequence"),
                "status": v.get("current_status"),
                "occupancy": v.get("occupancy_status"),
                "history": history,
                "speed_history": speed_hist,
            }
            vehicles[vid] = rec
            if inst is not None:
                inst_vehicle[inst] = vid
        return vehicles, inst_vehicle

    def _build_alerts(self, g, feed):
        out = []
        now = time.time()
        for ent in feed["entities"]:
            a = ent.get("alert")
            if not a:
                continue
            periods = a.get("active_period") or [{}]
            if not any(p.get("start", 0) <= now + 86400 and (p.get("end") or now + 1) >= now for p in periods):
                continue
            routes, stops = set(), set()
            for e in a.get("informed_entity", []):
                if e.get("route_id") in g.route_index:
                    routes.add(g.route_index[e["route_id"]])
                if e.get("stop_id") in g.stop_index:
                    stops.add(g.stop_index[e["stop_id"]])
            out.append({
                "id": ent.get("id"),
                "header": a.get("header", ""),
                "description": a.get("description", ""),
                "url": a.get("url", ""),
                "routes": sorted(routes),
                "stops": sorted(stops)[:50],
                "start": min((p.get("start") or 0) for p in periods),
                "end": max((p.get("end") or 0) for p in periods),
                "effect": a.get("effect"),
            })
        out.sort(key=lambda x: -x["start"])
        return out

    # ------------------------------------------------------------------ query

    def delay_of(self, inst, pos):
        dl = self.delays.get(inst)
        return dl[pos] if dl else None
