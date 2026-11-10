"""Priority 0 (issue #2): task identity, stale outputs, evidence checks, merge idempotency.

Every test here reproduces a defect confirmed on PR #1 head (01da588) or pins behaviour that
must not regress. See docs/audit-issue-2.md for the recorded before/after runs.
"""
import copy
import os
import unittest

from helpers import Case, POI_MATCH, RAW_POI, TICKET_EVIDENCE, read, write


class TaskIdentity(Case):
    def test_replaced_stop_does_not_inherit_old_result(self):
        """Swap the place at a position, re-run init: the old place's result must not be merged."""
        self.init()
        self.answer_poi("拙政园")
        trip = self.trip()
        trip["days"][0]["stops"][2] = {"time": "13:30", "name": "狮子林", "type": "景点",
                                       "address": "园林路23号"}
        self.save_trip(trip)
        self.init()
        self.merge("--write")
        lion = self.stop("狮子林")
        self.assertNotEqual(lion.get("amap_id"), "B020000BBB", "狮子林 got 拙政园's POI id")
        self.assertNotIn("178", lion.get("address", ""), "狮子林 got 拙政园's address")
        self.assertIsNone(lion.get("hours_week"), "狮子林 got 拙政园's opening hours")

    def test_inserting_a_stop_keeps_results_on_the_right_place(self):
        """Insert a stop earlier in the day, re-run init: results follow the place, not the slot."""
        self.init()
        self.answer_poi("拙政园")
        trip = self.trip()
        trip["days"][0]["stops"].insert(1, {"time": "09:30", "name": "狮子林", "type": "景点",
                                            "address": "园林路23号"})
        self.save_trip(trip)
        self.init()
        self.merge("--write")
        for name in ("狮子林", "苏州博物馆（本馆）"):
            self.assertNotEqual(self.stop(name).get("amap_id"), "B020000BBB",
                                f"{name} got 拙政园's result after an insert shifted positions")
        self.assertEqual(self.stop("拙政园").get("amap_id"), "B020000BBB",
                         "拙政园 lost its own, still valid, result")

    def test_changed_address_hint_invalidates_result(self):
        """Same name, different address (another branch): the old result is for the wrong shop."""
        self.init()
        self.answer_poi("拙政园")
        trip = self.trip()
        self.stop("拙政园", trip)["address"] = "工业园区星湖街999号"
        self.save_trip(trip)
        self.init()
        self.merge("--write")
        self.assertNotEqual(self.stop("拙政园").get("amap_id"), "B020000BBB")

    def test_xhs_result_is_stale_when_taste_filter_changes(self):
        self.run_cli("init", self.trip_path, self.work)
        self.run_cli("add-xhs", self.work, "某某面馆", "--query", "苏州 面馆")
        card = self.card("xhs", "某某面馆")
        write(self.raw_path(card, "note-1.txt"), "URL: x\nAUTHOR: 甲\n---BODY---\n两个人点了焖肉面和爆鱼面，人均三十五，汤头偏甜\n")
        write(self.out_path(card), self.base_out(
            card, candidate="某某面馆", result="graded", independent_positive=1, red_flags=[],
            taste_filter_hits=[], planner_note="1 篇独立好评", confidence="medium",
            notes=[{"file": "note-1.txt", "author": "甲", "is_marketing": False, "marketing_hits": [],
                    "stance": "正面", "real_signals": [{"file": "note-1.txt", "quote": "两个人点了焖肉面和爆鱼面，人均三十五"}]}]))
        self.assertEqual(self.status_of(card), "succeeded")
        self.run_cli("add-xhs", self.work, "某某面馆", "--query", "苏州 面馆", "--taste", "不吃甜")
        card2 = self.card("xhs", "某某面馆")
        self.assertNotEqual(self.status_of(card2), "succeeded",
                            "grading done without the taste filter was accepted for the new card")

    def test_same_place_twice_is_researched_once(self):
        """The station appears on both days; one lookup must serve both visits."""
        trip = self.trip()
        for d in trip["days"]:
            d["stops"].append({"time": "18:00", "name": "平江路历史街区", "type": "景点", "address": "平江路"})
        self.save_trip(trip)
        self.init()
        cards = [c for c in self.cards() if c["kind"] == "poi" and c["stop"]["name"] == "平江路历史街区"]
        self.assertEqual(len(cards), 1, "one place, one task")


class MainBranchRules(Case):
    def test_default_xhs_queries_are_neutral(self):
        """main (477e046) says: search with neutral words, never lead with 「避雷」."""
        self.init()
        self.run_cli("add-xhs", self.work, "某某面馆")
        with open(os.path.join(self.work, "collect.jsonl"), encoding="utf-8") as f:
            self.assertNotIn("避雷", f.read())


class StaleTripState(Case):
    def test_merge_survives_a_removed_stop(self):
        """Delete a stop after init (no re-init): merge must skip the orphan, not crash."""
        self.init()
        self.answer_poi("虎丘山风景名胜区")
        self.answer_poi("拙政园")
        trip = self.trip()
        del trip["days"][0]["stops"][2]
        trip["days"].pop()
        self.save_trip(trip)
        self.merge("--write", expect=0)


class Evidence(Case):
    def setUp(self):
        super().setUp()
        self.init()

    def test_found_without_any_evidence_is_rejected(self):
        """Address, coordinates and POI id made up, nothing cited."""
        m = {"name": "拙政园", "amap_id": "B0FAKE0000", "address": "编造路1号", "lng": 121.0, "lat": 31.0}
        card, _ = self.answer_poi("拙政园", match=m, evidence=[])
        self.assertEqual(self.status_of(card), "failed")

    def test_citation_of_a_missing_file_is_rejected(self):
        card, out = self.answer_poi("拙政园")
        for e in out["evidence"]:
            e["file"] = "detail-does-not-exist.txt"
        write(self.out_path(card), out)
        self.assertEqual(self.status_of(card), "failed")

    def test_trivial_quote_is_rejected(self):
        """A one-character 'quote' is verbatim in any page and proves nothing."""
        card, out = self.answer_poi("拙政园")
        out["match"]["hours"] = "每天 00:00-24:00"
        out["match"]["hours_week"] = {"sat": [["00:00", "24:00"]]}
        out["evidence"] = [{"file": "poiinfo.json", "field": "hours", "quote": "0"},
                           {"file": "poiinfo.json", "field": "address", "quote": "号"}]
        write(self.out_path(card), out)
        self.assertEqual(self.status_of(card), "failed")

    def test_coordinates_not_in_raw_are_rejected(self):
        card, out = self.answer_poi("拙政园")
        out["match"]["lng"], out["match"]["lat"] = 120.5, 31.5
        write(self.out_path(card), out)
        self.assertEqual(self.status_of(card), "failed")

    def test_fabricated_quote_is_rejected(self):
        card, out = self.answer_poi("拙政园")
        out["evidence"][1]["quote"] = "营业时间：每天 06:00-22:00，全年无休"
        write(self.out_path(card), out)
        self.assertEqual(self.status_of(card), "failed")

    def test_real_quote_that_does_not_support_the_value_is_not_merged(self):
        """advance_days=30 'backed' by a genuine sentence that says nothing of the sort."""
        ev = copy.deepcopy(TICKET_EVIDENCE)
        ev[2] = {"file": "official.txt", "field": "advance_days", "quote": "每周一闭馆（法定节假日除外）"}
        card, _ = self.answer_ticket("苏州博物馆（本馆）", rule={"advance_days": 30}, evidence=ev)
        self.assertNotEqual(self.status_of(card), "succeeded")
        self.merge("--write")
        self.assertNotEqual((self.stop("苏州博物馆（本馆）").get("booking_rule") or {}).get("advance_days"), 30)

    def test_wrong_shapes_fail_validation_instead_of_crashing_later(self):
        """Partial or mistyped outputs must be caught by check, never reach digest/merge."""
        bad = [
            ("candidates of non-objects", "poi", {"result": "ambiguous", "match": None, "candidates": [1, 2]}),
            ("unverified of non-strings", "ticket", {"unverified": [1]}),
            ("sources of non-objects", "ticket", {"sources": ["official.txt"]}),
            ("closed_days as a string", "ticket", {"rule": {"closed_days": "周一"}}),
            ("flags as a number", "poi", {"flags": 3}),
        ]
        for label, kind, patch in bad:
            with self.subTest(label):
                if kind == "poi":
                    card, out = self.answer_poi("拙政园")
                else:
                    card, out = self.answer_ticket("苏州博物馆（本馆）")
                for k, v in patch.items():
                    if isinstance(v, dict) and isinstance(out.get(k), dict):
                        out[k].update(v)
                    else:
                        out[k] = v
                write(self.out_path(card), out)
                self.assertEqual(self.status_of(card), "failed", label)
                self.run_cli("digest", self.work)
                self.merge()

    def test_truncated_json_is_a_failure_not_a_crash(self):
        card, _ = self.answer_poi("拙政园")
        with open(self.out_path(card), encoding="utf-8") as f:
            text = f.read()
        write(self.out_path(card), text[: len(text) // 2])
        self.assertEqual(self.status_of(card), "failed")
        self.merge("--write", expect=0)
        self.assertIsNone(self.stop("拙政园").get("amap_id"))

    def test_valid_output_still_passes(self):
        """Guard against over-tightening: the documented happy path stays green."""
        card, _ = self.answer_poi("拙政园")
        self.assertEqual(self.status_of(card), "succeeded")
        tcard, _ = self.answer_ticket("苏州博物馆（本馆）")
        self.assertEqual(self.status_of(tcard), "succeeded")


class MergeIdempotency(Case):
    def setUp(self):
        super().setUp()
        self.init()
        self.answer_poi("拙政园")
        self.answer_poi("虎丘山风景名胜区")
        self.answer_poi("苏州博物馆（本馆）")
        self.answer_ticket("苏州博物馆（本馆）")
        with open(self.trip_path, "rb") as f:
            self.original = f.read()

    def bytes(self, path=None):
        with open(path or self.trip_path, "rb") as f:
            return f.read()

    def backups(self):
        found = []
        for base, _, files in os.walk(self.dir):
            found += [os.path.join(base, f) for f in files if ".bak" in f or "backups" in base]
        return found

    def test_merge_applies_results(self):
        self.merge("--write", expect=0)
        z = self.stop("拙政园")
        self.assertEqual((z["amap_id"], z["lng"], z["status"]), ("B020000BBB", 120.62997, "地图核实"))
        self.assertEqual(self.stop("苏州博物馆（本馆）")["booking_rule"]["advance_days"], 7)
        t = self.trip()["tickets"]
        self.assertEqual(len(t), 1)
        self.assertIn("10/31", t[0]["when"])   # 2026-11-07 minus 7 days
        self.assertIn("09:00", t[0]["when"])

    def test_second_merge_changes_nothing(self):
        self.merge("--write")
        first = self.bytes()
        self.merge("--write")
        self.assertEqual(self.bytes(), first)

    def test_merge_on_a_later_day_changes_nothing(self):
        """Re-running tomorrow with nothing new must not grow the changelog."""
        self.merge("--write", now="2026-11-01T09:00:00+08:00")
        first = self.bytes()
        self.merge("--write", now="2026-11-02T09:00:00+08:00")
        self.assertEqual(self.bytes(), first)

    def test_original_survives_repeated_merges(self):
        """The pre-merge trip.json must still be recoverable after merging twice."""
        self.merge("--write")
        self.merge("--write")
        self.assertTrue(any(self.bytes(p) == self.original for p in self.backups()),
                        "no backup holds the original trip.json any more")

    def test_merge_is_order_independent(self):
        """Same inputs, cards created in a different order: identical trip.json."""
        self.merge("--write")
        a = self.bytes()
        other = Case("setUp"); other.setUp(); self.addCleanup(other.doCleanups)
        trip = other.trip()
        other.save_trip(trip)
        other.init()
        for name in ("苏州博物馆（本馆）", "虎丘山风景名胜区", "拙政园"):
            other.answer_poi(name)
        other.answer_ticket("苏州博物馆（本馆）")
        other.merge("--write")
        with open(other.trip_path, "rb") as f:
            self.assertEqual(f.read(), a)

    def test_dry_run_writes_nothing(self):
        self.merge()
        self.assertEqual(self.bytes(), self.original)
        self.assertEqual(self.backups(), [])

    def test_handwritten_ticket_for_another_venue_is_kept(self):
        """'苏州博物馆西馆' is a different venue from '苏州博物馆（本馆）'."""
        trip = self.trip()
        hand = {"when": "出发前 3 天", "platform": "官方小程序", "what": "预约 D2 苏州博物馆西馆"}
        trip["tickets"] = [hand]
        self.save_trip(trip)
        self.merge("--write")
        self.assertIn(hand, self.trip()["tickets"], "hand-written entry for another venue was overwritten")

    def test_two_places_resolving_to_one_poi_are_flagged(self):
        """Two differently named stops matched to the same map POI: one of them is wrong."""
        trip = self.trip()
        trip["days"][1]["stops"].append({"time": "14:00", "name": "忠王府", "type": "景点", "address": "东北街"})
        self.save_trip(trip)
        self.init()
        card = self.card("poi", "忠王府")
        write(self.raw_path(card, "poiinfo.json"), RAW_POI["拙政园"])
        write(self.out_path(card), self.base_out(
            card, result="found", match=copy.deepcopy(POI_MATCH["拙政园"]), candidates=[], flags=[],
            confidence="high", evidence=[
                {"file": "poiinfo.json", "field": "address", "quote": '"address":"姑苏区东北街178号"'},
                {"file": "poiinfo.json", "field": "hours", "quote": '"opentime":"每天 07:30-17:30"'}]))
        r = self.merge("--write")
        todo = " ".join(self.trip()["appendix"]["todo"])
        self.assertTrue("忠王府" in todo and "拙政园" in todo and "同一" in todo,
                        f"duplicate POI not flagged:\n{r.text}\n{todo}")


class DateConflicts(Case):
    def conflicts(self, trip, *extra):
        self.save_trip(trip)
        return self.run_cli("conflicts", self.trip_path, "--json", *extra)

    def test_iso_date_and_weekday_disagree(self):
        trip = self.trip()
        trip["days"][0]["weekday"] = "周日"      # 2026-11-07 is a Saturday
        r = self.conflicts(trip)
        self.assertEqual(r.returncode, 1, r.text)
        self.assertIn("周六", r.stdout)

    def test_invalid_iso_date_is_reported(self):
        trip = self.trip()
        trip["days"][0]["iso_date"] = "2026-11-31"
        r = self.conflicts(trip)
        self.assertEqual(r.returncode, 1, r.text)
        self.assertIn("iso_date", r.stdout)

    def test_days_out_of_order_are_reported(self):
        trip = self.trip()
        trip["days"][1]["iso_date"] = "2026-11-06"
        trip["days"][1]["weekday"] = "周五"
        r = self.conflicts(trip)
        self.assertIn("早于", r.stdout)

    def test_closed_day_is_an_error(self):
        """Pin existing behaviour: visiting on the weekly closed day is caught."""
        trip = self.trip()
        trip["days"][0].update(iso_date="2026-11-09", weekday="周一")
        self.stop("苏州博物馆（本馆）", trip)["booking_rule"] = {"closed_days": ["周一"]}
        r = self.conflicts(trip)
        self.assertEqual(r.returncode, 1)
        self.assertIn("闭馆", r.stdout)

    def test_booking_rule_contradicts_map_hours(self):
        """Official rule says closed Monday, map hours say open Monday: surface it."""
        trip = self.trip()
        s = self.stop("苏州博物馆（本馆）", trip)
        s["booking_rule"] = {"closed_days": ["周一"]}
        s["hours_week"] = {d: [["09:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
        r = self.conflicts(trip)
        self.assertIn("不一致", r.stdout)


if __name__ == "__main__":
    unittest.main()
