"""Business tests bypass identity explicitly; test_auth exercises the real gate.

The override is scoped to each request, and creates no production account or
session. This is test code only, not an environment switch shipped by the app.
"""
from unittest.mock import patch
from fastapi.testclient import TestClient as FastAPITestClient


class TestClient(FastAPITestClient):
    def request(self, *args, **kwargs):
        with patch('post_evaluation.auth.authorize', return_value={'id': 'business-test', 'username': 'business-test'}):
            return super().request(*args, **kwargs)
