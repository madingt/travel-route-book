#!/usr/bin/env python3
"""Planner/worker plumbing for travel-route-book: task cards, checks, merge, conflicts.

The planner (strong model) decides; cheap workers (agents/*.md, e.g. Haiku) fill in facts
from saved raw pages; this script does everything deterministic. See references/delegation.md.

  delegate.py init      trip.json work/            # task cards + work/collect.jsonl
  delegate.py add-xhs   work/ <店名> --query Q [--query Q2] [--category 餐饮] [--taste 只喝黑咖啡]
  delegate.py add-source work/ <card-id> <url> [--name official]
  delegate.py capture-amap trip.json work/         # API mode (needs AMAP_KEY): raw without a browser
  delegate.py pending   work/                      # what to dispatch next, and to which worker
  delegate.py check     work/                      # validate worker outputs (exit 1 on problems)
  delegate.py digest    work/                      # one line per card, for the planner to read
  delegate.py merge     trip.json work/ [--write]  # fold checked results into trip.json
  delegate.py conflicts trip.json [--json]         # opening hours / closed days vs. the schedule

work/ layout:  tasks/<id>.json  raw/<id>/*  out/<id>.json  collect.jsonl  check.json
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CN_DAY = {"一": "mon", "二": "tue", "三": "wed", "四": "thu", "五": "fri", "六": "sat",
          "日": "sun", "天": "sun"}
DAY_CN = {v: "周" + k for k, v in CN_DAY.items() if k != "天"}
WORKER = {"poi": "poi-checker", "ticket": "ticket-checker", "xhs": "xhs-grader"}
NEEDS_HOURS = {"景点", "餐饮", "咖啡", "活动"}
HHMM = re.compile(r"^([0-2]?\d):([0-5]\d)$")
CLOSE_WARN_MIN = 45


# ---------- small helpers ----------

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def minutes(s):
    m = HHMM.match(str(s or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def hhmm(n):
    return f"{n // 60:02d}:{n % 60:02d}"


def squash(s):
    return re.sub(r"\s+", "", s or "")


def day_key(day):
    """Weekday key ('sat') from iso_date, weekday or date text."""
    iso = day.get("iso_date")
    if iso:
        try:
            return DAYS[dt.date.fromisoformat(iso).weekday()]
        except ValueError:
            pass
    for field in ("weekday", "date"):
        m = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", str(day.get(field, "")))
        if m:
            return CN_DAY[m.group(1)]
    return None


def cn_days_to_keys(items):
    out = set()
    for it in items or []:
        m = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", str(it))
        if m:
            out.add(CN_DAY[m.group(1)])
    return out


def no_parens(s):
    return re.sub(r"[（(][^）)]*[）)]", "", s or "").strip()


def short_addr(addr, city):
    a = no_parens(addr).replace(city or "", "")
    a = re.split(r"[区县]", a, maxsplit=1)[-1] if re.search(r"[区县]", a) else a
    return a.strip()[:14]


def search_keyword(city, name, addr):
    """'城市 店名 路名' — no brackets, no repeated words; Amap often ignores the city param."""
    name, road = no_parens(name), short_addr(addr, city)
    if road and road in name:
        road = ""
    return " ".join(x for x in (city, name, road) if x)


def paths(work):
    return {k: os.path.join(work, k) for k in ("tasks", "raw", "out")}


def load_cards(work):
    cards = {}
    for p in sorted(glob.glob(os.path.join(work, "tasks", "*.json"))):
        c = read_json(p)
        cards[c["id"]] = c
    return cards


def append_jobs(work, jobs):
    path = os.path.join(work, "collect.jsonl")
    existing = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = {line.strip() for line in f if line.strip()}
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for j in jobs:
            line = json.dumps(j, ensure_ascii=False, sort_keys=True)
            if line not in existing:
                f.write(line + "\n")
                existing.add(line)
                added += 1
    return added


# ---------- init / add ----------

def cmd_init(a):
    trip = read_json(a.trip)
    p = paths(a.work)
    for d in p.values():
        os.makedirs(d, exist_ok=True)
    city, adcode, mapname = trip.get("city", ""), trip.get("adcode", ""), trip.get("map", "amap")
    made, jobs, need_url = [], [], []
    for day in trip.get("days", []):
        wk = day_key(day)
        for i, s in enumerate(day.get("stops", [])):
            if s.get("status") == "已移出" or not s.get("name"):
                continue
            placeholder = s.get("status") == "未定位" and not s.get("address")
            stop = {"day": day.get("id"), "index": i, "name": s["name"], "type": s.get("type", ""),
                    "time": s.get("time", ""), "weekday": DAY_CN.get(wk, ""),
                    "address_hint": s.get("address", "")}
            # POI card: mainland only (abroad, the planner uses a places tool directly)
            done = (s.get("amap_id") or s.get("place_id")) and s.get("hours_week") is not None
            skip_transport = s.get("type") == "交通" and s.get("lng") is not None
            if mapname == "amap" and not placeholder and not skip_transport and (a.all or not done):
                cid = f"p-{day.get('id')}-{i + 1}"
                card = {"id": cid, "kind": "poi", "map": mapname, "city": city, "stop": stop,
                        "raw": f"{a.work.rstrip('/')}/raw/{cid}/", "out": f"{a.work.rstrip('/')}/out/{cid}.json"}
                write_json(os.path.join(p["tasks"], cid + ".json"), card)
                made.append(cid)
                kw = search_keyword(city, s["name"], s.get("address"))
                jobs.append({"job": "amap_poi", "card": cid, "keyword": kw, "adcode": str(adcode)})
            # Ticket card: stops marked booking: true
            if s.get("booking") and (a.all or not s.get("booking_rule")):
                cid = f"t-{day.get('id')}-{i + 1}"
                card = {"id": cid, "kind": "ticket", "venue": s["name"],
                        "visit": {"day": day.get("id"), "date": day.get("iso_date", ""),
                                  "weekday": DAY_CN.get(wk, ""), "time": s.get("time", "")},
                        "raw": f"{a.work.rstrip('/')}/raw/{cid}/", "out": f"{a.work.rstrip('/')}/out/{cid}.json"}
                write_json(os.path.join(p["tasks"], cid + ".json"), card)
                made.append(cid)
                need_url.append(cid)
    added = append_jobs(a.work, jobs)
    print(f"cards: {len(made)}  ({', '.join(made) or '-'})")
    print(f"collect jobs added: {added}  → {a.work}/collect.jsonl")
    if mapname != "amap":
        print("map=google: no POI cards; look places up with the places tool and fill trip.json directly.")
    for cid in need_url:
        print(f"needs official URL: {cid}  → delegate.py add-source {a.work} {cid} <url>")


def cmd_add_xhs(a):
    name = a.name
    cid = "x-" + hashlib.sha1(name.encode()).hexdigest()[:6]
    card = {"id": cid, "kind": "xhs", "candidate": name, "category": a.category,
            "taste_filter": a.taste or "", "raw": f"{a.work.rstrip('/')}/raw/{cid}/",
            "out": f"{a.work.rstrip('/')}/out/{cid}.json"}
    write_json(os.path.join(a.work, "tasks", cid + ".json"), card)
    queries = a.query or [f"{name} 避雷", f"{name} 推荐"]
    n = append_jobs(a.work, [{"job": "xhs_search", "card": cid, "query": q, "max_notes": a.max_notes}
                             for q in queries])
    print(f"{cid}  {name}  ({n} search jobs)")


def cmd_add_source(a):
    if not os.path.exists(os.path.join(a.work, "tasks", a.card + ".json")):
        sys.exit(f"no card {a.card}")
    n = append_jobs(a.work, [{"job": "page", "card": a.card, "url": a.url, "name": a.name}])
    print(f"{a.card}: {'added' if n else 'already queued'} {a.url}")


def cmd_capture_amap(a):
    from amap_lookup import search
    key = os.environ.get("AMAP_KEY")
    if not key:
        sys.exit("AMAP_KEY not set: use the page-collector worker (browser) instead.")
    trip = read_json(a.trip)
    city = trip.get("city", "")
    for cid, c in load_cards(a.work).items():
        if c["kind"] != "poi":
            continue
        dest = os.path.join(a.work, "raw", cid, "amap_api.json")
        if os.path.exists(dest):
            continue
        s = c["stop"]
        kw = search_keyword("", s["name"], s.get("address_hint"))  # city goes in the region param
        try:
            rows = search(key, city, kw)
        except Exception as e:  # keep going; the worker will report insufficient
            print(f"{cid}: {e}")
            continue
        write_json(dest, {"keyword": kw, "city": city, "results": rows})
        print(f"{cid}: {len(rows)} results")


# ---------- check ----------

def raw_texts(card, work):
    d = os.path.join(work, "raw", card["id"])
    out = {}
    for p in glob.glob(os.path.join(d, "**", "*"), recursive=True):
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                out[os.path.relpath(p, d)] = f.read()
    return out


def check_quotes(items, texts, where, probs):
    for ev in items or []:
        q, f = squash(ev.get("quote")), ev.get("file", "")
        if not q:
            probs.append(f"{where}: empty quote")
            continue
        src = texts.get(f) or texts.get(os.path.basename(f))
        pool = [src] if src is not None else list(texts.values())
        if not any(q in squash(t) for t in pool):
            probs.append(f"{where}: quote not found verbatim in {f or 'raw'}: {ev.get('quote')[:30]}…")


def check_hours_week(hw, probs):
    if hw is None:
        return
    if not isinstance(hw, dict):
        probs.append("hours_week must be an object")
        return
    for k, spans in hw.items():
        if k not in DAYS:
            probs.append(f"hours_week: bad key {k}")
            continue
        if not isinstance(spans, list):
            probs.append(f"hours_week.{k} must be a list")
            continue
        for sp in spans:
            if not (isinstance(sp, list) and len(sp) == 2):
                probs.append(f"hours_week.{k}: span must be [open, close]")
                continue
            o, c = minutes(sp[0]), minutes(sp[1])
            if o is None or c is None or not (0 <= o < c <= 30 * 60):
                probs.append(f"hours_week.{k}: bad span {sp}")


def validate_out(card, out, texts):
    probs = []
    if out.get("id") != card["id"]:
        probs.append(f"id {out.get('id')!r} != card {card['id']!r}")
    kind = card["kind"]
    if out.get("kind") != kind:
        probs.append(f"kind {out.get('kind')!r} != {kind!r}")
    if out.get("confidence") not in ("high", "medium", "low"):
        probs.append("confidence must be high|medium|low")
    if kind == "poi":
        if out.get("result") not in ("found", "not_found", "ambiguous", "insufficient"):
            probs.append("result must be found|not_found|ambiguous|insufficient")
        m = out.get("match")
        if out.get("result") == "found":
            if not isinstance(m, dict) or not m.get("name"):
                probs.append("found but match.name missing")
            else:
                check_hours_week(m.get("hours_week"), probs)
                lng, lat = m.get("lng"), m.get("lat")
                if (lng is None) != (lat is None):
                    probs.append("only one of lng/lat")
                if lng is not None and not (73 <= float(lng) <= 136 and 3 <= float(lat) <= 54):
                    probs.append(f"lng/lat {lng},{lat} outside mainland China")
                if (m.get("hours") or m.get("hours_week")) and not any(
                        e.get("field") in ("hours", "hours_week") for e in out.get("evidence", [])):
                    probs.append("hours given without hours evidence")
        elif m not in (None, {}):
            probs.append("match must be null unless result is found")
        if out.get("result") == "ambiguous" and not out.get("candidates"):
            probs.append("ambiguous but no candidates")
        check_quotes(out.get("evidence"), texts, "evidence", probs)
    elif kind == "ticket":
        if out.get("result") not in ("found", "insufficient"):
            probs.append("result must be found|insufficient")
        r = out.get("rule")
        if out.get("result") == "found":
            if not isinstance(r, dict):
                probs.append("found but rule missing")
            else:
                for f in ("release_time", "last_entry"):
                    if r.get(f) not in (None, "") and minutes(r[f]) is None:
                        probs.append(f"rule.{f} must be HH:MM")
                if r.get("advance_days") is not None and not isinstance(r["advance_days"], int):
                    probs.append("rule.advance_days must be an integer")
                filled = [k for k, v in r.items() if v not in (None, "", [])]
                cited = {e.get("field") for e in out.get("evidence", [])}
                missing = [k for k in filled if k not in cited]
                if missing:
                    probs.append(f"fields without evidence: {', '.join(missing)}")
        check_quotes(out.get("evidence"), texts, "evidence", probs)
    elif kind == "xhs":
        if out.get("result") not in ("graded", "insufficient"):
            probs.append("result must be graded|insufficient")
        for i, n in enumerate(out.get("notes", [])):
            hits = n.get("marketing_hits", [])
            if bool(n.get("is_marketing")) != (len(hits) >= 2):
                probs.append(f"notes[{i}]: is_marketing must equal (marketing_hits >= 2)")
            check_quotes(n.get("real_signals"), texts, f"notes[{i}].real_signals", probs)
        check_quotes(out.get("red_flags"), texts, "red_flags", probs)
        check_quotes(out.get("taste_filter_hits"), texts, "taste_filter_hits", probs)
        authors = {n.get("author") for n in out.get("notes", [])
                   if not n.get("is_marketing") and n.get("stance") in ("正面", "混合")}
        if out.get("independent_positive") != len(authors):
            probs.append(f"independent_positive should be {len(authors)} (distinct non-marketing authors)")
        if len(out.get("planner_note", "")) > 80:
            probs.append("planner_note longer than 80 chars")
    return probs


def run_check(work, quiet=False):
    cards = load_cards(work)
    state_path = os.path.join(work, "check.json")
    state = read_json(state_path, {})
    report = {}
    for cid, card in cards.items():
        prev = state.get(cid, {})
        outp = os.path.join(work, "out", cid + ".json")
        if not os.path.exists(outp):
            report[cid] = {"status": "missing", "fails": prev.get("fails", 0), "sha": None, "problems": []}
            continue
        with open(outp, "rb") as f:
            sha = hashlib.sha1(f.read()).hexdigest()
        try:
            out = read_json(outp)
            probs = validate_out(card, out, raw_texts(card, work))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as e:
            probs = [f"unreadable output: {e}"]
        fails = prev.get("fails", 0)
        if probs and prev.get("sha") != sha:
            fails += 1
        report[cid] = {"status": "fail" if probs else "ok", "fails": fails, "sha": sha, "problems": probs}
    write_json(state_path, report)
    if not quiet:
        for cid, r in report.items():
            if r["status"] == "ok":
                continue
            print(f"{r['status'].upper():7} {cid}" + (f"  (failed {r['fails']}x)" if r["fails"] else ""))
            for pr in r["problems"]:
                print(f"         - {pr}")
        ok = sum(r["status"] == "ok" for r in report.values())
        print(f"ok {ok}/{len(report)}")
    return report


def cmd_check(a):
    report = run_check(a.work)
    sys.exit(0 if all(r["status"] == "ok" for r in report.values()) else 1)


def cmd_pending(a):
    report = run_check(a.work, quiet=True)
    cards = load_cards(a.work)
    blocked = os.path.join(a.work, "raw", "_blocked.txt")
    todo = {}
    for cid, r in report.items():
        if r["status"] == "ok":
            continue
        has_raw = bool(raw_texts(cards[cid], a.work))
        if not has_raw:
            todo.setdefault("collect first (page-collector or capture-amap)", []).append((cid, ""))
        elif r["fails"] >= 2:
            todo.setdefault("planner: do it yourself (worker failed twice)", []).append((cid, ""))
        else:
            why = "; ".join(r["problems"][:3])
            todo.setdefault(WORKER[cards[cid]["kind"]], []).append((cid, why))
    if os.path.exists(blocked):
        print(f"! blocked pages listed in {blocked} — ask the user to sign in themselves, or skip.\n")
    if not todo:
        print("nothing pending")
        return
    for who, items in todo.items():
        print(f"[{who}]  {len(items)}")
        for cid, why in items:
            print(f"  {cid}  {cards[cid].get('raw', '')}" + (f"\n      retry because: {why}" if why else ""))


# ---------- digest ----------

def fmt_week(hw):
    if not hw:
        return "营业时间未知"
    def txt(k):
        if k not in hw:
            return "?"
        return "休" if not hw[k] else ",".join(f"{o}-{c}" for o, c in hw[k])
    groups = []  # runs of consecutive days with the same hours: 二至日 09:00-17:00
    for k in DAYS:
        if groups and groups[-1][2] == txt(k):
            groups[-1][1] = k
        else:
            groups.append([k, k, txt(k)])
    return " ".join(DAY_CN[a][1] + ("" if a == b else "至" + DAY_CN[b][1]) + " " + t
                    for a, b, t in groups if t != "?") or "营业时间未知"


def cmd_digest(a):
    report = run_check(a.work, quiet=True)
    for cid, card in load_cards(a.work).items():
        r = report.get(cid, {})
        if r.get("status") != "ok":
            print(f"{cid}  [{r.get('status')}]")
            continue
        o = read_json(os.path.join(a.work, "out", cid + ".json"))
        flags = ("  ⚑ " + "；".join(o.get("flags", []))) if o.get("flags") else ""
        if card["kind"] == "poi":
            m = o.get("match") or {}
            line = f"{cid}  {card['stop']['name']} → {o['result']}/{o['confidence']}"
            if m:
                line += f"  {m.get('address', '')}  {fmt_week(m.get('hours_week'))}"
                if m.get("closed_notice"):
                    line += f"  ⚠ {m['closed_notice']}"
            if o.get("candidates"):
                line += "  候选: " + " / ".join(c.get("name", "") for c in o["candidates"])
            print(line + flags)
        elif card["kind"] == "ticket":
            rl = o.get("rule") or {}
            print(f"{cid}  {card['venue']} → {o['result']}  {rl.get('channel') or '?'}  "
                  f"提前{rl.get('advance_days', '?')}天 {rl.get('release_time') or '?'}放号  "
                  f"闭馆{','.join(rl.get('closed_days') or []) or '?'}  ({o.get('rule_year', '?')}年规则)"
                  + (f"  待核实:{','.join(o.get('unverified', []))}" if o.get("unverified") else "") + flags)
        else:
            notes = o.get("notes", [])
            mk = sum(bool(n.get("is_marketing")) for n in notes)
            red = {}
            for f in o.get("red_flags", []):
                red[f.get("type")] = red.get(f.get("type"), 0) + 1
            print(f"{cid}  {o.get('candidate')}  独立好评{o.get('independent_positive', 0)}  "
                  f"营销{mk}/{len(notes)}  致命差评:{('，'.join(f'{k}×{v}' for k, v in red.items()) or '无')}"
                  + (f"  口味:{len(o.get('taste_filter_hits', []))}条" if o.get("taste_filter_hits") else "")
                  + f"  | {o.get('planner_note', '')}")


# ---------- merge ----------

def release_when(visit_date, rule):
    adv, rt = rule.get("advance_days"), rule.get("release_time")
    if not rule.get("required"):
        return "无需预约（按官方说明）"
    if adv is None:
        return "放号规则待核实"
    if visit_date:
        try:
            d = dt.date.fromisoformat(visit_date) - dt.timedelta(days=adv)
            return f"{d.month:02d}/{d.day:02d} {DAY_CN[DAYS[d.weekday()]]} {rt or '时间待核实'} 放号"
        except ValueError:
            pass
    return f"出发前 {adv} 天 {rt or '时间待核实'} 放号"


def cmd_merge(a):
    trip = read_json(a.trip)
    report = run_check(a.work, quiet=True)
    cards = load_cards(a.work)
    app = trip.setdefault("appendix", {})
    todo = app.setdefault("todo", [])
    tickets = trip.setdefault("tickets", [])
    log, n_poi, n_tix = [], 0, 0

    def add_todo(t):
        if t not in todo:
            todo.append(t)

    days = {d.get("id"): d for d in trip.get("days", [])}
    for cid, card in cards.items():
        if report.get(cid, {}).get("status") != "ok":
            continue
        o = read_json(os.path.join(a.work, "out", cid + ".json"))
        if card["kind"] == "poi":
            st = card["stop"]
            stop = days[st["day"]]["stops"][st["index"]]
            if stop.get("name") != st["name"]:
                log.append(f"skip {cid}: stop changed since the card was made")
                continue
            res, conf = o["result"], o["confidence"]
            if res == "found":
                m = o["match"]
                for k in ("address", "phone", "amap_id", "place_id"):
                    if m.get(k):
                        stop[k] = m[k]
                if m.get("hours"):
                    stop["hours"] = m["hours"] + (f"（{m['hours_note']}）" if m.get("hours_note") else "")
                if m.get("hours_week") is not None:
                    stop["hours_week"] = m["hours_week"]
                if m.get("lng") is not None:
                    stop["lng"], stop["lat"] = m["lng"], m["lat"]
                stop["status"] = ("地图核实" if conf != "low" and m.get("lng") is not None
                                  else "只到片区" if conf != "low" else "待核实")
                if m.get("closed_notice"):
                    add_todo(f"{stop['name']}：地图提示「{m['closed_notice']}」，决定是否换掉")
                if conf == "low":
                    add_todo(f"{stop['name']}：地图信息可信度低，人工确认")
                n_poi += 1
            elif res == "not_found":
                stop["status"] = "待核实"
                add_todo(f"{stop['name']}：地图搜不到，可能已关店，换一家或人工确认")
            elif res == "ambiguous":
                stop["status"] = "待核实"
                names = " / ".join(c.get("name", "") for c in o.get("candidates", []))
                add_todo(f"{stop['name']}：地图上有多个同名点（{names}），确认是哪一家")
            else:
                add_todo(f"{stop['name']}：原文不足，地图信息没核到")
        elif card["kind"] == "ticket":
            v = card["visit"]
            day = days.get(v["day"])
            stop = next((s for s in day["stops"] if s.get("name") == card["venue"]), None) if day else None
            if o["result"] != "found":
                add_todo(f"{card['venue']}：没找到官方预约规则")
                continue
            r = o["rule"]
            if stop is not None:
                stop["booking_rule"] = r
            what = f"{day.get('label', v['day'])} {v.get('time', '')} {card['venue']}".strip()
            if r.get("real_name"):
                what += "（实名）"
            entry = {"ref": cid, "when": release_when(v.get("date"), r),
                     "platform": r.get("channel") or "渠道待核实", "what": "预约 " + what}
            venue_key = no_parens(card["venue"])
            for i, t in enumerate(tickets):
                # same card, or a hand-written entry for the same venue (no ref yet)
                hand = t.get("what", "") + t.get("platform", "")
                if t.get("ref") == cid or (not t.get("ref") and venue_key and venue_key in hand):
                    tickets[i] = entry
                    break
            else:
                tickets.append(entry)
            if o.get("unverified"):
                add_todo(f"{card['venue']} 预约规则待核实：{', '.join(o['unverified'])}")
            src = app.setdefault("sources", [])
            for s in o.get("sources", []):
                if s.get("url") and not any(x.get("url") == s["url"] for x in src):
                    src.append({"title": f"{card['venue']} 官方预约说明", "url": s["url"],
                                "note": f"{o.get('rule_year', '?')} 年规则"})
            n_tix += 1
    # xhs results never change trip.json: the planner reads `digest` and decides.
    summary = f"合并核对结果：{n_poi} 个地点、{n_tix} 条预约规则"
    print(summary)
    for line in log:
        print("  " + line)
    if not a.write:
        print("(dry run — add --write to save; a .bak copy is kept)")
        return
    shutil.copyfile(a.trip, a.trip + ".bak")
    entry = f"{dt.date.today().isoformat()}（自动）：{summary}"
    changelog = app.setdefault("changelog", [])
    if not changelog or changelog[-1] != entry:
        changelog.append(entry)
    write_json(a.trip, trip)
    print(f"saved {a.trip}  (backup {a.trip}.bak)")


# ---------- conflicts ----------

def find_conflicts(trip):
    out = []

    def add(level, day, stop, msg):
        out.append({"level": level, "day": day.get("label") or day.get("id"),
                    "time": stop.get("time", ""), "name": stop.get("name", ""), "msg": msg})

    for day in trip.get("days", []):
        wk = day_key(day)
        stops = [s for s in day.get("stops", []) if s.get("status") != "已移出"]
        prev_t = None
        for i, s in enumerate(stops):
            t = minutes(s.get("time"))
            if t is not None and prev_t is not None and t < prev_t:
                add("WARN", day, s, "时间早于上一站，顺序可能写错")
            if t is not None:
                prev_t = t
            nxt = next((minutes(x.get("time")) for x in stops[i + 1:] if minutes(x.get("time")) is not None), None)
            hw, rule = s.get("hours_week"), s.get("booking_rule") or {}
            if wk and wk in cn_days_to_keys(rule.get("closed_days")):
                add("ERROR", day, s, f"{DAY_CN[wk]}是闭馆日")
            if t is not None and minutes(rule.get("last_entry")) is not None and t > minutes(rule["last_entry"]):
                add("ERROR", day, s, f"晚于最晚入场 {rule['last_entry']}")
            if hw is None:
                if s.get("type") in NEEDS_HOURS:
                    add("WARN", day, s, "营业时间还没结构化核对（hours_week 为空）")
                continue
            if not wk:
                add("WARN", day, s, "这一天没有星期信息（加 iso_date 或 weekday），无法对照营业时间")
                continue
            if wk not in hw:
                add("WARN", day, s, f"原文没写{DAY_CN[wk]}的营业时间")
                continue
            spans = [(minutes(o), minutes(c)) for o, c in hw[wk]]
            if not spans:
                add("ERROR", day, s, f"{DAY_CN[wk]}不营业")
                continue
            if t is None:
                continue
            cur = next(((o, c) for o, c in spans if o <= t < c), None)
            if not cur:
                shown = "、".join(f"{hhmm(o)}-{hhmm(c)}" for o, c in spans)
                add("ERROR", day, s, f"{s.get('time')} 不在营业时段（{DAY_CN[wk]} {shown}）")
                continue
            if cur[1] - t < CLOSE_WARN_MIN:
                add("WARN", day, s, f"到店离打烊只剩 {cur[1] - t} 分钟（{hhmm(cur[1])} 关）")
            elif nxt is not None and nxt > cur[1] and s.get("type") in ("景点", "活动"):
                add("WARN", day, s, f"计划待到下一站前，但 {hhmm(cur[1])} 就关门")
    return out


def cmd_conflicts(a):
    items = find_conflicts(read_json(a.trip))
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for c in items:
            print(f"{c['level']:5} {c['day']} {c['time']:>5} {c['name']}：{c['msg']}")
        errs = sum(c["level"] == "ERROR" for c in items)
        print(f"{errs} error(s), {len(items) - errs} warning(s)")
    sys.exit(1 if any(c["level"] == "ERROR" for c in items) else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("trip"); p.add_argument("work")
    p.add_argument("--all", action="store_true", help="re-check stops that already have results")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("add-xhs"); p.add_argument("work"); p.add_argument("name")
    p.add_argument("--query", action="append"); p.add_argument("--category", default="餐饮")
    p.add_argument("--taste", default=""); p.add_argument("--max-notes", type=int, default=5)
    p.set_defaults(fn=cmd_add_xhs)
    p = sub.add_parser("add-source"); p.add_argument("work"); p.add_argument("card"); p.add_argument("url")
    p.add_argument("--name", default="official"); p.set_defaults(fn=cmd_add_source)
    p = sub.add_parser("capture-amap"); p.add_argument("trip"); p.add_argument("work")
    p.set_defaults(fn=cmd_capture_amap)
    for name, fn in (("pending", cmd_pending), ("check", cmd_check), ("digest", cmd_digest)):
        p = sub.add_parser(name); p.add_argument("work"); p.set_defaults(fn=fn)
    p = sub.add_parser("merge"); p.add_argument("trip"); p.add_argument("work")
    p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("conflicts"); p.add_argument("trip"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_conflicts)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
