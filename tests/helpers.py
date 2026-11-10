"""Shared fixtures for the delegate.py regression tests (stdlib only, fully offline).

Tests drive the real CLI in a temp directory. They never hard-code task ids: cards are looked
up by the place they describe, so the same tests run against any id scheme.
"""
import copy
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "travel-route-book", "scripts")
DELEGATE = os.path.join(SCRIPTS, "delegate.py")
NOW = "2026-11-01T09:00:00+08:00"

TRIP = {
    "title": "测试行程", "map": "amap", "city": "苏州", "adcode": "320500",
    "tickets": [],
    "days": [
        {"id": "d1", "label": "D1", "iso_date": "2026-11-07", "weekday": "周六", "stops": [
            {"time": "09:00", "name": "苏州站", "type": "交通", "address": "苏站路27号",
             "lng": 120.601293, "lat": 31.330467, "status": "地图核实"},
            {"time": "10:00", "name": "苏州博物馆（本馆）", "type": "景点", "address": "东北街204号",
             "booking": True},
            {"time": "13:30", "name": "拙政园", "type": "景点", "address": "东北街178号"},
        ]},
        {"id": "d2", "label": "D2", "iso_date": "2026-11-08", "weekday": "周日", "stops": [
            {"time": "09:00", "name": "虎丘山风景名胜区", "type": "景点", "address": "虎丘山门内8号"},
        ]},
    ],
    "appendix": {"todo": [], "sources": [], "changelog": []},
}

# What the collector would have saved for each place (invented text, shaped like the real pages).
RAW_POI = {
    "苏州博物馆（本馆）": (
        '{"name":"苏州博物馆","id":"B020000AAA","address":"姑苏区东北街204号",'
        '"location":"120.627791,31.322879","opentime":"周二至周日 09:00-17:00 周一闭馆 16:00停止入场"}'),
    "拙政园": (
        '{"name":"拙政园","id":"B020000BBB","address":"姑苏区东北街178号",'
        '"location":"120.629970,31.324224","opentime":"每天 07:30-17:30"}'),
    "虎丘山风景名胜区": (
        '{"name":"虎丘山风景名胜区","id":"B020000CCC","address":"姑苏区虎丘山门内8号",'
        '"location":"120.579912,31.333730","opentime":"每天 07:30-17:00"}'),
    "狮子林": (
        '{"name":"狮子林","id":"B020000DDD","address":"姑苏区园林路23号",'
        '"location":"120.631000,31.320000","opentime":"每天 07:30-17:00"}'),
}
POI_MATCH = {
    "苏州博物馆（本馆）": {
        "name": "苏州博物馆", "amap_id": "B020000AAA", "address": "姑苏区东北街204号",
        "lng": 120.627791, "lat": 31.322879, "hours": "周二至周日 09:00-17:00",
        "hours_note": "周一闭馆",
        "hours_week": {"mon": [], **{d: [["09:00", "17:00"]] for d in
                                     ("tue", "wed", "thu", "fri", "sat", "sun")}}},
    "拙政园": {
        "name": "拙政园", "amap_id": "B020000BBB", "address": "姑苏区东北街178号",
        "lng": 120.62997, "lat": 31.324224, "hours": "每天 07:30-17:30",
        "hours_week": {d: [["07:30", "17:30"]] for d in
                       ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}},
    "虎丘山风景名胜区": {
        "name": "虎丘山风景名胜区", "amap_id": "B020000CCC", "address": "姑苏区虎丘山门内8号",
        "lng": 120.579912, "lat": 31.33373, "hours": "每天 07:30-17:00",
        "hours_week": {d: [["07:30", "17:00"]] for d in
                       ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}},
}
POI_EVIDENCE = {
    "苏州博物馆（本馆）": [
        {"file": "poiinfo.json", "field": "address", "quote": '"address":"姑苏区东北街204号"'},
        {"file": "poiinfo.json", "field": "hours", "quote": "周二至周日 09:00-17:00 周一闭馆"}],
    "拙政园": [
        {"file": "poiinfo.json", "field": "address", "quote": '"address":"姑苏区东北街178号"'},
        {"file": "poiinfo.json", "field": "hours", "quote": '"opentime":"每天 07:30-17:30"'}],
    "虎丘山风景名胜区": [
        {"file": "poiinfo.json", "field": "address", "quote": '"address":"姑苏区虎丘山门内8号"'},
        {"file": "poiinfo.json", "field": "hours", "quote": '"opentime":"每天 07:30-17:00"'}],
}
RAW_TICKET = (
    "URL: https://museum.example.org/visit\nFETCHED: 2026-11-01\n\n"
    "苏州博物馆参观须知（2026年3月更新）\n"
    "本馆实行实名预约参观，请通过苏州博物馆微信小程序预约。\n"
    "观众可预约7天内（含当日）的门票，每日09:00放出新一天的名额。\n"
    "每周一闭馆（法定节假日除外），16:00停止入场。门票免费。\n")
TICKET_RULE = {"required": True, "channel": "苏州博物馆微信小程序", "advance_days": 7,
               "release_time": "09:00", "same_day": None, "closed_days": ["周一"],
               "last_entry": "16:00", "eligibility": "", "price": "免费", "real_name": True}
TICKET_EVIDENCE = [
    {"file": "official.txt", "field": "required", "quote": "本馆实行实名预约参观，请通过苏州博物馆微信小程序预约"},
    {"file": "official.txt", "field": "channel", "quote": "请通过苏州博物馆微信小程序预约"},
    {"file": "official.txt", "field": "advance_days", "quote": "观众可预约7天内（含当日）的门票"},
    {"file": "official.txt", "field": "release_time", "quote": "每日09:00放出新一天的名额"},
    {"file": "official.txt", "field": "closed_days", "quote": "每周一闭馆（法定节假日除外）"},
    {"file": "official.txt", "field": "last_entry", "quote": "16:00停止入场"},
    {"file": "official.txt", "field": "price", "quote": "16:00停止入场。门票免费。"},
    {"file": "official.txt", "field": "real_name", "quote": "本馆实行实名预约参观"},
]


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)


class Case(unittest.TestCase):
    """A temp dir holding trip.json and work/, plus helpers to play the worker's part."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="trb-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.trip_path = os.path.join(self.dir, "trip.json")
        self.work = os.path.join(self.dir, "work")
        self.save_trip(copy.deepcopy(TRIP))

    # --- plumbing ---
    def run_cli(self, *args, now=NOW, expect=None):
        env = dict(os.environ, DELEGATE_NOW=now, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, DELEGATE, *args], capture_output=True, text=True,
                           env=env, cwd=self.dir)
        r.text = r.stdout + r.stderr
        self.assertNotIn("Traceback", r.text, f"delegate.py {' '.join(args)} crashed:\n{r.text}")
        if expect is not None:
            self.assertEqual(r.returncode, expect, r.text)
        return r

    def trip(self):
        return read(self.trip_path)

    def save_trip(self, trip):
        write(self.trip_path, trip)

    def init(self, *extra):
        return self.run_cli("init", self.trip_path, self.work, *extra)

    def merge(self, *extra, **kw):
        return self.run_cli("merge", self.trip_path, self.work, *extra, **kw)

    def check(self):
        return self.run_cli("check", self.work)

    def cards(self):
        out = []
        for p in sorted(glob.glob(os.path.join(self.work, "tasks", "*.json"))):
            c = read(p)
            if c.get("active", True):
                out.append(c)
        return out

    def card(self, kind, name):
        """The live card for a place, whatever its id is."""
        for c in self.cards():
            label = {"poi": (c.get("stop") or {}).get("name"), "ticket": c.get("venue"),
                     "xhs": c.get("candidate")}[c["kind"]]
            if c["kind"] == kind and label == name:
                return c
        self.fail(f"no {kind} card for {name}; have {[c['id'] for c in self.cards()]}")

    def stop(self, name, trip=None):
        for d in (trip or self.trip())["days"]:
            for s in d["stops"]:
                if s["name"] == name:
                    return s
        self.fail(f"no stop {name}")

    def raw_path(self, card, fname):
        return os.path.join(self.work, "raw", card["id"], fname)

    def out_path(self, card):
        return os.path.join(self.work, "out", card["id"] + ".json")

    def base_out(self, card, **fields):
        out = {"id": card["id"], "kind": card["kind"]}
        if card.get("fingerprint"):
            out["fingerprint"] = card["fingerprint"]
        out.update(fields)
        return out

    # --- playing the collector and the worker ---
    def answer_poi(self, name, raw_as=None, **override):
        """Save raw + a correct worker output for a POI card. raw_as: reuse another place's data."""
        src = raw_as or name
        card = self.card("poi", name)
        write(self.raw_path(card, "poiinfo.json"), RAW_POI[src])
        out = self.base_out(card, result="found", match=copy.deepcopy(POI_MATCH[src]), candidates=[],
                            evidence=copy.deepcopy(POI_EVIDENCE[src]), flags=[], confidence="high")
        out.update(override)
        write(self.out_path(card), out)
        return card, out

    def answer_ticket(self, name, rule=None, evidence=None, **override):
        card = self.card("ticket", name)
        write(self.raw_path(card, "official.txt"), RAW_TICKET)
        out = self.base_out(
            card, result="found", rule=dict(TICKET_RULE, **(rule or {})), rule_year="2026",
            unverified=["same_day"], sources=[{"file": "official.txt", "url": "https://museum.example.org/visit"}],
            evidence=copy.deepcopy(TICKET_EVIDENCE if evidence is None else evidence), flags=[],
            confidence="high")
        out.update(override)
        write(self.out_path(card), out)
        return card, out

    def status_of(self, card):
        """Task state as the pipeline sees it, across the old (check.json) and new (state.json) files."""
        self.run_cli("check", self.work)
        st = os.path.join(self.work, "state.json")
        if os.path.exists(st):
            return read(st)["tasks"][card["id"]]["state"]
        legacy = read(os.path.join(self.work, "check.json"))[card["id"]]["status"]
        return {"ok": "succeeded", "fail": "failed", "missing": "pending"}[legacy]
