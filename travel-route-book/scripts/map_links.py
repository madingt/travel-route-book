#!/usr/bin/env python3
"""Print per-day map links for a trip.

Usage: python3 map_links.py trip.json [--json]

map == "amap"   -> uri.amap.com marker links (<=10 points each, GCJ-02)
map == "google" -> Google Maps directions links (<=9 waypoints each)
Stops without lng/lat are skipped for Amap and listed as warnings.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trip_lib import load_trip, day_map_urls, located  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    trip = load_trip(sys.argv[1])
    out = {}
    for d in trip["days"]:
        stops = d.get("stops", [])
        out[d["id"]] = {
            "label": d.get("label", d["id"]),
            "urls": day_map_urls(trip, d),
            "unlocated": [s["name"] for s in stops if s not in located(stops)],
        }
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    for did, info in out.items():
        print(f"# {info['label']}")
        for u in info["urls"]:
            print(u)
        if info["unlocated"] and trip.get("map", "amap") == "amap":
            print("  (no coordinates, not on map:", "、".join(info["unlocated"]) + ")")
        print()


if __name__ == "__main__":
    main()
