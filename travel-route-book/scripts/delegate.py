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

  delegate.py status    work/ [--json]             # every task and its state (resume point)
  delegate.py claim     work/ <id>… [--owner W]    # mark as handed to a worker (no double dispatch)
  delegate.py approve   work/ <id> --by NAME       # accept a needs_review output after reading it
  delegate.py unblock   work/ <id>…                # the user signed in themselves; collect again

work/ layout:  tasks/<id>.json  raw/<id>/*  out/<id>.json  collect.jsonl  state.json  events.jsonl
               backups/trip-<hash>.json

Task identity (schema 2). A card's id is a hash of what decides *which pages get collected*
(place name + address hint + city for a POI; venue + visit date for a ticket; candidate for
xhs). Its fingerprint is a hash of *everything that decides the answer* (the id inputs plus
official sources, taste filter, search queries, locale, schema version). Position in the
itinerary is not part of either: reordering stops keeps results, changing the place makes a
new task, and an output is only accepted if it echoes the fingerprint of the current card.
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

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
SCHEMA = 2          # bump when card inputs or the worker output contract change
LOCALE = "zh-CN"    # language the sources are read in; part of every fingerprint


# ---------- small helpers ----------

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_text(path, text):
    """Write via a temp file + rename so a crash never leaves a half-written file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_json(path, data):
    write_text(path, dump_json(data))


def norm(s):
    """Canonical text for hashing: NFKC (full-width = half-width), single spaces, trimmed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or ""))).strip()


def digest_of(obj, n):
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:n]


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


def load_cards(work, active_only=True):
    cards = {}
    for p in sorted(glob.glob(os.path.join(work, "tasks", "*.json"))):
        try:
            c = read_json(p)
            cid = c["id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            sys.stderr.write(f"warning: unreadable task card skipped: {p}\n")
            continue
        if active_only and not c.get("active", True):
            continue
        cards[cid] = c
    return cards


def label(card):
    """Human name of a task; ids are hashes, so every listing shows this next to them."""
    name = (card.get("stop") or {}).get("name") or card.get("venue") or card.get("candidate") or "?"
    return {"poi": "地点", "ticket": "预约", "xhs": "小红书"}.get(card.get("kind"), "?") + "·" + name


# ---------- task identity ----------

def poi_input(trip, stop):
    """What a POI lookup depends on. After a merge the stop's address is the map's, so the hint
    the lookup was made with is kept in stop.provenance and reused while the name is unchanged."""
    prov = stop.get("provenance") or {}
    hint = stop.get("address", "")
    if prov.get("name") == stop.get("name") and "address_hint" in prov:
        hint = prov["address_hint"]
    return {"kind": "poi", "map": trip.get("map", "amap"), "city": norm(trip.get("city")),
            "adcode": norm(trip.get("adcode")), "name": norm(stop.get("name")),
            "address_hint": norm(hint), "locale": LOCALE}


def ticket_key(trip, day, stop):
    date = norm(day.get("iso_date"))
    return {"kind": "ticket", "city": norm(trip.get("city")), "venue": norm(stop.get("name")),
            "date": date, "weekday": "" if date else (day_key(day) or "")}


def card_ids(kind, key, extra=None):
    """(id, fingerprint) for a card. `key` picks the slot; `extra` are answer-only inputs."""
    cid = {"poi": "p-", "ticket": "t-", "xhs": "x-"}[kind] + digest_of(key, 8)
    return cid, digest_of({"schema": SCHEMA, "locale": LOCALE, "key": key, "extra": extra or {}}, 16)


def refresh_fingerprint(card):
    k = card["kind"]
    extra = ({"sources": sorted(card.get("sources", []))} if k == "ticket" else
             {"category": card.get("category", ""), "taste_filter": card.get("taste_filter", ""),
              "queries": sorted(card.get("queries", []))} if k == "xhs" else None)
    card["id"], card["fingerprint"] = card_ids(k, card["input"], extra)
    return card


def save_card(work, card):
    """Write a card (and its raw/out paths) only when something changed."""
    w = work.rstrip("/")
    card.update(schema=SCHEMA, raw=f"{w}/raw/{card['id']}/", out=f"{w}/out/{card['id']}.json")
    path = os.path.join(work, "tasks", card["id"] + ".json")
    if read_json_safe(path) != card:
        write_json(path, card)


def read_json_safe(path):
    try:
        return read_json(path)
    except (json.JSONDecodeError, OSError):
        return None


def trip_bindings(trip):
    """Which stops each POI / ticket task serves right now: {card id: [(day, stop), ...]}."""
    poi, ticket = {}, {}
    for day in trip.get("days", []):
        for s in day.get("stops", []):
            if not isinstance(s, dict) or s.get("status") == "已移出" or not s.get("name"):
                continue
            poi.setdefault(card_ids("poi", poi_input(trip, s))[0], []).append((day, s))
            if s.get("booking"):
                ticket.setdefault(card_ids("ticket", ticket_key(trip, day, s))[0], []).append((day, s))
    return poi, ticket


def sync_jobs(work):
    """collect.jsonl = jobs of live cards. Lines for retired cards are dropped; hand-added lines
    for live cards are kept. Returns how many lines were added."""
    path = os.path.join(work, "collect.jsonl")
    live = load_cards(work)
    kept, seen = [], set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                try:
                    card = json.loads(line).get("card")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if line and card in live and line not in seen:
                    kept.append(line)
                    seen.add(line)
    added = 0
    for cid in sorted(live):
        for j in live[cid].get("jobs", []):
            line = json.dumps(dict(j, card=cid), ensure_ascii=False, sort_keys=True)
            if line not in seen:
                kept.append(line)
                seen.add(line)
                added += 1
    write_text(path, "".join(l + "\n" for l in kept))
    return added


# ---------- init / add ----------

def cmd_init(a):
    trip = read_json(a.trip)
    p = paths(a.work)
    for d in p.values():
        os.makedirs(d, exist_ok=True)
    city, adcode, mapname = trip.get("city", ""), trip.get("adcode", ""), trip.get("map", "amap")
    old = load_cards(a.work, active_only=False)
    wanted, need_url, renamed = {}, [], []
    for day in trip.get("days", []):
        wk = day_key(day)
        for s in day.get("stops", []):
            if s.get("status") == "已移出" or not s.get("name"):
                continue
            prov = s.get("provenance") or {}
            if prov.get("name") not in (None, s["name"]):
                renamed.append((prov["name"], s["name"]))
            visit = {"day": day.get("id"), "time": s.get("time", ""), "weekday": DAY_CN.get(wk, "")}
            placeholder = s.get("status") == "未定位" and not s.get("address")
            # POI card: mainland only (abroad, the planner uses a places tool directly)
            done = (s.get("amap_id") or s.get("place_id")) and s.get("hours_week") is not None
            skip_transport = s.get("type") == "交通" and s.get("lng") is not None
            if mapname == "amap" and not placeholder and not skip_transport and (a.all or not done):
                inp = poi_input(trip, s)
                cid, _ = card_ids("poi", inp)
                card = wanted.get(cid)
                if card is None:     # the same place on two days is one task with two visits
                    kw = search_keyword(city, s["name"], inp["address_hint"])
                    card = wanted[cid] = refresh_fingerprint({
                        "kind": "poi", "input": inp, "map": mapname, "city": city,
                        "stop": {"name": s["name"], "type": s.get("type", ""),
                                 "address_hint": inp["address_hint"], "visits": []},
                        "jobs": [{"job": "amap_poi", "keyword": kw, "adcode": str(adcode)}]})
                card["stop"]["visits"].append(visit)
            # Ticket card: stops marked booking: true
            key = ticket_key(trip, day, s)
            cid, _ = card_ids("ticket", key)
            # A rule filled by another task (the visit date changed since) is not "done".
            ruled = s.get("booking_rule") and s.get("booking_ref", cid) == cid
            if s.get("booking") and (a.all or not ruled):
                if cid not in wanted:
                    prev = old.get(cid) or {}
                    wanted[cid] = refresh_fingerprint({
                        "kind": "ticket", "input": key, "venue": s["name"],
                        "visit": dict(visit, date=day.get("iso_date", "")),
                        "sources": prev.get("sources", []),     # official URLs survive a re-init
                        "jobs": prev.get("jobs", [])})
                    if not wanted[cid]["sources"]:
                        need_url.append(cid)
    for cid, card in wanted.items():
        save_card(a.work, card)
    # Cards for places that left the itinerary are retired, not deleted: their raw/out stay on
    # disk and come back into use if the place returns unchanged.
    retired = []
    for cid, card in old.items():
        if card.get("kind") in ("poi", "ticket") and cid not in wanted and card.get("active", True):
            card["active"] = False
            write_json(os.path.join(p["tasks"], cid + ".json"), card)
            retired.append(cid)
    added = sync_jobs(a.work)
    made = sorted(wanted)
    print(f"cards: {len(made)}  ({', '.join(made) or '-'})")
    if retired:
        print(f"retired (no longer in trip.json, files kept): {', '.join(sorted(retired))}")
    print(f"collect jobs added: {added}  → {a.work}/collect.jsonl")
    if mapname != "amap":
        print("map=google: no POI cards; look places up with the places tool and fill trip.json directly.")
    for was, now in renamed:
        print(f"! {now}: was verified as「{was}」. Its address/hours/coordinates in trip.json may "
              f"belong to the old place — clear them or re-check.")
    for cid in need_url:
        print(f"needs official URL: {cid} ({wanted[cid]['venue']})  → delegate.py add-source {a.work} {cid} <url>")


def cmd_add_xhs(a):
    name = a.name
    # Neutral queries first (references/xhs-screening.md): 「避雷」 is not a search keyword.
    queries = a.query or [name, f"{name} 人均"]
    key = {"kind": "xhs", "candidate": norm(name)}
    prev = load_cards(a.work, active_only=False).get(card_ids("xhs", key)[0]) or {}
    queries = sorted(set(prev.get("queries", [])) | set(queries))
    card = refresh_fingerprint({
        "kind": "xhs", "input": key, "candidate": name, "category": a.category,
        "taste_filter": a.taste or "", "queries": queries,
        "jobs": [{"job": "xhs_search", "query": q, "max_notes": a.max_notes} for q in queries]})
    stale = prev.get("fingerprint") not in (None, card["fingerprint"])
    save_card(a.work, card)
    n = sync_jobs(a.work)
    print(f"{card['id']}  {name}  ({n} search jobs)"
          + ("  — inputs changed: the earlier result no longer counts, grade again" if stale else ""))


def cmd_add_source(a):
    card = load_cards(a.work, active_only=False).get(a.card)
    if not card:
        sys.exit(f"no card {a.card}")
    job = {"job": "page", "url": a.url, "name": a.name}
    known = job in card.get("jobs", [])
    if not known:
        card.setdefault("jobs", []).append(job)
        if card["kind"] == "ticket":   # a new official source changes what the answer rests on
            card["sources"] = sorted(set(card.get("sources", [])) | {a.url})
            refresh_fingerprint(card)
        save_card(a.work, card)
    sync_jobs(a.work)
    print(f"{a.card}: {'already queued' if known else 'added'} {a.url}")


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


# ---------- check: deterministic validators ----------
#
# What these checks can and cannot establish. They are plain code, no model involved:
#   error  — the output breaks the contract (wrong shape, stale fingerprint, a quote that is
#            not in the cited file, a number that is not in the saved page). Worker retries.
#   review — the output is well-formed and its quotes are real, but a value is not visibly
#            carried by the quote cited for it. A person or the planner must look.
# A verbatim quote proves the sentence is in the saved page. It does not prove the page is
# right, current, official, or about this place, nor that the worker read it correctly.
# Which shop matches, stance, marketing or not, confidence: those stay model judgments.

MIN_QUOTE, MAX_QUOTE = 8, 200     # characters after removing whitespace
CN_NUM = "零一二三四五六七八九十"

TYPES = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "num": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "obj": lambda v: isinstance(v, dict),
    "year": lambda v: isinstance(v, (str, int)) and not isinstance(v, bool),
    "list:str": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "list:obj": lambda v: isinstance(v, list) and all(isinstance(x, dict) for x in v),
}
EVIDENCE = {"file": "str", "field": "str", "quote": "str"}
COMMON = {"id": "str", "kind": "str", "fingerprint": "str", "result": "str", "confidence": "str",
          "flags": "list:str?"}
SHAPES = {
    "poi": dict(COMMON, match="obj?", candidates="list:obj?", evidence="list:obj?"),
    "poi.match": {"name": "str", "amap_id": "str?", "place_id": "str?", "address": "str?", "lng": "num?",
                  "lat": "num?", "phone": "str?", "nearest_metro": "str?", "hours": "str?",
                  "hours_note": "str?", "hours_week": "obj?", "closed_notice": "str?"},
    "ticket": dict(COMMON, rule="obj?", rule_year="year?", unverified="list:str?", sources="list:obj?",
                   evidence="list:obj?"),
    "ticket.rule": {"required": "bool?", "channel": "str?", "advance_days": "int?", "release_time": "str?",
                    "same_day": "bool?", "closed_days": "list:str?", "last_entry": "str?",
                    "eligibility": "str?", "price": "str?", "real_name": "bool?"},
    "xhs": dict(COMMON, candidate="str?", notes="list:obj", independent_positive="int",
                red_flags="list:obj?", taste_filter_hits="list:obj?", planner_note="str?"),
    "xhs.note": {"file": "str", "author": "str?", "is_marketing": "bool", "marketing_hits": "list:str?",
                 "stance": "str?", "real_signals": "list:obj?"},
}


class Problems(list):
    def add(self, code, msg, level="error"):
        self.append({"code": code, "level": level, "msg": msg})


def shape(obj, spec, where, P):
    """Type-check obj against {key: type}; a trailing ? allows null / absent."""
    if not isinstance(obj, dict):
        P.add("SCHEMA", f"{where} must be an object")
        return False
    ok = True
    for key, t in spec.items():
        v = obj.get(key)
        if v is None:
            if not t.endswith("?"):
                P.add("SCHEMA", f"{where}.{key} is missing")
                ok = False
        elif not TYPES[t.rstrip("?")](v):
            P.add("SCHEMA", f"{where}.{key} must be {t.rstrip('?')}")
            ok = False
    return ok


def raw_texts(card, work):
    d = os.path.join(work, "raw", card["id"])
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                out[os.path.relpath(p, d)] = f.read()
    return out


def find_raw(texts, name):
    """The raw file a citation points at: exact relative path, or an unambiguous basename."""
    if not isinstance(name, str) or not name:
        return None
    if name in texts:
        return name
    hits = [k for k in texts if os.path.basename(k) == os.path.basename(name)]
    return hits[0] if len(hits) == 1 else None


def flat(s):
    """Comparison form: NFKC (： → :, full-width digits → ASCII) with all whitespace removed."""
    return squash(unicodedata.normalize("NFKC", str(s or "")))


def check_quotes(items, texts, where, P, default_file=None):
    """Each item cites a file that exists and quotes a non-trivial passage found in that file."""
    good = []
    for n, ev in enumerate(items or []):
        w = f"{where}[{n}]"
        if not isinstance(ev, dict) or not isinstance(ev.get("quote"), str):
            P.add("SCHEMA", f"{w} must be an object with a string quote")
            continue
        q, f = flat(ev["quote"]), ev.get("file") or default_file
        key = find_raw(texts, f)
        if not q:
            P.add("EMPTY_QUOTE", f"{w}: empty quote")
        elif len(q) < MIN_QUOTE:
            P.add("QUOTE_TOO_SHORT", f"{w}: quote「{ev['quote']}」is under {MIN_QUOTE} characters and proves nothing")
        elif len(q) > MAX_QUOTE:
            P.add("QUOTE_TOO_LONG", f"{w}: quote is over {MAX_QUOTE} characters; cite the sentence, not the page")
        elif key is None:
            P.add("CITED_FILE_MISSING", f"{w}: cited file {f!r} is not in raw/ (have: {', '.join(texts) or 'nothing'})")
        elif q not in flat(texts[key]):
            P.add("QUOTE_NOT_FOUND", f"{w}: quote not found verbatim in {key}: {ev['quote'][:30]}…")
        else:
            good.append(ev)
    return good


def quoted(evidence, *fields):
    """All verified quote text cited for the given fields, in comparison form."""
    return "".join(flat(e["quote"]) + "\n" for e in evidence if e.get("field") in fields)


def cn_number(n):
    if 0 <= n <= 10:
        return CN_NUM[n]
    if n < 20:
        return "十" + CN_NUM[n - 10]
    if n < 100:
        return CN_NUM[n // 10] + "十" + (CN_NUM[n % 10] if n % 10 else "")
    return str(n)


def says_number(text, n):
    return bool(re.search(rf"(?<!\d){n}(?!\d)", text)) or cn_number(n) in text or (n == 2 and "两" in text)


def says_time(text, value):
    """Does text state this clock time in any usual spelling? 17:00 = 17:00 / 17点 / 下午5点 / 5:00pm."""
    m = minutes(value)
    if m is None:
        return False
    h, mi = divmod(m, 60)
    if mi == 0 and h in (0, 24) and re.search(r"24小时|全天|通宵", text):
        return True
    hours = {h % 24} | ({h} if h < 24 else set()) | ({24} if h % 24 == 0 else set())
    for hh in hours:
        if re.search(rf"(?<!\d)0?{hh}[:.]{mi:02d}(?!\d)", text):
            return True
        tail = "" if mi == 0 else "半" if mi == 30 else f"{mi}分?"
        spoken = [str(hh), cn_number(hh)] + ([str(hh - 12), cn_number(hh - 12)] if 12 < hh < 24 else [])
        if any(re.search(rf"(?<!\d){x}[点时]{tail}" + ("(?![0-9半一二三四五六七八九十])" if mi == 0 else ""), text)
               for x in spoken):
            return True
        if 12 < hh < 24 and re.search(rf"(?:下午|晚上?|pm)\s*0?{hh - 12}[:.]{mi:02d}|(?<!\d)0?{hh - 12}[:.]{mi:02d}\s*pm",
                                      text, re.I):
            return True
    return False


def check_hours_week(hw, P):
    if hw is None:
        return
    for k, spans in hw.items():
        if k not in DAYS:
            P.add("BAD_HOURS", f"hours_week: bad key {k}")
            continue
        if not isinstance(spans, list):
            P.add("BAD_HOURS", f"hours_week.{k} must be a list")
            continue
        for sp in spans:
            if not (isinstance(sp, list) and len(sp) == 2):
                P.add("BAD_HOURS", f"hours_week.{k}: span must be [open, close]")
                continue
            o, c = minutes(sp[0]), minutes(sp[1])
            if o is None or c is None or not (0 <= o < c <= 30 * 60):
                P.add("BAD_HOURS", f"hours_week.{k}: bad span {sp}")


def validate_poi(card, out, texts, P):
    if out["result"] not in ("found", "not_found", "ambiguous", "insufficient"):
        P.add("SCHEMA", "result must be found|not_found|ambiguous|insufficient")
    good = check_quotes(out.get("evidence"), texts, "evidence", P)
    m = out.get("match")
    if out["result"] == "ambiguous" and not out.get("candidates"):
        P.add("SCHEMA", "ambiguous but no candidates")
    if out["result"] != "found":
        if m not in (None, {}):
            P.add("SCHEMA", "match must be null unless result is found")
        return
    if not shape(m, SHAPES["poi.match"], "match", P):
        return
    check_hours_week(m.get("hours_week"), P)
    everything = flat("\n".join(texts.values()))
    if not out.get("evidence"):
        P.add("NO_EVIDENCE", "found, but not a single piece of evidence is cited")
    # Literal copies: these must be character-for-character in the saved pages.
    name = flat(no_parens(m["name"]))
    if not name or name not in everything:
        P.add("VALUE_NOT_IN_RAW", f"match.name「{m['name']}」does not appear in the saved pages")
    for k in ("amap_id", "place_id", "phone"):
        if m.get(k) and flat(m[k]) not in everything:
            P.add("VALUE_NOT_IN_RAW", f"match.{k} {m[k]!r} does not appear in the saved pages")
    lng, lat = m.get("lng"), m.get("lat")
    if (lng is None) != (lat is None):
        P.add("BAD_COORDS", "only one of lng/lat")
    elif lng is not None:
        if not (73 <= lng <= 136 and 3 <= lat <= 54):
            P.add("BAD_COORDS", f"lng/lat {lng},{lat} outside mainland China")
        def numbers(t):
            return {float(x) for x in re.findall(r"\d+\.\d+", flat(t))}
        if not any({float(lng), float(lat)} <= numbers(t) for t in texts.values()):
            P.add("VALUE_NOT_IN_RAW", f"lng/lat {lng},{lat} are not in any saved page; copy the numbers, do not estimate")
    # Every free-text field needs a citation; the citation should visibly carry the value.
    for field, labels in (("address", ("address",)), ("hours", ("hours", "hours_week")),
                          ("hours_week", ("hours", "hours_week")), ("closed_notice", ("closed_notice",))):
        if m.get(field) and not any(e.get("field") in labels for e in out.get("evidence") or []):
            P.add("NO_EVIDENCE", f"match.{field} given without {labels[0]} evidence")
    if m.get("address") and flat(m["address"])[-6:] not in quoted(good, "address"):
        P.add("VALUE_NOT_IN_QUOTE", f"address「{m['address']}」is not what the address quote says", "review")
    if re.search(r"\d{1,2}[:：]\d{2}", m.get("hours") or "") and not m.get("hours_week"):
        P.add("HOURS_NOT_STRUCTURED", f"hours「{m['hours']}」has clock times but hours_week is empty, so the "
                                      f"schedule cannot be checked against it", "review")
    hours_text = quoted(good, "hours", "hours_week")
    times = sorted({t for spans in (m.get("hours_week") or {}).values() if isinstance(spans, list)
                    for sp in spans if isinstance(sp, list) for t in sp if isinstance(t, str)})
    missing = [t for t in times if not says_time(hours_text, t)]
    if hours_text and missing:
        P.add("VALUE_NOT_IN_QUOTE", f"hours_week times {', '.join(missing)} are not in the hours quotes", "review")


def validate_ticket(card, out, texts, P):
    if out["result"] not in ("found", "insufficient"):
        P.add("SCHEMA", "result must be found|insufficient")
    good = check_quotes(out.get("evidence"), texts, "evidence", P)
    for n, src in enumerate(out.get("sources") or []):
        key = find_raw(texts, src.get("file"))
        if key is None:
            P.add("CITED_FILE_MISSING", f"sources[{n}]: file {src.get('file')!r} is not in raw/")
        elif not isinstance(src.get("url"), str) or flat(src["url"]) not in flat(texts[key]):
            P.add("VALUE_NOT_IN_RAW", f"sources[{n}]: url {src.get('url')!r} is not the URL recorded in {key}")
    r = out.get("rule")
    if out["result"] != "found":
        return
    if not shape(r, SHAPES["ticket.rule"], "rule", P):
        return
    for f in ("release_time", "last_entry"):
        if r.get(f) not in (None, "") and minutes(r[f]) is None:
            P.add("SCHEMA", f"rule.{f} must be HH:MM")
    filled = [k for k, v in r.items() if v not in (None, "", [])]
    cited = {e.get("field") for e in out.get("evidence") or [] if isinstance(e, dict)}
    if [k for k in filled if k not in cited]:
        P.add("NO_EVIDENCE", f"fields without evidence: {', '.join(k for k in filled if k not in cited)}")
    unverified = set(out.get("unverified") or [])
    year, visit = str(out.get("rule_year") or ""), str(card.get("visit", {}).get("date") or "")
    if re.fullmatch(r"\d{4}", year) and re.match(r"\d{4}-", visit):
        if int(year) < int(visit[:4]) - 1 and not set(filled) <= unverified:
            P.add("RULE_TOO_OLD", f"rules dated {year} for a {visit[:4]} visit: every field must be listed in unverified")
        if year not in flat("\n".join(texts.values())):
            P.add("VALUE_NOT_IN_QUOTE", f"rule_year {year} does not appear in the saved pages", "review")
    # The cited sentence should carry the value (catches a real quote attached to a made-up number).
    checks = []
    if r.get("advance_days") is not None and "advance_days" not in unverified:
        checks.append(("advance_days", says_number(quoted(good, "advance_days"), r["advance_days"])))
    for f in ("release_time", "last_entry"):
        if r.get(f) and f not in unverified:
            checks.append((f, says_time(quoted(good, f), r[f])))
    if r.get("closed_days") and "closed_days" not in unverified:
        text = quoted(good, "closed_days")
        said = cn_days_to_keys(re.findall(r"(?:周|星期|礼拜)[一二三四五六日天]", text))
        checks.append(("closed_days", cn_days_to_keys(r["closed_days"]) <= said and bool(said)))
    if r.get("price") and "price" not in unverified:
        text, nums = quoted(good, "price"), re.findall(r"\d+", flat(r["price"]))
        checks.append(("price", all(says_number(text, int(n)) for n in nums) if nums else flat(r["price"]) in text))
    for field, ok in checks:
        if not ok and field in cited:
            P.add("VALUE_NOT_IN_QUOTE", f"rule.{field} = {r[field]!r} is not what its quote says", "review")


def validate_xhs(card, out, texts, P):
    if out["result"] not in ("graded", "insufficient"):
        P.add("SCHEMA", "result must be graded|insufficient")
    for i, n in enumerate(out["notes"]):
        if not shape(n, SHAPES["xhs.note"], f"notes[{i}]", P):
            continue
        if find_raw(texts, n["file"]) is None:
            P.add("CITED_FILE_MISSING", f"notes[{i}]: file {n['file']!r} is not in raw/")
        if n["is_marketing"] != (len(n.get("marketing_hits") or []) >= 2):
            P.add("SCHEMA", f"notes[{i}]: is_marketing must equal (marketing_hits >= 2)")
        check_quotes(n.get("real_signals"), texts, f"notes[{i}].real_signals", P, default_file=n["file"])
    check_quotes(out.get("red_flags"), texts, "red_flags", P)
    check_quotes(out.get("taste_filter_hits"), texts, "taste_filter_hits", P)
    authors = {n.get("author") for n in out["notes"] if isinstance(n, dict)
               and not n.get("is_marketing") and n.get("stance") in ("正面", "混合")}
    if out["independent_positive"] != len(authors):
        P.add("SCHEMA", f"independent_positive should be {len(authors)} (distinct non-marketing authors)")
    if len(out.get("planner_note") or "") > 80:
        P.add("SCHEMA", "planner_note longer than 80 chars")


def validate_out(card, out, texts):
    """All deterministic checks for one output → Problems (dicts with code / level / msg)."""
    P = Problems()
    if card.get("schema") != SCHEMA:
        P.add("LEGACY_CARD", f"task card is from an older version (schema {card.get('schema', 1)}): re-run init / add-xhs")
        return P
    kind = card["kind"]
    if not shape(out, SHAPES[kind], "output", P):
        return P
    if out["id"] != card["id"]:
        P.add("ID_MISMATCH", f"id {out['id']!r} != card {card['id']!r}")
    if out["kind"] != kind:
        P.add("ID_MISMATCH", f"kind {out['kind']!r} != {kind!r}")
    if out["fingerprint"] != card["fingerprint"]:
        P.add("STALE_OUTPUT", f"fingerprint {out['fingerprint']!r} != card {card['fingerprint']!r}: the task's "
                              f"inputs changed after this was written, or the worker did not copy it")
    if out["confidence"] not in ("high", "medium", "low"):
        P.add("SCHEMA", "confidence must be high|medium|low")
    {"poi": validate_poi, "ticket": validate_ticket, "xhs": validate_xhs}[kind](card, out, texts, P)
    return P


# ---------- task state (durable checkpoint) ----------
#
# state.json is the checkpoint: one record per live task, rewritten atomically, and identical
# bytes when nothing changed. It is derived from files (cards, raw/, out/) plus the few facts
# files cannot hold (attempt count, lease, approval), so losing it only loses those.
# events.jsonl is the append-only history of state changes.
#
#   pending       nothing usable yet (or the old output is stale) — dispatch it
#   running       claimed by a worker; the lease expires so a dead worker cannot hold it
#   succeeded     passed every deterministic check (or review-level doubts were approved)
#   failed        broke the contract; retry with the reasons attached
#   needs_review  a human or the planner must act: retries used up, sign-in/CAPTCHA wall,
#                 or the quotes are real but do not visibly carry the values

STATES = ("pending", "running", "succeeded", "failed", "needs_review")
MAX_ATTEMPTS = 2
LEASE_MINUTES = 20


def sha_of(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def load_state(work):
    try:
        data = read_json(os.path.join(work, "state.json"), {})
        return data["tasks"] if isinstance(data.get("tasks"), dict) else {}
    except (json.JSONDecodeError, AttributeError, KeyError, UnicodeDecodeError):
        sys.stderr.write("warning: state.json unreadable; rebuilding it from the files\n")
        return {}


def blocked_ids(work, cards):
    path = os.path.join(work, "raw", "_blocked.txt")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return {cid for cid in cards if cid in text}


def evaluate(card, prev, work, clock, blocked):
    """Next state record for one task from its files and its previous record."""
    cid, fp = card["id"], card.get("fingerprint")
    outp = os.path.join(work, "out", cid + ".json")
    sha = sha_of(outp)
    if prev and prev.get("fingerprint") != fp:
        # The task's inputs changed: whatever sits in out/ was written for the old version.
        # Set it aside without charging the worker an attempt.
        prev = {"attempts": 0, "ignore_sha": sha}
    rec = {"state": "pending", "kind": card["kind"], "fingerprint": fp, "attempts": prev.get("attempts", 0),
           "out_sha": prev.get("out_sha"), "reasons": [], "problems": []}
    for k in ("ignore_sha", "lease", "approved"):
        if prev.get(k):
            rec[k] = prev[k]
    lease = rec.get("lease")
    live = lease and dt.datetime.fromisoformat(lease["until"]) > clock
    usable = sha is not None and sha != rec.get("ignore_sha")
    if usable and not (live and sha == lease.get("out_sha")):
        # A (new) output is on disk: judge it. This is the only place attempts are counted for
        # outputs, and only once per distinct file content.
        try:
            probs = list(validate_out(card, read_json(outp), raw_texts(card, work)))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            probs = [{"code": "UNREADABLE", "level": "error", "msg": f"output is not valid JSON ({e})"}]
        if sha != rec["out_sha"]:
            rec["attempts"] += 1
            rec["out_sha"] = sha
        rec.pop("lease", None)
        if (rec.get("approved") or {}).get("out_sha") != sha:
            rec.pop("approved", None)
        rec["problems"] = probs
        rec["reasons"] = sorted({p["code"] for p in probs})
        if any(p["level"] == "error" for p in probs):
            rec["state"] = "failed"
            if rec["attempts"] >= MAX_ATTEMPTS:
                rec["state"] = "needs_review"
                rec["reasons"].append("RETRIES_EXHAUSTED")
        elif probs and not rec.get("approved"):
            rec["state"] = "needs_review"
        else:
            rec["state"] = "succeeded"
        return rec
    rec["out_sha"] = prev.get("out_sha")
    if live:
        rec["state"] = "running"
        return rec
    if lease:                                   # the worker never delivered
        rec.pop("lease")
        rec["attempts"] += 1
        rec["reasons"].append("LEASE_EXPIRED")
    if sha is not None and sha == rec.get("ignore_sha"):
        rec["reasons"].append("STALE_OUTPUT")
    if cid in blocked:
        rec["state"] = "needs_review"
        rec["reasons"].append("HUMAN_ACTION_REQUIRED")
    elif rec["attempts"] >= MAX_ATTEMPTS:
        rec["state"] = "needs_review"
        rec["reasons"].append("RETRIES_EXHAUSTED")
    elif not raw_texts(card, work):
        rec["reasons"].append("NO_RAW")
    return rec


def run_check(work, quiet=False):
    """Re-evaluate every live task, persist the checkpoint, log state changes. → {id: record}"""
    cards = load_cards(work)
    old = load_state(work)
    clock, blocked = now(), blocked_ids(work, cards)
    tasks, events = {}, []
    for cid in sorted(cards):
        prev = old.get(cid, {})
        rec = evaluate(cards[cid], prev, work, clock, blocked)
        same = all(rec.get(k) == prev.get(k) for k in ("state", "fingerprint", "attempts", "out_sha", "reasons"))
        rec["updated_at"] = prev.get("updated_at") if same and prev.get("updated_at") else clock.isoformat()
        if not same:
            events.append({"at": clock.isoformat(), "task": cid, "from": prev.get("state"), "to": rec["state"],
                           "attempts": rec["attempts"], "reasons": rec["reasons"]})
        tasks[cid] = rec
    save_state(work, tasks, events)
    if not quiet:
        for cid, r in tasks.items():
            if r["state"] == "succeeded":
                continue
            print(f"{r['state'].upper():12} {cid}  {label(cards[cid])}" + (f"  (attempt {r['attempts']})" if r["attempts"] else "")
                  + (f"  [{', '.join(r['reasons'])}]" if r["reasons"] else ""))
            for pr in r["problems"]:
                print(f"         - {pr['code']}: {pr['msg']}")
        print("  ".join(f"{st} {sum(r['state'] == st for r in tasks.values())}" for st in STATES))
    return tasks


def save_state(work, tasks, events=()):
    path = os.path.join(work, "state.json")
    text = dump_json({"schema": SCHEMA, "tasks": tasks})
    current = None
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            current = f.read()
    if text != current:
        write_text(path, text)
    if events:
        with open(os.path.join(work, "events.jsonl"), "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")


def cmd_check(a):
    tasks = run_check(a.work)
    sys.exit(0 if all(r["state"] == "succeeded" for r in tasks.values()) else 1)


def cmd_status(a):
    tasks = run_check(a.work, quiet=True)
    cards = load_cards(a.work)
    counts = {st: sum(r["state"] == st for r in tasks.values()) for st in STATES}
    if a.json:
        print(json.dumps({"counts": counts, "tasks": tasks}, ensure_ascii=False, indent=2))
        return
    print("  ".join(f"{st} {n}" for st, n in counts.items()))
    for cid, r in tasks.items():
        print(f"  {r['state']:12} {cid}  {label(cards[cid])}  attempts={r['attempts']}"
              + (f"  [{', '.join(r['reasons'])}]" if r["reasons"] else ""))


def cmd_claim(a):
    """Mark tasks as handed to a worker so nobody dispatches them twice."""
    tasks = run_check(a.work, quiet=True)
    clock, refused, events = now(), [], []
    for cid in a.ids:
        r = tasks.get(cid)
        if r is None:
            refused.append(f"{cid}: no such live task")
        elif r["state"] == "running":
            refused.append(f"{cid}: already running (owner {r['lease']['owner']}, until {r['lease']['until']})")
        elif r["state"] not in ("pending", "failed"):
            refused.append(f"{cid}: is {r['state']}, not dispatchable" + (f" [{', '.join(r['reasons'])}]" if r["reasons"] else ""))
        else:
            r["lease"] = {"owner": a.owner, "until": (clock + dt.timedelta(minutes=a.minutes)).isoformat(),
                          "out_sha": sha_of(os.path.join(a.work, "out", cid + ".json"))}
            events.append({"at": clock.isoformat(), "task": cid, "from": r["state"], "to": "running",
                           "attempts": r["attempts"], "reasons": [], "owner": a.owner})
            r.update(state="running", updated_at=clock.isoformat(), reasons=[], problems=[])
            print(f"{cid}: running (owner {a.owner}, {a.minutes} min)")
    save_state(a.work, tasks, events)
    for line in refused:
        print(line)
    sys.exit(1 if refused else 0)


def cmd_approve(a):
    """A person (or the planner) has read the evidence and accepts a needs_review output as is."""
    tasks = run_check(a.work, quiet=True)
    r = tasks.get(a.id)
    if r is None:
        sys.exit(f"no live task {a.id}")
    if r["state"] != "needs_review" or any(p["level"] == "error" for p in r["problems"]) or not r["problems"]:
        sys.exit(f"{a.id} is {r['state']} [{', '.join(r['reasons'])}]: only outputs whose sole problems are "
                 f"review-level can be approved. Fix the output (or the blocker) instead.")
    clock = now()
    r["approved"] = {"by": a.by, "note": a.note, "out_sha": r["out_sha"], "at": clock.isoformat()}
    r.update(state="succeeded", updated_at=clock.isoformat())
    save_state(a.work, tasks, [{"at": clock.isoformat(), "task": a.id, "from": "needs_review", "to": "succeeded",
                                "attempts": r["attempts"], "reasons": r["reasons"], "approved_by": a.by,
                                "note": a.note}])
    print(f"{a.id}: approved by {a.by}; it will be merged")


def cmd_unblock(a):
    """The user has signed in / solved the CAPTCHA themselves: let collection try again."""
    path = os.path.join(a.work, "raw", "_blocked.txt")
    if not os.path.exists(path):
        print("nothing is blocked")
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    keep = [ln for ln in lines if not any(cid in ln for cid in a.ids)]
    write_text(path, "".join(ln + "\n" for ln in keep))
    clock = now().isoformat()
    with open(os.path.join(a.work, "events.jsonl"), "a", encoding="utf-8") as f:
        for ln in lines:
            if ln not in keep:
                f.write(json.dumps({"at": clock, "event": "unblocked", "line": ln}, ensure_ascii=False) + "\n")
    print(f"unblocked {len(lines) - len(keep)} line(s); send the collector again")


REVIEW_HINT = {
    "HUMAN_ACTION_REQUIRED": "a page needs sign-in / CAPTCHA: ask the user to do it in their own browser, "
                             "then `unblock` and collect again. Never enter credentials for them.",
    "RETRIES_EXHAUSTED": "the worker failed twice: the planner does this card by hand (same output format).",
    "HOURS_NOT_STRUCTURED": "opening hours were copied as text but not turned into hours_week: fill hours_week "
                            "in the out file (no weekday in the source = every day).",
    "VALUE_NOT_IN_QUOTE": "quotes are real but do not visibly state the value: open the out file, compare, "
                          "then fix it or `approve`.",
}


def cmd_pending(a):
    tasks = run_check(a.work, quiet=True)
    cards = load_cards(a.work)
    groups = {}
    for cid, r in tasks.items():
        if r["state"] == "succeeded":
            continue
        if r["state"] == "running":
            who, why = "running — do not dispatch again", f"owner {r['lease']['owner']}, lease until {r['lease']['until']}"
        elif r["state"] == "needs_review":
            who = "needs_review — planner or user, not a worker"
            why = "; ".join(REVIEW_HINT[c] for c in r["reasons"] if c in REVIEW_HINT) or ", ".join(r["reasons"])
        elif "NO_RAW" in r["reasons"]:
            who, why = "collect first (page-collector or capture-amap)", ""
        else:
            who = WORKER[cards[cid]["kind"]]
            why = "; ".join(f"{p['code']}: {p['msg']}" for p in r["problems"][:3]) or ", ".join(r["reasons"])
            why = why and "retry because: " + why
        groups.setdefault(who, []).append((cid, why))
    if not groups:
        print("nothing pending")
        return
    for who, items in groups.items():
        print(f"[{who}]  {len(items)}")
        for cid, why in items:
            print(f"  {cid}  {label(cards[cid])}  {cards[cid].get('raw', '')}" + (f"\n      {why}" if why else ""))


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
        if r.get("state") != "succeeded":
            print(f"{cid}  {label(card)}  [{r.get('state')}" + (": " + ", ".join(r["reasons"]) if r.get("reasons") else "") + "]")
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


def now():
    """Current time; DELEGATE_NOW (ISO 8601) pins it for tests and reproducible runs."""
    v = os.environ.get("DELEGATE_NOW")
    return dt.datetime.fromisoformat(v) if v else dt.datetime.now().astimezone()


def apply_poi(stop, card, o, add_todo):
    """Fold one checked POI result into one stop. Returns True when a match was applied."""
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
        # Where the facts came from, and the hint the lookup used (address is now the map's).
        stop["provenance"] = {"task": card["id"], "fingerprint": card["fingerprint"],
                              "name": stop["name"], "address_hint": card["input"]["address_hint"],
                              "confidence": conf}
        if m.get("closed_notice"):
            add_todo(f"{stop['name']}：地图提示「{m['closed_notice']}」，决定是否换掉")
        if conf == "low":
            add_todo(f"{stop['name']}：地图信息可信度低，人工确认")
        return True
    if res == "not_found":
        stop["status"] = "待核实"
        add_todo(f"{stop['name']}：地图搜不到，可能已关店，换一家或人工确认")
    elif res == "ambiguous":
        stop["status"] = "待核实"
        names = " / ".join(c.get("name", "") for c in o.get("candidates", []))
        add_todo(f"{stop['name']}：地图上有多个同名点（{names}），确认是哪一家")
    else:
        add_todo(f"{stop['name']}：原文不足，地图信息没核到")
    return False


RULE_KEYS = ("required", "channel", "advance_days", "release_time")


def cmd_merge(a):
    with open(a.trip, encoding="utf-8") as f:
        before_text = f.read()
    trip = json.loads(before_text)
    before = json.loads(before_text)
    report = run_check(a.work, quiet=True)
    cards = load_cards(a.work)
    app = trip.setdefault("appendix", {})
    todo = app.setdefault("todo", [])
    tickets = trip.setdefault("tickets", [])
    log, n_poi, n_tix = [], 0, 0

    def add_todo(t):
        if t not in todo:
            todo.append(t)

    poi_stops, ticket_stops = trip_bindings(trip)
    # Auto-generated ticket lines belong to a task; drop those whose task no longer matches
    # the itinerary (venue removed, or its date changed and a new task replaced it).
    for t in [t for t in tickets if t.get("ref") and t["ref"] not in ticket_stops]:
        tickets.remove(t)
        log.append(f"removed outdated ticket line {t['ref']}: {t.get('what', '')}")
    by_poi, by_venue = {}, {}      # amap id -> [(stop name, matched name)], venue -> [(card, rule)]
    for cid in sorted(cards):      # sorted: the result never depends on creation order
        card = cards[cid]
        if report.get(cid, {}).get("state") != "succeeded":
            continue
        o = read_json(os.path.join(a.work, "out", cid + ".json"))
        if card["kind"] == "poi":
            targets = poi_stops.get(cid, [])
            if not targets:
                log.append(f"skip {cid} ({card['stop']['name']}): not in trip.json any more — re-run init")
                continue
            applied = [apply_poi(stop, card, o, add_todo) for _day, stop in targets]
            if any(applied):
                n_poi += 1
                pid = o["match"].get("amap_id") or o["match"].get("place_id")
                if pid:
                    by_poi.setdefault(pid, []).append((card["stop"]["name"], o["match"].get("name", "")))
        elif card["kind"] == "ticket":
            targets = ticket_stops.get(cid, [])
            if not targets:
                log.append(f"skip {cid} ({card['venue']}): no such booking in trip.json any more — re-run init")
                continue
            if o["result"] != "found":
                add_todo(f"{card['venue']}：没找到官方预约规则")
                continue
            v, r = card["visit"], o["rule"]
            day = targets[0][0]
            for _day, stop in targets:
                stop["booking_rule"] = r
                stop["booking_ref"] = cid
            what = f"{day.get('label', v['day'])} {v.get('time', '')} {card['venue']}".strip()
            if r.get("real_name"):
                what += "（实名）"
            entry = {"ref": cid, "when": release_when(v.get("date"), r),
                     "platform": r.get("channel") or "渠道待核实", "what": "预约 " + what}
            venue_key = no_parens(card["venue"])
            mine = [i for i, t in enumerate(tickets)
                    if t.get("ref") == cid or (not t.get("ref") and t.get("venue") == card["venue"])]
            if mine:
                tickets[mine[0]] = entry
            else:
                tickets.append(entry)
            # A hand-written line that merely mentions the venue may be about another one
            # (「苏州博物馆西馆」 vs 「苏州博物馆」): never overwrite it, ask instead.
            for t in tickets:
                if not t.get("ref") and venue_key and venue_key in t.get("what", "") + t.get("platform", ""):
                    add_todo(f"抢票清单里手写的「{t.get('what', '')}」可能和自动生成的「{card['venue']}」"
                             f"是同一件事，确认后删掉多余的一条")
            if o.get("unverified"):
                add_todo(f"{card['venue']} 预约规则待核实：{', '.join(o['unverified'])}")
            src = app.setdefault("sources", [])
            for s in o.get("sources", []):
                if s.get("url") and not any(x.get("url") == s["url"] for x in src):
                    src.append({"title": f"{card['venue']} 官方预约说明", "url": s["url"],
                                "note": f"{o.get('rule_year', '?')} 年规则"})
            by_venue.setdefault(norm(card["venue"]), []).append((card, r))
            n_tix += 1
    # Cross-task consistency: the same map point under two names, or one venue with two rules.
    for pid in sorted(by_poi):
        names = sorted({n for n, _ in by_poi[pid]})
        if len(names) > 1:
            add_todo(f"{' 和 '.join(names)} 核对到了同一个地图点（{by_poi[pid][0][1] or pid}），"
                     f"至少有一个认错了，人工确认")
            for _day, stop in (x for c in cards.values() if c["kind"] == "poi" and c["stop"]["name"] in names
                               for x in poi_stops.get(c["id"], [])):
                stop["status"] = "待核实"
    for venue in sorted(by_venue):
        rules = by_venue[venue]
        diff = [k for k in RULE_KEYS if len({json.dumps(r.get(k), ensure_ascii=False) for _, r in rules}) > 1]
        if diff:
            dates = "、".join(sorted(c["visit"].get("date") or c["visit"].get("weekday", "") for c, _ in rules))
            add_todo(f"{rules[0][0]['venue']}：不同日期（{dates}）核到的预约规则不一致（{', '.join(diff)}），"
                     f"以官方最新说明为准，人工确认")
    # xhs results never change trip.json: the planner reads `digest` and decides.
    summary = f"合并核对结果：{n_poi} 个地点、{n_tix} 条预约规则"
    changed = trip != before
    print(summary + ("" if changed else "（trip.json 无变化）"))
    for line in log:
        print("  " + line)
    if not changed:
        return
    if not a.write:
        print("(dry run — add --write to save; backups are kept)")
        return
    # Idempotent by construction: a merge that changes nothing writes nothing, so the changelog
    # only grows when facts did. Every distinct pre-merge version is kept under work/backups/.
    app.setdefault("changelog", []).append(f"{now().date().isoformat()}（自动）：{summary}")
    keep = os.path.join(a.work, "backups", f"trip-{hashlib.sha256(before_text.encode('utf-8')).hexdigest()[:12]}.json")
    if not os.path.exists(keep):
        write_text(keep, before_text)
    write_text(a.trip + ".bak", before_text)
    write_json(a.trip, trip)
    print(f"saved {a.trip}  (previous version: {a.trip}.bak and {keep})")


# ---------- conflicts ----------

def trip_clock(trip):
    """(tzinfo or None, now in the destination's timezone). trip.timezone is an IANA name."""
    name = trip.get("timezone")
    if not name:
        return None, now()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(str(name))
    except Exception:     # unknown name, or no tz database on this machine
        return None, now()
    return tz, now().astimezone(tz)


def find_conflicts(trip):
    out = []

    def add(level, day, stop, msg):
        out.append({"level": level, "day": day.get("label") or day.get("id"),
                    "time": stop.get("time", ""), "name": stop.get("name", ""), "msg": msg})

    tz, clock = trip_clock(trip)
    if trip.get("timezone") and tz is None:
        out.append({"level": "ERROR", "day": "-", "time": "", "name": "",
                    "msg": f"timezone「{trip['timezone']}」不是合法的时区名（例：Asia/Tokyo）"})
    elif not trip.get("timezone") and trip.get("map") == "google":
        out.append({"level": "WARN", "day": "-", "time": "", "name": "",
                    "msg": "境外行程没写 timezone：「今天」和放号时间只能按这台电脑的时区算，可能差一天"})
    prev_date = None
    for day in trip.get("days", []):
        wk = day_key(day)
        none = {"time": "", "name": ""}
        date, iso = None, day.get("iso_date")
        if iso:
            try:
                date = dt.date.fromisoformat(str(iso))
            except ValueError:
                add("ERROR", day, none, f"iso_date「{iso}」不是合法日期（要写成 2026-11-07 这样）")
        said = sorted(cn_days_to_keys([day.get("weekday", ""), day.get("date", "")]))
        if date and said and DAYS[date.weekday()] not in said:
            add("ERROR", day, none, f"iso_date {iso} 是{DAY_CN[DAYS[date.weekday()]]}，"
                                    f"但这一天写的是{DAY_CN[said[0]]}，两个对不上")
        if date and prev_date and date <= prev_date:
            add("WARN", day, none, f"iso_date {iso} 早于或等于上一天（{prev_date.isoformat()}），天的顺序或日期写错了")
        if date and date < clock.date():
            add("WARN", day, none, f"这一天（{iso}）已经过去了")
        prev_date = date or prev_date
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
            closed = cn_days_to_keys(rule.get("closed_days"))
            for k in sorted(closed, key=DAYS.index):
                if isinstance(hw, dict) and hw.get(k):
                    add("WARN", day, s, f"预约规则写{DAY_CN[k]}闭馆，但地图营业时间显示{DAY_CN[k]}营业，两处不一致，以官方说明为准")
            adv, rt = rule.get("advance_days"), minutes(rule.get("release_time"))
            if date and rule.get("required") and isinstance(adv, int) and date >= clock.date():
                opens = dt.datetime.combine(date - dt.timedelta(days=adv), dt.time(*divmod(rt or 0, 60)), clock.tzinfo)
                if opens <= clock:
                    add("WARN", day, s, f"放号时间（{opens:%m/%d %H:%M}）已经过了，现在就能约，别再等")
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
    p = sub.add_parser("status"); p.add_argument("work"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("claim"); p.add_argument("work"); p.add_argument("ids", nargs="+")
    p.add_argument("--owner", default="worker"); p.add_argument("--minutes", type=int, default=LEASE_MINUTES)
    p.set_defaults(fn=cmd_claim)
    p = sub.add_parser("approve"); p.add_argument("work"); p.add_argument("id")
    p.add_argument("--by", required=True, help="who looked at the evidence"); p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("unblock"); p.add_argument("work"); p.add_argument("ids", nargs="+")
    p.set_defaults(fn=cmd_unblock)
    p = sub.add_parser("merge"); p.add_argument("trip"); p.add_argument("work")
    p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("conflicts"); p.add_argument("trip"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_conflicts)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
