"""Decoder minimale e senza dipendenze per i feed GTFS-Realtime (protobuf).

Implementa solo i campi che servono a Movite: posizioni veicoli,
aggiornamenti corse (ritardi / orari previsti) e avvisi di servizio.
"""
import struct


def _varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, i
        shift += 7


def _fields(buf):
    """Itera (numero_campo, wire_type, valore) su un messaggio protobuf."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _varint(buf, i)
        elif wt == 1:
            val = buf[i:i + 8]
            i += 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wt == 5:
            val = buf[i:i + 4]
            i += 4
        else:
            raise ValueError("wire type non supportato: %d" % wt)
        yield field, wt, val


def _signed(v):
    """int32/int64 codificati come varint (complemento a due su 64 bit)."""
    return v - (1 << 64) if v >= (1 << 63) else v


def _f32(b):
    return struct.unpack("<f", b)[0]


def _str(b):
    return bytes(b).decode("utf-8", "replace")


def _trip(buf):
    t = {}
    for f, _, v in _fields(buf):
        if f == 1: t["trip_id"] = _str(v)
        elif f == 2: t["start_time"] = _str(v)
        elif f == 3: t["start_date"] = _str(v)
        elif f == 4: t["schedule_relationship"] = v
        elif f == 5: t["route_id"] = _str(v)
        elif f == 6: t["direction_id"] = v
    return t


def _vehicle_desc(buf):
    d = {}
    for f, _, v in _fields(buf):
        if f == 1: d["id"] = _str(v)
        elif f == 2: d["label"] = _str(v)
        elif f == 3: d["license_plate"] = _str(v)
    return d


def _position(buf):
    p = {}
    for f, _, v in _fields(buf):
        if f == 1: p["lat"] = _f32(v)
        elif f == 2: p["lon"] = _f32(v)
        elif f == 3: p["bearing"] = _f32(v)
        elif f == 4: p["odometer"] = struct.unpack("<d", v)[0]
        elif f == 5: p["speed"] = _f32(v)
    return p


def _vehicle_position(buf):
    vp = {}
    for f, _, v in _fields(buf):
        if f == 1: vp["trip"] = _trip(v)
        elif f == 2: vp["position"] = _position(v)
        elif f == 3: vp["current_stop_sequence"] = v
        elif f == 4: vp["current_status"] = v
        elif f == 5: vp["timestamp"] = v
        elif f == 6: vp["congestion_level"] = v
        elif f == 7: vp["stop_id"] = _str(v)
        elif f == 8: vp["vehicle"] = _vehicle_desc(v)
        elif f == 9: vp["occupancy_status"] = v
        elif f == 10: vp["occupancy_percentage"] = v
    return vp


def _stop_time_event(buf):
    e = {}
    for f, _, v in _fields(buf):
        if f == 1: e["delay"] = _signed(v)
        elif f == 2: e["time"] = _signed(v)
        elif f == 3: e["uncertainty"] = _signed(v)
    return e


def _stop_time_update(buf):
    s = {}
    for f, _, v in _fields(buf):
        if f == 1: s["stop_sequence"] = v
        elif f == 2: s["arrival"] = _stop_time_event(v)
        elif f == 3: s["departure"] = _stop_time_event(v)
        elif f == 4: s["stop_id"] = _str(v)
        elif f == 5: s["schedule_relationship"] = v
    return s


def _trip_update(buf):
    tu = {"stop_time_update": []}
    for f, _, v in _fields(buf):
        if f == 1: tu["trip"] = _trip(v)
        elif f == 2: tu["stop_time_update"].append(_stop_time_update(v))
        elif f == 3: tu["vehicle"] = _vehicle_desc(v)
        elif f == 4: tu["timestamp"] = v
        elif f == 5: tu["delay"] = _signed(v)
    return tu


def _translated(buf):
    out = {}
    for f, _, v in _fields(buf):
        if f == 1:
            text, lang = "", ""
            for f2, _, v2 in _fields(v):
                if f2 == 1: text = _str(v2)
                elif f2 == 2: lang = _str(v2)
            out[lang or "it"] = text
    # preferisce l'italiano, altrimenti la prima traduzione disponibile
    return out.get("it") or out.get("") or next(iter(out.values()), "")


def _alert(buf):
    a = {"active_period": [], "informed_entity": []}
    for f, _, v in _fields(buf):
        if f == 1:
            rng = {}
            for f2, _, v2 in _fields(v):
                if f2 == 1: rng["start"] = v2
                elif f2 == 2: rng["end"] = v2
            a["active_period"].append(rng)
        elif f == 5:
            ent = {}
            for f2, _, v2 in _fields(v):
                if f2 == 1: ent["agency_id"] = _str(v2)
                elif f2 == 2: ent["route_id"] = _str(v2)
                elif f2 == 3: ent["route_type"] = v2
                elif f2 == 4: ent["trip"] = _trip(v2)
                elif f2 == 5: ent["stop_id"] = _str(v2)
            a["informed_entity"].append(ent)
        elif f == 6: a["cause"] = v
        elif f == 7: a["effect"] = v
        elif f == 8: a["url"] = _translated(v)
        elif f == 10: a["header"] = _translated(v)
        elif f == 11: a["description"] = _translated(v)
    return a


def parse_feed(buf):
    """Ritorna {'timestamp': int, 'entities': [ {id, vehicle|trip_update|alert} ]}."""
    buf = memoryview(buf)
    feed = {"timestamp": 0, "entities": []}
    for f, _, v in _fields(buf):
        if f == 1:
            for f2, _, v2 in _fields(v):
                if f2 == 3:
                    feed["timestamp"] = v2
        elif f == 2:
            ent = {}
            for f2, _, v2 in _fields(v):
                if f2 == 1: ent["id"] = _str(v2)
                elif f2 == 2: ent["is_deleted"] = bool(v2)
                elif f2 == 3: ent["trip_update"] = _trip_update(v2)
                elif f2 == 4: ent["vehicle"] = _vehicle_position(v2)
                elif f2 == 5: ent["alert"] = _alert(v2)
            feed["entities"].append(ent)
    return feed
