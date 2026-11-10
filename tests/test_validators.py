"""Unit tests for the deterministic value-in-quote helpers (no files, no CLI)."""
import sys
import unittest

from helpers import SCRIPTS

sys.path.insert(0, SCRIPTS)
import delegate as d  # noqa: E402


class SaysTime(unittest.TestCase):
    def test_spellings_that_count(self):
        for text, value in [
            ("开放时间09:00-17:00", "09:00"), ("开放时间9:00-17:00", "09:00"), ("开放时间9：00—17：00", "17:00"),
            ("每日9点放号", "09:00"), ("每日九点放号", "09:00"), ("下午5点停止入场", "17:00"),
            ("16点半停止检票", "16:30"), ("下午4:30停止检票", "16:30"), ("营业至次日02:00", "26:00"),
            ("营业至凌晨2点", "26:00"), ("24小时营业", "00:00"), ("24小时营业", "24:00"), ("十点开门", "10:00"),
        ]:
            with self.subTest(text=text, value=value):
                self.assertTrue(d.says_time(d.flat(text), value))

    def test_lookalikes_that_do_not(self):
        for text, value in [
            ("开放时间19:00-22:00", "09:00"),     # 9:00 inside 19:00
            ("开放时间09:30-17:00", "09:00"), ("9点半放号", "09:00"), ("提前9天预约", "09:00"),
            ("19点放号", "09:00"), ("每周一闭馆", "16:00"), ("", "09:00"), ("09:00放号", "not a time"),
        ]:
            with self.subTest(text=text, value=value):
                self.assertFalse(d.says_time(d.flat(text), value))


class SaysNumber(unittest.TestCase):
    def test_numbers(self):
        self.assertTrue(d.says_number("可预约7天内的门票", 7))
        self.assertTrue(d.says_number("可预约七天内的门票", 7))
        self.assertTrue(d.says_number("提前十四天", 14))
        self.assertTrue(d.says_number("提前两天", 2))
        self.assertFalse(d.says_number("可预约17天内的门票", 7))
        self.assertFalse(d.says_number("每周一闭馆", 30))


class Identity(unittest.TestCase):
    TRIP = {"map": "amap", "city": "苏州", "adcode": "320500"}

    def ident(self, stop, trip=None):
        return d.card_ids("poi", d.poi_input(trip or self.TRIP, stop))

    def test_width_and_spacing_do_not_change_identity(self):
        a = self.ident({"name": "苏州博物馆（本馆）", "address": "东北街204号"})
        b = self.ident({"name": "苏州博物馆(本馆) ", "address": "东北街 204号".replace(" ", "")})
        self.assertEqual(a, b)

    def test_each_input_changes_identity(self):
        base = self.ident({"name": "拙政园", "address": "东北街178号"})
        self.assertNotEqual(base, self.ident({"name": "拙政园", "address": "星湖街999号"}))
        self.assertNotEqual(base, self.ident({"name": "狮子林", "address": "东北街178号"}))
        self.assertNotEqual(base, self.ident({"name": "拙政园", "address": "东北街178号"}, dict(self.TRIP, city="上海")))

    def test_position_and_time_are_not_identity(self):
        a = self.ident({"name": "拙政园", "address": "东北街178号", "time": "09:00"})
        b = self.ident({"name": "拙政园", "address": "东北街178号", "time": "15:00", "note": "x"})
        self.assertEqual(a, b)

    def test_schema_version_is_in_the_fingerprint(self):
        key = d.poi_input(self.TRIP, {"name": "拙政园"})
        _, fp = d.card_ids("poi", key)
        old, d.SCHEMA = d.SCHEMA, d.SCHEMA + 1
        try:
            self.assertNotEqual(fp, d.card_ids("poi", key)[1])
        finally:
            d.SCHEMA = old

    def test_ticket_identity_follows_venue_and_date(self):
        day = {"id": "d1", "iso_date": "2026-11-07"}
        a = d.card_ids("ticket", d.ticket_key(self.TRIP, day, {"name": "苏州博物馆"}))
        b = d.card_ids("ticket", d.ticket_key(self.TRIP, dict(day, id="d9"), {"name": "苏州博物馆", "time": "15:00"}))
        c = d.card_ids("ticket", d.ticket_key(self.TRIP, dict(day, iso_date="2026-11-08"), {"name": "苏州博物馆"}))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
