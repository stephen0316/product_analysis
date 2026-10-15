import unittest

from openpyxl import Workbook

from post_evaluation.ingest import _find_finance_mapping, _find_year_column


class DynamicYearMappingTests(unittest.TestCase):
    def test_finance_columns_follow_source_year_headers(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([])
        sheet.append([
            "产品线", "产品收入", None, "产品毛利", None,
            "产品毛利率", None, "营业利润", None, None, None,
        ])
        sheet.append([
            None, "25年", "26年", "25年", "26年", "25年", "26年",
            "25年营业利润额", "26年营业利润额", "25年营业利润率", "26年营业利润率",
        ])

        mapping = _find_finance_mapping(sheet, 2026)

        self.assertEqual(mapping["header_row"], 3)
        self.assertEqual(mapping["product_col"], "A")
        self.assertEqual(mapping["fields"], {
            "revenue:2025": "B", "revenue:2026": "C",
            "gross_profit:2025": "D", "gross_profit:2026": "E",
            "gross_margin:2025": "F", "gross_margin:2026": "G",
            "operating_profit:2025": "H", "operating_profit:2026": "I",
            "operating_margin:2025": "J", "operating_margin:2026": "K",
        })

    def test_renamed_share_header_can_be_found_by_year(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["公司产品", "行业份额（2025年）", "行业份额（2026年）"])

        self.assertEqual(_find_year_column(sheet, 1, 2026, "行业份额"), "C")


if __name__ == "__main__":
    unittest.main()
