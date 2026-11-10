#!/usr/bin/env python3
"""Build a fresh benchmark work dir: task cards + pre-collected raw pages, no outputs.

  python3 bench/make_work.py /tmp/run1        # → /tmp/run1/trip.json, /tmp/run1/work/

Collection is already done (bench/fixture/raw), so a run measures only the part the issue asks
about: turning saved pages into checked facts. Nothing here touches the network.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DELEGATE = os.path.join(HERE, "..", "travel-route-book", "scripts", "delegate.py")
FIX = os.path.join(HERE, "fixture")


def card_for(work, kind, name):
    for f in sorted(os.listdir(os.path.join(work, "tasks"))):
        with open(os.path.join(work, "tasks", f), encoding="utf-8") as fh:
            c = json.load(fh)
        label = (c.get("stop") or {}).get("name") or c.get("venue") or c.get("candidate")
        if c["kind"] == kind and label == name and c.get("active", True):
            return c
    raise SystemExit(f"no {kind} card for {name}")


def main(dest):
    if os.path.exists(dest) and os.listdir(dest):
        raise SystemExit(f"{dest} is not empty; pick a new directory")
    os.makedirs(dest, exist_ok=True)
    shutil.copy(os.path.join(FIX, "trip.json"), os.path.join(dest, "trip.json"))
    env = dict(os.environ, DELEGATE_NOW="2026-11-01T09:00:00+08:00")

    def run(*args):
        subprocess.run([sys.executable, DELEGATE, *args], cwd=dest, env=env, check=True,
                       stdout=subprocess.DEVNULL)

    run("init", "trip.json", "work")
    run("add-xhs", "work", "临河面馆", "--query", "临河面馆", "--category", "餐饮")
    run("add-xhs", "work", "半山咖啡", "--query", "半山咖啡", "--category", "咖啡", "--taste", "只喝黑咖啡")
    work = os.path.join(dest, "work")
    with open(os.path.join(FIX, "cards.json"), encoding="utf-8") as f:
        keys = json.load(f)
    urls = {"ticket-museum": ["https://www.qingshi-museum.example/visit/notice"],
            "ticket-pagoda": ["https://www.yunqi-pagoda.example/news/20230412"]}
    for key, urllist in urls.items():
        for u in urllist:
            run("add-source", "work", card_for(work, *keys[key])["id"], u)
    mapping = {}
    for key, (kind, name) in keys.items():
        card = card_for(work, kind, name)
        shutil.copytree(os.path.join(FIX, "raw", key), os.path.join(work, "raw", card["id"]))
        mapping[key] = card["id"]
    with open(os.path.join(dest, "mapping.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"{dest}: {len(mapping)} cards ready")
    for key, cid in mapping.items():
        print(f"  {cid}  {key}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__))
