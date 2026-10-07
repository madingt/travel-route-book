# 谷歌地图（中国大陆以外）

- 有地点搜索工具（如 `places_search`）时：拿 place_id 和坐标回填 `trip.json`；有地图展示工具（如 `places_map_display_v0`）时按天出地图，紧接着再写说明文字。
- 没有这些工具时：用网页搜索 + Google Maps 网页核对门牌和营业时间，坐标可留空。
- 路书里的多点链接由 `scripts/map_links.py` 生成：`trip.json` 里 `"map": "google"` 时输出 Google Maps 路线链接（`https://www.google.com/maps/dir/?api=1&origin=…&destination=…&waypoints=…`，途经点最多 9 个，超出自动拆段）。
- 境外坐标是 WGS-84，不要和大陆的 GCJ-02 混用。
- 营业时间、闭馆日、预约规则一样要逐条对照行程时段。
