# tests/test_webui.py
"""Web 界面 FastAPI 应用的测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ultrabot.webui.app import _redact_api_keys, create_app


class TestRedactApiKeys:
    def test_redacts_keys(self):
        data = {"api_key": "sk-12345", "name": "test", "nested": {"secret": "abc"}}
        redacted = _redact_api_keys(data)
        assert redacted["api_key"] == "***"
        assert redacted["name"] == "test"
        assert redacted["nested"]["secret"] == "***"

    def test_empty_values_not_redacted(self):
        data = {"api_key": "", "token": None}
        redacted = _redact_api_keys(data)
        assert redacted["api_key"] == ""  # 空字符串不遮蔽

    def test_lists_handled(self):
        data = [{"secret_key": "val"}, {"normal": "ok"}]
        redacted = _redact_api_keys(data)
        assert redacted[0]["secret_key"] == "***"
        assert redacted[1]["normal"] == "ok"


class TestAppFactory:
    def test_create_app_returns_fastapi(self):
        app = create_app(config_path="/nonexistent/config.json")
        assert app.title == "ultrabot Web UI"

    def test_health_endpoint_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/api/health" in routes

    def test_websocket_endpoint_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/ws/chat" in routes
