from copy import deepcopy
from io import BytesIO
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from post_evaluation.app import app
from post_evaluation.constants import PRODUCTS
from post_evaluation.ingest import extract_records
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.model_mapping import match_model_workbook
from post_evaluation.semantic_rules import compile_plan
from test_semantic_rules import example_plan


def workbook(headers, *, sheet_name='原始数据', second_sheet=False):
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append(headers)
    for index, product in enumerate(PRODUCTS):
        sheet.append([product, 100-index, 50-index][:len(headers)])
    if second_sheet:
        book.copy_worksheet(sheet).title = '另一个来源'
    result = BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


def quality(name='服务评分'):
    item = deepcopy(default_model_definition()['dimensions'][-1])
    item['rule_summary'] = f'按{name}降序排名赋分。'
    return item


class ModelMappingTests(unittest.TestCase):
    def test_summary_input_names_match_without_fixed_quality_header(self):
        for name in ('服务评分', '满意度', '产品推广满意度', '客户体验指数'):
            with self.subTest(name=name):
                item = quality(name)
                targets, sources, mapping = match_model_workbook(BytesIO(workbook(['产品线', name])), [item], 2025)
                self.assertEqual(targets, [item['key']])
                self.assertEqual(sources, ['quality'])
                self.assertEqual(mapping['fields']['quality:2025'], 'B')
                records, issues = extract_records(BytesIO(workbook(['产品线', name])), 'named', 'generic', mapping)
                self.assertFalse(issues)
                self.assertEqual(records[0]['cell'], '原始数据!B2')
                self.assertEqual(records[0]['value'], 100)

    def test_model_name_does_not_replace_the_raw_input_name(self):
        item = quality()
        item['label'] = '客户服务体验'
        targets, _, _ = match_model_workbook(BytesIO(workbook(['产品线', '服务评分'])), [item], 2025)
        self.assertEqual(targets, [item['key']])
        for wrong in ('客户服务体验', '产品推广满意度', '服务评分排名', '服务评分得分'):
            with self.subTest(wrong=wrong):
                targets, _, _ = match_model_workbook(BytesIO(workbook(['产品线', wrong])), [item], 2025)
                self.assertEqual(targets, [])

    def test_ambiguous_columns_sheets_and_years_require_confirmation(self):
        cases = [
            workbook(['产品线', '服务评分', '服务评分']),
            workbook(['产品线', '服务评分'], second_sheet=True),
            workbook(['产品线', '2024年服务评分']),
            workbook(['产品线', '服务评分'], sheet_name='2024年原始数据'),
            workbook(['产品线', '2025年服务评分'], sheet_name='2024年原始数据'),
        ]
        for content in cases:
            with self.subTest(content_size=len(content)):
                targets, _, mapping = match_model_workbook(BytesIO(content), [quality()], 2025)
                self.assertTrue(targets)
                self.assertIsNone(mapping['fields']['quality:2025'])
        _, _, mapping = match_model_workbook(BytesIO(workbook(['产品线', '2025年服 务\n评分', '2024年服务评分'])), [quality()], 2025)
        self.assertEqual(mapping['fields']['quality:2025'], 'B')

    def test_manual_target_choice_keeps_unmatched_input_available(self):
        item = quality()
        targets, _, mapping = match_model_workbook(BytesIO(workbook(['产品线', '其他列'])), [item], 2025, [item['key']])
        self.assertEqual(targets, [item['key']])
        self.assertIsNone(mapping['fields']['quality:2025'])

    def test_hint_does_not_override_explicit_known_source_or_unsupported_operations(self):
        from post_evaluation.calculation_types import local_rule_binding
        self.assertEqual(local_rule_binding('品质服务', '按市场份额降序排名赋分。', 'quality')[0], 'market_share')
        for summary in ('按服务评分加10分降序排名赋分。', '按服务评分降序排名赋分，额外加分。', '按2025年服务评分降序排名赋分。'):
            self.assertIsNone(local_rule_binding('品质服务', summary, 'quality'), summary)

    def test_semantic_plan_preserves_explicit_names_for_multiple_periods(self):
        item = deepcopy(default_model_definition()['dimensions'][1])
        plan = example_plan('revenue')
        for entry in plan['inputs']:
            entry['name'] = '业务营收'
        summary = '使用业务营收比较规模及同比变化，两项赋分均值再次排名赋分。'
        rule = compile_plan(plan, 'revenue')['rule']
        item.update(rule_summary=summary, options={'parsed_rule':rule, 'rule_parser':{'source_summary':summary}})
        _, _, mapping = match_model_workbook(BytesIO(workbook(['产品线', '2025年业务营收', '2024年业务营收'])), [item], 2025)
        self.assertEqual(mapping['fields'], {'revenue:2025':'B', 'revenue:2024':'C'})
        self.assertEqual(rule['execution_plan']['inputs'][0]['name'], '业务营收')

    def test_stale_or_invented_parser_names_do_not_match_unrelated_headers(self):
        item = quality()
        item['rule_summary'] = '将评价原值按从高到低的次序排名赋分。'
        plan = example_plan('quality')
        plan['inputs'][0]['name'] = '凭空编造的列名'
        rule = compile_plan(plan, 'quality')['rule']
        item['options'] = {'parsed_rule':rule,'rule_parser':{'source_summary':item['rule_summary']}}
        targets, _, _ = match_model_workbook(BytesIO(workbook(['产品线','凭空编造的列名'])), [item], 2025)
        self.assertEqual(targets, [])
        item['options']['rule_parser']['source_summary'] = '另一条旧摘要'
        targets, _, _ = match_model_workbook(BytesIO(workbook(['产品线','凭空编造的列名'])), [item], 2025)
        self.assertEqual(targets, [])

    def test_parse_publish_upload_validate_and_score_named_input(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory, GEMINI_API_KEY=''):
            client = TestClient(app)
            parsed = client.post('/api/rules/parse', json={'dimension_label':'品质服务','calculation_type':'quality','summary':'按服务评分降序排名赋分。'})
            self.assertEqual(parsed.status_code, 200, parsed.text)
            value = parsed.json()
            self.assertEqual(value['rule']['required_inputs'][0]['name'], '服务评分')
            original = client.get('/api/models').json()[0]
            draft = client.post('/api/models', json={'name':'服务评分联动','version':'NAMED','clone_from':original['id']}).json()
            definition = draft['definition']
            item = quality()
            item.update(weight=100, options={'parsed_rule':value['rule'],'rule_parser':{'source_summary':item['rule_summary']}})
            definition['dimensions'] = [item]
            body = {'name':draft['name'],'version':draft['version'],'definition':definition}
            published = client.post(f'/api/models/{draft["id"]}/publish', json=body)
            self.assertEqual(published.status_code, 200, published.text)
            run = client.post('/api/runs', json={'name':'同名表头映射','year':2025,'model_id':draft['id']}).json()
            uploaded = client.post(f'/api/runs/{run["id"]}/files', files={'files':('评分表.xlsx',workbook(['产品线','服务评分']))})
            result = uploaded.json()
            self.assertEqual(result['files'][0]['targets'], [item['key']])
            mapping = result['files'][0]['mapping']
            self.assertEqual(mapping['fields']['quality:2025'], 'B')
            self.assertEqual(mapping['input_labels']['quality:2025'], '2025年服务评分')
            validated = client.post(f'/api/runs/{run["id"]}/validate')
            self.assertEqual(validated.json()['validation']['status'], 'passed', validated.text)
            scored = client.post(f'/api/runs/{run["id"]}/calculate')
            self.assertEqual(scored.status_code, 200, scored.text)
            self.assertEqual(scored.json()['scores'][PRODUCTS[0]]['quality'], 10)
            self.assertEqual(scored.json()['traces'][0]['steps'][0]['source']['cell'], '原始数据!B2')
