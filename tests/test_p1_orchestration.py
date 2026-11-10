"""Priority 1 (issue #2): explicit task states, durable checkpoint, safe retries, human gates."""
import copy
import json
import os

from helpers import Case, TICKET_EVIDENCE, read, write

T0 = "2026-11-01T09:00:00+08:00"
T_PLUS_10 = "2026-11-01T09:10:00+08:00"
T_PLUS_60 = "2026-11-01T10:00:00+08:00"


class States(Case):
    def setUp(self):
        super().setUp()
        self.init()
        self.card_z = self.card("poi", "拙政园")

    def state(self, card=None, now=T0):
        self.run_cli("check", self.work, now=now)
        return read(os.path.join(self.work, "state.json"))["tasks"][(card or self.card_z)["id"]]

    def events(self):
        with open(os.path.join(self.work, "events.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def break_out(self, card, quote):
        out = read(self.out_path(card))
        out["evidence"][1]["quote"] = quote
        write(self.out_path(card), out)

    def test_lifecycle_pending_running_succeeded(self):
        self.assertEqual(self.state()["state"], "pending")
        self.run_cli("claim", self.work, self.card_z["id"], "--owner", "w1", expect=0)
        self.assertEqual(self.state()["state"], "running")
        self.answer_poi("拙政园")
        st = self.state()
        self.assertEqual((st["state"], st["attempts"]), ("succeeded", 1))
        self.assertEqual([(e["from"], e["to"]) for e in self.events() if e["task"] == self.card_z["id"]],
                         [(None, "pending"), ("pending", "running"), ("running", "succeeded")])

    def test_a_running_task_cannot_be_claimed_twice(self):
        self.run_cli("claim", self.work, self.card_z["id"], "--owner", "w1", expect=0)
        r = self.run_cli("claim", self.work, self.card_z["id"], "--owner", "w2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("w1", r.text)

    def test_a_dead_worker_frees_the_task_and_counts_as_an_attempt(self):
        self.run_cli("claim", self.work, self.card_z["id"], "--minutes", "20", now=T0, expect=0)
        self.assertEqual(self.state(now=T_PLUS_10)["state"], "running")
        st = self.state(now=T_PLUS_60)
        self.assertEqual((st["state"], st["attempts"]), ("pending", 1))
        self.run_cli("claim", self.work, self.card_z["id"], now=T_PLUS_60, expect=0)

    def test_failed_then_retry_succeeds(self):
        self.answer_poi("拙政园")
        self.break_out(self.card_z, "营业时间：每天 06:00-22:00，全年无休")
        st = self.state()
        self.assertEqual((st["state"], st["attempts"]), ("failed", 1))
        self.assertIn("QUOTE_NOT_FOUND", st["reasons"])
        r = self.run_cli("pending", self.work)
        self.assertIn("poi-checker", r.stdout)
        self.assertIn("QUOTE_NOT_FOUND", r.stdout)      # the reason goes back to the worker
        self.answer_poi("拙政园")
        st = self.state()
        self.assertEqual((st["state"], st["attempts"]), ("succeeded", 2))

    def test_two_failures_escalate_to_needs_review(self):
        self.answer_poi("拙政园")
        self.break_out(self.card_z, "营业时间：每天 06:00-22:00，全年无休")
        self.state()
        self.break_out(self.card_z, "营业时间：每天 05:00-23:00，全年无休")
        st = self.state()
        self.assertEqual(st["state"], "needs_review")
        self.assertIn("RETRIES_EXHAUSTED", st["reasons"])
        self.assertNotIn("poi-checker", self.run_cli("pending", self.work).stdout.split("needs_review")[0])

    def test_rechecking_the_same_output_is_not_a_new_attempt(self):
        self.answer_poi("拙政园")
        self.break_out(self.card_z, "营业时间：每天 06:00-22:00，全年无休")
        for _ in range(4):
            st = self.state()
        self.assertEqual((st["state"], st["attempts"]), ("failed", 1))

    def test_checkpoint_is_stable_and_survives_a_crash_mid_write(self):
        """Re-running check later changes nothing on disk; a stray temp file does no harm."""
        self.answer_poi("拙政园")
        self.state(now=T0)
        path = os.path.join(self.work, "state.json")
        with open(path, "rb") as f:
            first = f.read()
        write(path + ".tmp999", '{"tasks": {"half-writ')
        self.state(now=T_PLUS_60)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), first)

    def test_lost_checkpoint_is_rebuilt_from_files(self):
        self.answer_poi("拙政园")
        self.state()
        os.remove(os.path.join(self.work, "state.json"))
        self.assertEqual(self.state()["state"], "succeeded")
        write(os.path.join(self.work, "state.json"), "{ not json")
        self.assertEqual(self.state()["state"], "succeeded")

    def test_review_level_problem_waits_for_approval(self):
        ev = copy.deepcopy(TICKET_EVIDENCE)
        ev[2] = {"file": "official.txt", "field": "advance_days", "quote": "每周一闭馆（法定节假日除外）"}
        card, _ = self.answer_ticket("苏州博物馆（本馆）", rule={"advance_days": 30}, evidence=ev)
        st = self.state(card)
        self.assertEqual(st["state"], "needs_review")
        self.assertIn("VALUE_NOT_IN_QUOTE", st["reasons"])
        self.merge("--write")
        self.assertIsNone(self.stop("苏州博物馆（本馆）").get("booking_rule"))
        self.run_cli("approve", self.work, card["id"], "--by", "tester", "--note", "checked the page myself", expect=0)
        self.assertEqual(self.state(card)["state"], "succeeded")
        self.merge("--write")
        self.assertEqual(self.stop("苏州博物馆（本馆）")["booking_rule"]["advance_days"], 30)

    def test_approval_does_not_carry_over_to_a_different_output(self):
        ev = copy.deepcopy(TICKET_EVIDENCE)
        ev[2] = {"file": "official.txt", "field": "advance_days", "quote": "每周一闭馆（法定节假日除外）"}
        card, _ = self.answer_ticket("苏州博物馆（本馆）", rule={"advance_days": 30}, evidence=ev)
        self.state(card)
        self.run_cli("approve", self.work, card["id"], "--by", "tester", expect=0)
        self.answer_ticket("苏州博物馆（本馆）", rule={"advance_days": 60}, evidence=ev)
        self.assertEqual(self.state(card)["state"], "needs_review")

    def test_hard_errors_cannot_be_approved(self):
        self.answer_poi("拙政园")
        self.break_out(self.card_z, "营业时间：每天 06:00-22:00，全年无休")
        self.state()
        self.assertEqual(self.run_cli("approve", self.work, self.card_z["id"], "--by", "tester").returncode, 1)
        self.assertEqual(self.state()["state"], "failed")

    def test_login_wall_needs_a_human_and_is_never_dispatched(self):
        """The collector hit a sign-in page: only the user can clear that."""
        tcard = self.card("ticket", "苏州博物馆（本馆）")
        write(os.path.join(self.work, "raw", "_blocked.txt"), f"page {tcard['id']} https://x.example 需要登录\n")
        st = self.state(tcard)
        self.assertEqual(st["state"], "needs_review")
        self.assertIn("HUMAN_ACTION_REQUIRED", st["reasons"])
        self.assertEqual(self.run_cli("claim", self.work, tcard["id"]).returncode, 1)
        self.run_cli("unblock", self.work, tcard["id"], expect=0)
        self.assertEqual(self.state(tcard)["state"], "pending")

    def test_status_reports_every_state(self):
        self.answer_poi("拙政园")
        r = self.run_cli("status", self.work, "--json", expect=0)
        data = json.loads(r.stdout)
        self.assertEqual(data["counts"]["succeeded"], 1)
        self.assertEqual(set(data["counts"]), {"pending", "running", "succeeded", "failed", "needs_review"})

    def test_check_exit_code(self):
        self.assertEqual(self.check().returncode, 1)
        for name in ("拙政园", "虎丘山风景名胜区", "苏州博物馆（本馆）"):
            self.answer_poi(name)
        self.answer_ticket("苏州博物馆（本馆）")
        self.assertEqual(self.check().returncode, 0)


class Resume(Case):
    def test_interrupted_run_resumes_without_redoing_finished_work(self):
        """Half the cards done, process dies, run again: finished cards are not re-dispatched."""
        self.init()
        self.answer_poi("拙政园")
        first = self.run_cli("pending", self.work).stdout
        done, todo = self.card("poi", "拙政园")["id"], self.card("poi", "虎丘山风景名胜区")["id"]
        self.assertNotIn(done, first)
        self.assertIn(todo, first)
        self.init()                                    # re-running init is harmless
        self.assertEqual(self.run_cli("pending", self.work).stdout, first)
        self.answer_poi("虎丘山风景名胜区")
        self.assertNotIn(todo, self.run_cli("pending", self.work).stdout)
