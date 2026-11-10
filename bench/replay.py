#!/usr/bin/env python3
"""Re-score the recorded benchmark runs offline (no model calls).

  python3 bench/replay.py            # every arm under bench/recorded/
  python3 bench/replay.py multi-haiku

Rebuilds the work dir from the fixture, drops in the outputs the agents actually wrote on the
recorded run, and scores them with today's validators. Use it to see how a validator change
would have judged real worker output.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_work  # noqa: E402
import score  # noqa: E402


def replay(arm):
    tmp = tempfile.mkdtemp(prefix="trb-bench-")
    try:
        run = os.path.join(tmp, "run")
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            make_work.main(run)
        src = os.path.join(HERE, "recorded", arm, "out")
        os.makedirs(os.path.join(run, "work", "out"), exist_ok=True)
        for f in os.listdir(src):
            shutil.copy(os.path.join(src, f), os.path.join(run, "work", "out", f))
        return score.score(run)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    arms = sys.argv[1:] or sorted(os.listdir(os.path.join(HERE, "recorded")))
    print(f"{'arm':26} validated failed review falsecite facts  wrong-accepted surfaced")
    for arm in arms:
        r = replay(arm)
        print(f"{arm:26} {r['validated']:>4}/{r['cards']:<4} {r['failed']:>5} {r['needs_review']:>6} {r['false_citations']:>9} "
              f"{r['facts_ok']:>2}/{r['facts_total']:<3} {r['wrong_accepted']:>13} {r['surfaced_ok']:>4}/{r['surfaced_total']}")
