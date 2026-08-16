"""13.0.5：公司公告须公司语境；股票常识 AI 覆盖失败不覆盖。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.database import execute, init_db, query
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
            "华测导航公告，2026年上半年营业收入19.29亿元",
            "广东鸿图公告称，公司第九届董事会第九次会议同意现金管理",
            "【中国海诚中标云南沃森生物能碳管理平台项目】此次中标是公司在生物医药领域的突破",
        ):
            self.assertTrue(announcement._is_company_announcement(text), text)  # noqa: SLF001

    def test_rejects_gov_roundup_and_non_filings(self):
        for text in (
            "海关总署发布关于进口泰国食用水生动物检疫和卫生要求的公告",
            "印度政府一份公告显示下调出口税",
            "【下周将有29只股解禁 2股解禁比例超50%】据统计，下周将有29只股解禁",
            "【中方团队在全球最大“人造太阳”壁处理领域实现技术链条贯通】据中核集团消息，日前，中核集团核工业西南物理研究院团队在国际热核聚变实验堆（ITER）装置壁处理技术领域接连取得重要进展：不仅顺利通过辉光放电清洗系统",
            "【宇树科技未上市先疯抢 部分中介出价520元/股】记者获悉，宇树科技场外“暗盘交易”悄然兴起。目前多家中介通过各类渠道收购该股新股",
            "【从价格战到场景战 LED产业链结构性修复】今年上半年LED产业链上市公司业绩呈现显著分化，部分公司发布业绩预告",
            "【美国ITC发布对墨盒及其组件II的337部分终裁】美国国际贸易委员会（ITC）发布公告称，对特定墨盒作出终裁",
            "【原山东钢铁集团房地产有限公司董事长王文学接受纪律审查和监察调查】8月14日，莒县纪委监委通报",
            "【段永平继续加仓拼多多，大幅减持英伟达、谷歌】截至二季度末，由段永平管理的基金总持仓市值约191亿美元",
        ):
            self.assertFalse(announcement._is_company_announcement(text), text)  # noqa: SLF001

    def test_get_announcements_always_returns_list(self):
        init_db()
        with patch("app.services.macro.get_news", return_value=[]):
            d = announcement.get_announcements("", 20)
        self.assertIsInstance(d.get("items"), list)
        self.assertIn("empty_reason", d)
        self.assertIn("note", d)
        joined = "\n".join(it.get("text") or "" for it in d["items"])
        for bad in ("人造太阳", "暗盘交易", "LED产业链", "美国ITC", "海关总署", "枪支回购"):
            self.assertNotIn(bad, joined, bad)
        for it in d["items"]:
            self.assertTrue(announcement._is_company_announcement(it.get("text") or ""))  # noqa: SLF001
            self.assertIsInstance(it.get("affected_sectors"), list)
        if query("SELECT 1 FROM macro_event WHERE event_id LIKE 'news_%' AND summary LIKE '%华测导航公告%' LIMIT 1"):
            self.assertTrue(any("华测导航" in (it.get("text") or "") for it in d["items"]))
        elif d["items"]:
            self.assertTrue(any("公告称" in (it.get("text") or "") or "中标" in (it.get("text") or "")
                                for it in d["items"]))


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


class KnowledgeAiEndpointTests(unittest.TestCase):
    """右键更新走 POST /api/knowledge/ai：空 body 不得 422。"""

    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import router
        app = FastAPI()
        app.include_router(router)
        cls.client = TestClient(app)
        init_db()
        knowledge.ensure_table()

    def test_empty_body_is_json_not_422(self):
        r = self.client.post("/api/knowledge/ai")
        self.assertNotEqual(r.status_code, 422, r.text)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type", "")[:16], "application/json")
        d = r.json()
        self.assertFalse(d.get("applied"))
        self.assertTrue(d.get("error"))

    def test_json_term_and_query_fallback(self):
        r = self.client.post("/api/knowledge/ai", json={"term": "量比"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d.get("term"), "量比")
        self.assertIn("applied", d)
        r2 = self.client.post("/api/knowledge/ai?term=%E9%87%8F%E6%AF%94")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json().get("term"), "量比")

    def test_empty_json_object(self):
        r = self.client.post("/api/knowledge/ai", json={})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json().get("applied"))


if __name__ == "__main__":
    unittest.main()

