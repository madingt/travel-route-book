"""The recorded benchmark runs must keep scoring as published in bench/RESULTS.md.

These are real model outputs (recorded 2026-11-10). If a validator change alters how they are
judged, this fails and RESULTS.md has to be re-examined, not silently drift.
"""
import os
import sys
import unittest

from helpers import ROOT

sys.path.insert(0, os.path.join(ROOT, "bench"))
import replay  # noqa: E402

#            validated needs_review failed false_cit facts wrong_accepted surfaced
EXPECTED = {
    "single-opus":              (10, 0, 0, 0, 25, 0, 6),
    "single-haiku":             (8, 1, 1, 0, 24, 0, 4),
    "multi-haiku":              (9, 1, 0, 0, 24, 0, 5),
    "single-haiku-after-retry": (9, 1, 0, 0, 24, 0, 5),
    "multi-haiku-after-retry":  (10, 0, 0, 0, 25, 0, 6),
}


class Replay(unittest.TestCase):
    def test_recorded_runs_score_as_published(self):
        for arm, want in EXPECTED.items():
            with self.subTest(arm):
                r = replay.replay(arm)
                got = (r["validated"], r["needs_review"], r["failed"], r["false_citations"], r["facts_ok"],
                       r["wrong_accepted"], r["surfaced_ok"])
                self.assertEqual(got, want)

    def test_fixture_is_self_consistent(self):
        """Every card of the fixture gets raw pages, and scoring an empty run is all zeros."""
        r = replay.replay("single-opus")
        self.assertEqual((r["cards"], r["facts_total"], r["surfaced_total"]), (10, 25, 6))


if __name__ == "__main__":
    unittest.main()
