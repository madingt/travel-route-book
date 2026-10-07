#!/usr/bin/env python3
"""Build the offline HTML route book from trip.json.

Usage: python3 build_routebook.py trip.json out.html

Uses ../assets/routebook-template.html (navigation script, [hidden] rule, no-JS fallback,
checklist storage are already in the template). Edit trip.json, re-run, re-publish to the
same Artifact URL. Schema: references/routebook-html.md.
"""
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from trip_lib import load_trip, day_map_urls, place_url, located  # noqa: E402

TEMPLATE = os.path.join(HERE, "..", "assets", "routebook-template.html")


def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def ext(url, text, cls="btn"):
    return f'<a class="{cls}" href="{e(url)}" target="_blank" rel="noopener">{e(text)}</a>'


def li_list(items):
    items = [i for i in (items or []) if i]
    return "<ul class='plain'>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>" if items else ""


def map_name(trip):
    return "谷歌地图" if trip.get("map") == "google" else "高德"


def section(sid, title, body):
    return f'<section id="{e(sid)}" data-p>\n<h2>{e(title)}</h2>\n{body}\n</section>'


def overview(trip):
    parts = []
    if trip.get("notice"):
        parts.append(f'<div class="card notice"><strong>最新调整</strong><br>{e(trip["notice"])}</div>')
    if trip.get("assumptions"):
        parts.append(f'<div class="card"><strong>行程假设</strong>{li_list(trip["assumptions"])}</div>')
    cards = []
    for d in trip["days"]:
        names = "、".join(s["name"] for s in d.get("stops", []) if s.get("type") != "交通")[:80]
        cards.append(
            f'<a class="card daycard" href="#{e(d["id"])}">'
            f'<div><span class="lab">{e(d.get("label", d["id"]))}</span>'
            f'<span class="muted small">{e(d.get("date", ""))}</span></div>'
            f'<div><strong>{e(d.get("title", ""))}</strong></div>'
            f'<div class="small muted">{e(names)}</div>'
            f'<div class="small">住：{e(d.get("stay", "—"))}</div></a>')
    parts.append('<div class="daycards">' + "".join(cards) + "</div>")
    if trip.get("route_name") and trip.get("map", "amap") == "amap":
        parts.append(f'<p class="small muted">手机高德：我的 → 收藏 → 路线 → 我创建的 → 「{e(trip["route_name"])}」，每天一组。</p>')
    return section("overview", "总览", "\n".join(parts))


def tickets(trip):
    if not trip.get("tickets"):
        return ""
    rows = []
    for i, t in enumerate(trip["tickets"]):
        rows.append(
            f'<li><input type="checkbox" id="tk{i}" data-key="tk{i}">'
            f'<label for="tk{i}"><strong>{e(t.get("when", ""))}</strong> · {e(t.get("platform", ""))}<br>'
            f'{e(t.get("what", ""))}</label></li>')
    body = ('<div class="card"><p class="small muted">按时间顺序，抢一项勾一项（勾选只存在本机浏览器）。</p>'
            f'<ul class="checks">{"".join(rows)}</ul></div>')
    return section("tickets", "抢票/预约日历", body)


def day_section(trip, d, prev_d, next_d):
    lis = []
    for s in d.get("stops", []):
        status = s.get("status", "")
        warn = status in ("未定位", "待核实", "已移出")
        tag = f'<span class="tag{" warn" if warn else ""}">{e(status)}</span>' if status else ""
        typ = f'<span class="tag">{e(s["type"])}</span>' if s.get("type") else ""
        bits = [f'<div class="n">{e(s["name"])}{typ}{tag}</div>']
        if s.get("note"):
            bits.append(f'<div>{e(s["note"])}</div>')
        meta = " · ".join(x for x in [s.get("address"), ("营业 " + s["hours"]) if s.get("hours") else "",
                                      s.get("transit")] if x)
        if meta:
            bits.append(f'<div class="small muted">{e(meta)}</div>')
        lis.append(f'<li><div class="t">{e(s.get("time", ""))}</div><div>{"".join(bits)}</div></li>')
    body = [f'<p class="muted">{e(d.get("date", ""))} · 住：{e(d.get("stay", "—"))}</p>']
    if d.get("summary"):
        body.append(f"<p>{e(d['summary'])}</p>")
    urls = day_map_urls(trip, d)
    if urls:
        btns = [ext(u, f"在{map_name(trip)}看今天的地点" + (f"（{i + 1}/{len(urls)}）" if len(urls) > 1 else ""))
                for i, u in enumerate(urls)]
        body.append('<div class="btnrow">' + "".join(btns) + "</div>")
    body.append(f'<div class="card"><ul class="timeline">{"".join(lis)}</ul></div>')
    if d.get("rain_plan"):
        body.append(f'<div class="card"><strong>雨天方案</strong><p>{e(d["rain_plan"])}</p></div>')
    if d.get("backups"):
        body.append(f'<div class="card"><strong>随缘备选</strong>{li_list(d["backups"])}</div>')
    if d.get("tips"):
        body.append(f'<div class="card"><strong>注意</strong>{li_list(d["tips"])}</div>')
    prev_a = (f'<a class="btn ghost" href="#{e(prev_d["id"])}">← {e(prev_d.get("label", prev_d["id"]))}</a>'
              if prev_d else '<span></span>')
    next_a = (f'<a class="btn ghost" href="#{e(next_d["id"])}">{e(next_d.get("label", next_d["id"]))} →</a>'
              if next_d else '<a class="btn ghost" href="#overview">回总览</a>')
    body.append(f'<div class="pager">{prev_a}{next_a}</div>')
    title = f'{d.get("label", d["id"])} · {d.get("title", "")}'
    return section(d["id"], title, "\n".join(body))


def map_section(trip):
    parts = []
    if trip.get("map", "amap") == "amap":
        parts.append('<p class="small muted">每条链接最多显示 10 个点，编号即游览顺序；手机上会直接打开高德 App。</p>')
    for d in trip["days"]:
        urls = day_map_urls(trip, d)
        names = "".join(f"<li>{e(s['name'])}</li>" for s in
                        (located(d.get("stops", [])) if trip.get("map", "amap") == "amap" else d.get("stops", [])))
        btns = "".join(ext(u, "打开地图" + (f"（{i + 1}）" if len(urls) > 1 else "")) for i, u in enumerate(urls))
        parts.append(f'<div class="card"><strong>{e(d.get("label", d["id"]))} · {e(d.get("title", ""))}</strong>'
                     f'<ol>{names}</ol><div class="btnrow">{btns}</div></div>')
    if trip.get("route_name") and trip.get("map", "amap") == "amap":
        parts.append(f'<p class="small">手机高德里的同名路线：我的 → 收藏 → 路线 → 我创建的 → 「{e(trip["route_name"])}」。</p>')
    return section("map", "地图", "\n".join(parts))


def address_section(trip):
    rows = []
    for d in trip["days"]:
        for s in d.get("stops", []):
            if s.get("type") == "交通" and not s.get("address"):
                continue
            info = " · ".join(x for x in [s.get("address"), ("营业 " + s["hours"]) if s.get("hours") else "",
                                          s.get("phone")] if x)
            link = ext(place_url(trip, s), map_name(trip), "") if (s.get("address") or s.get("amap_id")
                                                                   or s.get("place_id") or s.get("lng")) else ""
            rows.append(f"<tr><td>{e(d.get('label', d['id']))}</td><td>{e(s['name'])}</td>"
                        f"<td>{e(info)}</td><td>{link}</td><td>{e(s.get('status', ''))}</td></tr>")
    body = ('<div class="card tablewrap"><table><thead><tr><th>天</th><th>地点</th><th>门牌 · 营业 · 电话</th>'
            '<th>地图</th><th>状态</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    return section("addresses", "地址簿", body)


def appendix(trip):
    a = trip.get("appendix") or {}
    blocks = []
    for key, title in (("review", "反向审查"), ("dropped", "舍弃清单"), ("todo", "待核实"),
                       ("changelog", "改动记录")):
        if a.get(key):
            blocks.append(f'<div class="card"><strong>{title}</strong>{li_list(a[key])}</div>')
    if a.get("sources"):
        items = "".join(f'<li><a href="{e(s.get("url", "#"))}" target="_blank" rel="noopener">{e(s.get("title", s.get("url", "")))}</a>'
                        + (f' <span class="muted small">{e(s.get("note", ""))}</span>' if s.get("note") else "")
                        + "</li>" for s in a["sources"])
        blocks.append(f'<div class="card"><strong>信息来源</strong><ul class="plain">{items}</ul></div>')
    return section("appendix", "附录", "\n".join(blocks)) if blocks else ""


def build(trip):
    days = trip["days"]
    secs = [overview(trip), tickets(trip)]
    for i, d in enumerate(days):
        secs.append(day_section(trip, d, days[i - 1] if i else None, days[i + 1] if i + 1 < len(days) else None))
    secs += [map_section(trip), address_section(trip), appendix(trip)]
    secs = [s for s in secs if s]
    nav = []
    import re
    for s in secs:
        sid = re.search(r'id="([^"]+)"', s).group(1)
        label = {"overview": "总览", "tickets": "抢票", "map": "地图", "addresses": "地址", "appendix": "附录"}.get(sid)
        if not label:
            d = next(x for x in days if x["id"] == sid)
            label = d.get("label", sid)
        nav.append(f'<a href="#{e(sid)}">{e(label)}</a>')
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    return (tpl.replace("{{TITLE}}", e(trip["title"]))
               .replace("{{SUBTITLE}}", e(trip.get("subtitle", "")))
               .replace("{{STORE_KEY}}", e(trip.get("store_key", "routebook")))
               .replace("{{NAV}}", "\n".join(nav))
               .replace("{{SECTIONS}}", "\n\n".join(secs))
               .replace("{{FOOTER}}", "营业时间、预约规则以官方渠道为准；标「待核实」的出发前再确认。"))


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    trip = load_trip(sys.argv[1])
    out = build(trip)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(out)
    print(f"wrote {sys.argv[2]} ({len(out):,} bytes, {len(trip['days'])} days)")


if __name__ == "__main__":
    main()
