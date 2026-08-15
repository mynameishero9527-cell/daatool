"""13.0.5：公司公告须公司语境；股票常识 AI 覆盖失败不覆盖。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.database import execute, init_db
from app.services import announcement, knowledge


class AnnouncementFilterTests(unittest.TestCase):
    def test_rejects_macro_news_that_only_share_keywords(self):
        for text in (
            "美国宣布枪支回购计划以降低流通枪支",
            "地质调查显示该区域矿产资源储量上升",
            "多国签署麦加协议推动地区和解",
            "央行开展7天期逆回购操作",
            "美联储官员称将评估资产负债表",
        ):
            self.assertFalse(announcement._is_company_announcement(text), text)  # noqa: SLF001

    def test_accepts_company_context_announcements(self):
        for text in (
            "某某股份有限公司关于回购股份的公告",
            "某某董事会决议：控股股东拟增持公司股份",
            "000001 平安银行股份回购进展公告",
            "上市公司发布业绩预告：净利润同比预增",
        ):
            self.assertTrue(announcement._is_company_announcement(text), text)  # noqa: SLF001

    def test_get_announcements_always_returns_list(self):
        init_db()
        d = announcement.get_announcements("", 20)
        self.assertIsInstance(d.get("items"), list)
        self.assertIn("empty_reason", d)
        self.assertIn("note", d)
        for it in d["items"]:
            self.assertTrue(announcement._is_company_announcement(it.get("text") or ""))  # noqa: SLF001
            self.assertIsInstance(it.get("affected_sectors"), list)


class KnowledgeAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        knowledge.ensure_table()

    def tearDown(self):
        execute("DELETE FROM knowledge_ai WHERE term LIKE ?", ("量比",))
        execute("DELETE FROM knowledge_ai WHERE term LIKE ?", ("打板",))

    def test_sections_map_groups(self):
        groups = knowledge.get_knowledge()
        by_group = {g["group"]: g["section"] for g in groups}
        self.assertEqual(by_group["基础术语"], "股票常识")
        self.assertEqual(by_group["资金术语"], "股票常识")
        self.assertEqual(by_group["形态战法"], "选股票小技巧")
        self.assertEqual(by_group["实用技巧"], "选股票小技巧")
        item = next(it for g in groups if g["group"] == "基础术语" for it in g["items"] if it["term"] == "量比")
        self.assertFalse(item["ai_updated"])
        self.assertEqual(item["section"], "股票常识")

    def test_overlay_keep_on_unconfigured(self):
        knowledge.save_knowledge_ai("量比", "这是已经保存过的AI解释，长度足够不被覆盖。", "基础术语", {"source": "测试"})
        with patch("app.services.ai.get_config", return_value={"api_key": "", "api_base": "", "model": ""}):
            d = knowledge.ai_update(term="量比")
        self.assertTrue(d.get("ok"))
        self.assertFalse(d.get("applied"))
        self.assertTrue(d.get("kept"))
        self.assertIn("已经保存过的AI解释", d.get("desc") or "")
        item = next(it for g in knowledge.get_knowledge() for it in g["items"] if it["term"] == "量比")
        self.assertTrue(item["ai_updated"])
        self.assertIn("已经保存过的AI解释", item["desc"])

    def test_overlay_keep_on_llm_fail(self):
        knowledge.save_knowledge_ai("打板", "这是打板词条已保存的解释，失败时必须保留。", "形态战法", {"source": "测试"})
        cfg = {"api_key": "sk-test", "api_base": "https://example.com/v1", "model": "x"}
        with patch("app.services.ai.get_config", return_value=cfg), \
             patch("app.services.ai._call_llm", side_effect=RuntimeError("timeout")):
            d = knowledge.ai_update(term="打板")
        self.assertTrue(d.get("ok"))
        self.assertFalse(d.get("applied"))
        self.assertTrue(d.get("kept"))
        self.assertIn("已保存的解释", d.get("desc") or "")
        item = next(it for g in knowledge.get_knowledge() for it in g["items"] if it["term"] == "打板")
        self.assertIn("失败时必须保留", item["desc"])

    def test_revert_restores_builtin(self):
        knowledge.save_knowledge_ai("量比", "这是临时AI解释，用于测试还原内置。", "基础术语", {})
        r = knowledge.revert_knowledge("量比")
        self.assertTrue(r.get("reverted"))
        item = next(it for g in knowledge.get_knowledge() for it in g["items"] if it["term"] == "量比")
        self.assertFalse(item["ai_updated"])
        self.assertEqual(item["desc"], item["builtin_desc"])

    def test_section_llm_fail_keeps_all(self):
        knowledge.save_knowledge_ai("量比", "常识覆盖解释，整节失败时仍应保留这条。", "基础术语", {})
        cfg = {"api_key": "sk-test", "api_base": "https://example.com/v1", "model": "x"}
        with patch("app.services.ai.get_config", return_value=cfg), \
             patch("app.services.ai._call_llm", side_effect=RuntimeError("boom")):
            d = knowledge.ai_update(section="股票常识")
        self.assertTrue(d.get("ok"))
        self.assertFalse(d.get("applied"))
        self.assertGreater(d.get("total") or 0, 1)
        item = next(it for g in knowledge.get_knowledge() for it in g["items"] if it["term"] == "量比")
        self.assertIn("整节失败时仍应保留", item["desc"])


if __name__ == "__main__":
    unittest.main()
