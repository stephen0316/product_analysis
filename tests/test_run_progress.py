from io import BytesIO
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from post_evaluation import store
from post_evaluation.app import app


class RunProgressTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory.name)
        environment.start(); self.addCleanup(environment.stop)
        self.client = TestClient(app)

    def add_source(self, run):
        book = Workbook(); book.active.append(['产品', '值']); book.active.append(['千里眼', 10])
        buffer = BytesIO(); book.save(buffer); book.close()
        return store.add_file(run['id'], 'generic', [], '测试.xlsx', buffer.getvalue(), {}, [])

    def check_progress(self, run, status, view):
        row = next(r for r in self.client.get('/api/runs').json() if r['id'] == run['id'])
        actual = self.client.get('/api/runs/'+run['id']).json()
        self.assertEqual(row['progress'], actual['progress'])
        self.assertEqual((row['progress']['status'], row['progress']['next_view']), (status, view))
        return row['progress']

    def test_five_states_match_list_and_detail_without_manual_status(self):
        run = store.create_run(2025, '流程状态')
        self.check_progress(run, 'awaiting_upload', 'upload')
        self.add_source(run)
        self.check_progress(run, 'awaiting_validation', 'validation')
        store.update_validation(run['id'], {'status':'failed'})
        self.assertEqual(self.check_progress(run,'awaiting_validation','validation')['action'], '处理校验问题')
        store.update_validation(run['id'], {'status':'passed'})
        self.check_progress(run, 'awaiting_calculation', 'validation')
        store.update_run_json(run['id'], 'result', {'scores':{}})
        self.check_progress(run, 'results_ready', 'overview')
        store.save_report(store.get_run(run['id']), {}, b'report')
        self.check_progress(run, 'report_ready', 'report')

    def test_mapping_edit_invalidates_report_result_and_validation_then_delete_returns_upload(self):
        run = store.create_run(2025, '状态回退'); source = self.add_source(run)
        store.update_validation(run['id'], {'status':'passed'})
        store.update_run_json(run['id'], 'result', {'scores':{}})
        store.save_report(store.get_run(run['id']), {}, b'report')
        self.check_progress(run, 'report_ready', 'report')
        store.update_mapping(run['id'], source, {'sheet':'新工作表'})
        self.check_progress(run, 'awaiting_validation', 'validation')
        store.delete_file(run['id'], source)
        self.check_progress(run, 'awaiting_upload', 'upload')

    def test_review_change_invalidates_report_only(self):
        run = store.create_run(2025, '审阅更新'); self.add_source(run)
        store.update_run_json(run['id'], 'result', {'scores':{}})
        store.save_report(store.get_run(run['id']), {}, b'report')
        store.update_run_json(run['id'], 'notes', {'analysis':'新意见'})
        self.check_progress(run, 'results_ready', 'overview')

    def test_uploaded_file_and_source_change_regress_to_validation(self):
        for mutation in ('file', 'source'):
            with self.subTest(mutation=mutation):
                run = store.create_run(2025, mutation); self.add_source(run)
                store.update_validation(run['id'], {'status':'passed'})
                store.update_run_json(run['id'], 'result', {'scores':{}})
                if mutation == 'file': self.add_source(run)
                else: store.update_run_json(run['id'], 'source_choices', {'field':'source'})
                self.check_progress(run, 'awaiting_validation', 'validation')
