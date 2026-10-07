"""Shared helpers for travel-route-book scripts: load trip.json, build map links.

trip.json schema: see references/routebook-html.md.
"""
import json
import sys
from urllib.parse import quote

AMAP_MAX_POINTS = 10        # uri.amap.com/marker shows at most 10 markers
GOOGLE_MAX_WAYPOINTS = 9    # Google Maps dir URL: origin + destination + 9 waypoints


def load_trip(path):
    with open(path, encoding="utf-8") as f:
        trip = json.load(f)
    problems = validate(trip)
    if problems:
        sys.stderr.write("trip.json problems:\n  - " + "\n  - ".join(problems) + "\n")
    return trip


def validate(trip):
    out = []
    for key in ("title", "days"):
        if key not in trip:
            out.append(f"missing top-level '{key}'")
    if trip.get("map", "amap") not in ("amap", "google"):
        out.append("'map' must be 'amap' (mainland China) or 'google' (elsewhere)")
    ids = set()
    for i, d in enumerate(trip.get("days", [])):
        did = d.get("id")
        if not did:
            out.append(f"days[{i}] has no id")
        elif did in ids:
            out.append(f"duplicate day id '{did}'")
        ids.add(did)
        for j, s in enumerate(d.get("stops", [])):
            if not s.get("name"):
                out.append(f"{did}.stops[{j}] has no name")
            has_lng, has_lat = s.get("lng") is not None, s.get("lat") is not None
            if has_lng != has_lat:
                out.append(f"{did}.stops[{j}] ({s.get('name')}) has only one of lng/lat")
    return out


def located(stops):
    return [s for s in stops if s.get("lng") is not None and s.get("lat") is not None]


def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def amap_marker_urls(stops, src="routebook"):
    """One URL per <=10 located stops, in visiting order."""
    urls = []
    for part in chunks(located(stops), AMAP_MAX_POINTS):
        markers = "|".join(
            f"{s['lng']},{s['lat']},{quote(s['name'].replace('|', ' ').replace(',', ' '))}" for s in part)
        urls.append(f"https://uri.amap.com/marker?markers={markers}&src={quote(src)}&callnative=1")
    return urls


def google_dir_urls(stops):
    """Google Maps directions URLs; splits long days into overlapping legs."""
    pts = [f"{s['lat']},{s['lng']}" if s.get("lat") is not None else s["name"] for s in stops]
    if len(pts) < 2:
        return [f"https://www.google.com/maps/search/?api=1&query={quote(pts[0])}"] if pts else []
    urls, step, i = [], GOOGLE_MAX_WAYPOINTS + 1, 0
    while i < len(pts) - 1:
        leg = pts[i:i + step + 1]
        u = (f"https://www.google.com/maps/dir/?api=1&origin={quote(leg[0])}"
             f"&destination={quote(leg[-1])}")
        if len(leg) > 2:
            u += "&waypoints=" + quote("|".join(leg[1:-1]))
        urls.append(u)
        i += step
    return urls


def day_map_urls(trip, day):
    if trip.get("map", "amap") == "google":
        return google_dir_urls(day.get("stops", []))
    return amap_marker_urls(day.get("stops", []), src=trip.get("store_key", "routebook"))


def place_url(trip, stop):
    """Link for a single place (address book)."""
    if trip.get("map", "amap") == "google":
        if stop.get("place_id"):
            return ("https://www.google.com/maps/search/?api=1&query="
                    f"{quote(stop['name'])}&query_place_id={quote(stop['place_id'])}")
        q = stop.get("address") or stop["name"]
        return f"https://www.google.com/maps/search/?api=1&query={quote(q)}"
    if stop.get("amap_id"):
        return f"https://www.amap.com/ssr/search/poi_detail?id={quote(stop['amap_id'])}&source=search_result"
    kw = f"{stop['name']} {stop.get('address', '')}".strip()
    city = trip.get("adcode") or trip.get("city", "")
    return f"https://uri.amap.com/search?keyword={quote(kw)}&city={quote(str(city))}&src=routebook&callnative=1"
