"""13.0.2：宏观情报全部菜单可落库 AI；失败不覆盖；热门个股 TOP20/30/50。"""
from __future__ import annotations

import unittest

from app.database import execute, init_db
from app.services import hot_terms, intel_ai


class IntelAiMenuTests(unittest.TestCase):
    KEY_K = "knowledge:__t1302_量比"
    TERM = "__t1302_降准"

    @classmethod
    def setUpClass(cls):
        init_db()
        intel_ai.ensure_table()

    def tearDown(self):
        execute("DELETE FROM intel_item_ai WHERE item_key LIKE ?", ("knowledge:__t1302_%",))
        execute("DELETE FROM intel_item_ai WHERE item_key LIKE ?", ("hot_term:__t1302_%",))
        execute("DELETE FROM hot_term_ai WHERE term LIKE ?", ("__t1302_%",))

    def test_all_macro_sources_allowed(self):
        for src in ("news", "outlook", "calendar", "sector_event", "policy",
                    "official", "announce", "hot_term", "knowledge", "holders", "major"):
            self.assertIn(src, intel_ai.ALLOWED_SOURCES, src)
        self.assertFalse(intel_ai.SKIP_MENUS)
        self.assertEqual(intel_ai.SOURCE_LABELS["knowledge"], "股票常识")

    def test_knowledge_save_index_and_keep_on_empty_patch(self):
        rec = intel_ai.save(
            {"source": "knowledge", "ident": "__t1302_量比", "title": "量比",
             "text": "量比：当日成交量相对过去5日"},
            {"reading": "量比放大说明交投活跃。", "keywords": ["量比", "成交量"],
             "bull": [{"name": "券商", "why": "交投活跃"}],
             "bear": [], "reason": "量能观察", "source": "测试写入"},
        )
        self.assertTrue(rec.get("applied"))
        self.assertEqual(rec.get("reading"), "量比放大说明交投活跃。")
        self.assertIn("量比", rec.get("keywords") or [])
        pub = intel_ai._row_public(rec)  # noqa: SLF001
        self.assertEqual(pub.get("ident"), "__t1302_量比")
        idx = intel_ai.index()["items"]
        self.assertIn(self.KEY_K, idx)
        self.assertTrue(idx[self.KEY_K].get("has_reading"))
        hot = intel_ai.list_hot_intel("knowledge")
        titles = [c.get("title") for c in hot.get("items") or []]
        self.assertIn("量比", titles)
        ident_ok = any(c.get("ident") == "__t1302_量比" for c in (hot.get("items") or []))
        self.assertTrue(ident_ok)
        intel_ai.save(
            {"source": "knowledge", "ident": "__t1302_量比", "title": "量比"},
            {"reading": "", "keywords": [], "bull": [], "bear": []},
        )
        kept = intel_ai.load(self.KEY_K)
        self.assertEqual(kept.get("reading"), "量比放大说明交投活跃。")
        self.assertIn("量比", kept.get("keywords") or [])

    def test_hot_term_reading_syncs_overlay(self):
        intel_ai._merge_hot_term_overlay(self.TERM, {  # noqa: SLF001
            "bull": [{"name": "银行", "why": "流动性"}],
            "bear": [],
            "reading": "降准偏利好银行。",
            "keywords": ["降准", "银行"],
            "reason": "流动性",
            "source": "测试写入",
        })
        ov = hot_terms._ai_overlay(self.TERM)  # noqa: SLF001
        self.assertIsNotNone(ov)
        self.assertEqual(ov.get("reading"), "降准偏利好银行。")
        packed, meta = hot_terms._with_ai_overlay(self.TERM, [])  # noqa: SLF001
        self.assertTrue(meta.get("ai_applied"))
        self.assertEqual(meta.get("ai_reading"), "降准偏利好银行。")
        intel_ai.save(
            {"source": "hot_term", "ident": self.TERM, "title": self.TERM, "text": "降准"},
            {"reading": "降准偏利好银行。", "keywords": ["降准"], "source": "测试写入"},
        )
        cards = intel_ai.list_hot_intel("hot_term").get("items") or []
        self.assertTrue(any(c.get("ident") == self.TERM or c.get("title") == self.TERM for c in cards))
        reverted = hot_terms.revert_hot_term_ai(self.TERM)
        self.assertTrue(reverted.get("reverted"))
        self.assertIsNone(hot_terms._ai_overlay(self.TERM))  # noqa: SLF001
        self.assertIsNone(intel_ai.load(intel_ai.make_key("hot_term", self.TERM)))

    def test_hot_sector_stock_limits(self):
        empty = hot_terms.hot_sector_stocks("", 20)
        self.assertEqual(empty.get("stocks"), [])
        for lim in (20, 30, 50):
            d = hot_terms.hot_sector_stocks("银行", lim)
            self.assertLessEqual(len(d.get("stocks") or []), lim)
            self.assertLessEqual(d.get("count") or 0, lim)

    def test_merge_orig_boards_keeps_ai_and_adds_pull(self):
        bull, bear = intel_ai.merge_orig_boards(
            [{"name": "银行", "why": "AI回填"}],
            [],
            ["半导体", "银行"],
            "利好",
        )
        names = [x["name"] for x in bull]
        self.assertIn("银行", names)
        self.assertIn("半导体", names)
        self.assertEqual(next(x["why"] for x in bull if x["name"] == "银行"), "AI回填")
        self.assertEqual(bear, [])

    def test_merge_orig_boards_bear_direction_and_ai_wins(self):
        bull, bear = intel_ai.merge_orig_boards(
            [{"name": "银行", "why": "AI回填"}],
            [{"name": "半导体", "why": "AI回填"}],
            ["半导体", "新能源"],
            "利空",
        )
        self.assertEqual([x["name"] for x in bull], ["银行"])
        bear_names = [x["name"] for x in bear]
        self.assertIn("半导体", bear_names)
        self.assertIn("新能源", bear_names)
        self.assertEqual(next(x["why"] for x in bear if x["name"] == "半导体"), "AI回填")


if __name__ == "__main__":
    unittest.main()
