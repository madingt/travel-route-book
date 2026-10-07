#!/usr/bin/env python3
"""Look up places with the official Amap (高德) Web Service API.

Needs a "Web服务" key from https://console.amap.com in the env var AMAP_KEY.

Usage:
  python3 amap_lookup.py <城市> <关键词> [<关键词> ...]
  python3 amap_lookup.py 苏州 "苏州博物馆" "拙政园"
  python3 amap_lookup.py --json 苏州 "平江路 咖啡"

Prints, for each keyword, the top matches: amap_id, name, address, lng/lat (GCJ-02),
phone and opening hours when Amap returns them. Copy the right match into trip.json.
Read-only. Without a key, use the web method in references/amap-web.md instead.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

API = "https://restapi.amap.com/v5/place/text"


def search(key, city, keyword, size=3):
    params = {
        "key": key, "keywords": keyword, "region": city, "city_limit": "true",
        "page_size": str(size), "show_fields": "business",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.load(r)
    if data.get("status") != "1":
        raise RuntimeError(f"Amap API error: {data.get('info')} ({data.get('infocode')})")
    out = []
    for p in data.get("pois", []):
        lng, lat = (p.get("location") or ",").split(",")
        biz = p.get("business") or {}
        out.append({
            "amap_id": p.get("id"),
            "name": p.get("name"),
            "address": f"{p.get('adname', '')}{p.get('address', '')}",
            "lng": float(lng) if lng else None,
            "lat": float(lat) if lat else None,
            "phone": biz.get("tel") or "",
            "hours": biz.get("opentime_week") or biz.get("opentime_today") or "",
        })
    return out


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if len(args) < 2:
        sys.exit(__doc__)
    key = os.environ.get("AMAP_KEY")
    if not key:
        sys.exit("AMAP_KEY is not set. Get a 'Web服务' key at console.amap.com, "
                 "or use the browser method in references/amap-web.md.")
    city, keywords = args[0], args[1:]
    results = {kw: search(key, city, kw) for kw in keywords}
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for kw, rows in results.items():
        print(f"# {kw}")
        if not rows:
            print("  (no match — the place may have closed; treat as a signal)")
        for r in rows:
            print(f"  {r['amap_id']}  {r['name']}  |  {r['address']}  |  {r['lng']},{r['lat']}"
                  + (f"  |  {r['hours']}" if r['hours'] else "")
                  + (f"  |  {r['phone']}" if r['phone'] else ""))
        print()


if __name__ == "__main__":
    main()
