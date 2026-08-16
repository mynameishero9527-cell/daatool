"""13.0.38：机构持仓展示机构名称与持股比例，不编造未披露名单。"""
from __future__ import annotations

import unittest

from app.services import holders
from app.services.intelpick import get_page


class HoldersOrgTests(unittest.TestCase):
    def test_parse_org_names_and_ratio(self):
        raw = {
            "jgcc": [{
                "ORG_TYPE": "00", "ORG_TYPEName": "机构汇总",
                "TOTAL_ORG_NUM": 3, "TOTAL_FREE_SHARES": 1000,
                "TOTAL_SHARES_RATIO": 12.5, "REPORT_DATE": "2026-06-30",
            }, {
                "ORG_TYPE": "01", "ORG_TYPEName": "基金",
                "TOTAL_ORG_NUM": 2, "TOTAL_FREE_SHARES": 200,
                "TOTAL_SHARES_RATIO": 2.0, "REPORT_DATE": "2026-06-30",
            }],
            "jgcg": [{
                "ORG_TYPE": "07", "HOLDER_NAME": "某某资本有限公司",
                "TOTAL_SHARES": 800, "TOTALSHARES_RATIO": 8.25,
                "FREESHARES_RATIO": 8.25, "REPORT_DATE": "2026-06-30",
                "F9_ORGTYPE_NAME": "一般法人", "CHANGE_TYPE_NEW": "增持",
                "FSR_RATE_CHANGE_NEW": 0.12,
            }, {
                "ORG_TYPE": "01", "HOLDER_NAME": "某某基金A",
                "FUND_CODE": "000001", "TOTAL_SHARES": 120,
                "TOTALSHARES_RATIO": 1.2, "REPORT_DATE": "2026-06-30",
                "F9_ORGTYPE_NAME": "基金",
            }, {
                "ORG_TYPE": "00", "HOLDER_NAME": "机构汇总",
                "TOTALSHARES_RATIO": 12.5,
            }, {
                "ORG_TYPE": "01", "HOLDER_NAME": "",
                "TOTALSHARES_RATIO": 0.5,
            }],
            "jjcg": [],
            "gdrs": [], "sdgd": [], "sdltgd": [],
        }
        out = holders.parse_shareholders(raw)
        names = [r["name"] for r in out["org_holders"]]
        self.assertEqual(names, ["某某资本有限公司", "某某基金A"])
        self.assertEqual(out["org_holders"][0]["ratio"], 8.25)
        self.assertEqual(out["org_holders"][0]["type_name"], "一般法人")
        self.assertIn("增持", out["org_holders"][0]["change"] or "")
        self.assertEqual(out["org_holders"][1]["code"], "000001")
        self.assertFalse(any(r["name"] == "机构汇总" for r in out["org_holders"]))
        self.assertEqual(get_page("up")["items"], [])

    def test_empty_details_not_invented(self):
        out = holders.parse_shareholders({"jgcc": [], "jgcg": [], "jjcg": []})
        self.assertEqual(out["org_holders"], [])
        self.assertIn("不编造", out["org_holders_note"])
        self.assertTrue(out["empty"])


if __name__ == "__main__":
    unittest.main()
