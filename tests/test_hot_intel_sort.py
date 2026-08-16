"""13.0.7：热门信息按热度倒序，支持多维度排序。"""
from __future__ import annotations

import unittest

from app.database import execute, init_db
from app.services import intel_ai


class HotIntelSortTests(unittest.TestCase):
    PREFIX = "__t1307_"

    @classmethod
    def setUpClass(cls):
        init_db()
        intel_ai.ensure_table()

    def tearDown(self):
        execute("DELETE FROM intel_item_ai WHERE ident LIKE ?", (self.PREFIX + "%",))
        execute("DELETE FROM intel_item_ai WHERE item_key LIKE ?", ("news:" + self.PREFIX + "%",))

    def _save(self, ident: str, heat, attention):
        intel_ai.save(
            {"source": "news", "ident": self.PREFIX + ident, "title": ident,
             "attention": attention, "heat": heat, "time": f"2026-08-0{ident[-1:] if ident[-1:].isdigit() else 1}"},
            {"reading": "测试解读足够长。", "bull": [{"name": "银行"}], "bear": [],
             "keywords": ["测试"], "source": "测试写入"},
        )

    def _idents(self, sort: str, order: str) -> list[str]:
        items = intel_ai.list_hot_intel(sort=sort, order=order, limit=200).get("items") or []
        return [c.get("ident") for c in items if str(c.get("ident") or "").startswith(self.PREFIX)]

    def test_default_heat_desc_nulls_last_and_attention_tiebreak(self):
        self._save("a", 10, 1)
        self._save("b", 90, 1)
        self._save("c", None, 80)
        self._save("d", 90, 50)
        d = intel_ai.list_hot_intel(limit=200)
        self.assertEqual(d.get("sort"), "heat")
        self.assertEqual(d.get("order"), "desc")
        ids = [s["id"] for s in (d.get("sorts") or [])]
        self.assertEqual(ids, ["heat", "attention", "updated_at", "event_time"])
        self.assertEqual(self._idents("heat", "desc"), [
            self.PREFIX + "d", self.PREFIX + "b", self.PREFIX + "a", self.PREFIX + "c",
        ])

    def test_attention_desc_and_heat_asc(self):
        self._save("a", 10, 1)
        self._save("b", 90, 1)
        self._save("c", None, 80)
        self._save("d", 90, 50)
        self.assertEqual(self._idents("attention", "desc"), [
            self.PREFIX + "c", self.PREFIX + "d", self.PREFIX + "b", self.PREFIX + "a",
        ])
        # 热度正序：缺值排后；10 先于 90，同热度 90 时关注度升序 b(1) 再 d(50)
        self.assertEqual(self._idents("heat", "asc"), [
            self.PREFIX + "a", self.PREFIX + "b", self.PREFIX + "d", self.PREFIX + "c",
        ])

    def test_invalid_sort_falls_back_to_heat_desc(self):
        d = intel_ai.list_hot_intel(sort="nope", order="weird")
        self.assertEqual(d.get("sort"), "heat")
        self.assertEqual(d.get("order"), "desc")


if __name__ == "__main__":
    unittest.main()
