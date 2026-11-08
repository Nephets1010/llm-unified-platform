"""MCP 网关测试（P1-05）。"""

from __future__ import annotations

import uuid

import pytest

from llm_platform.db.session import SessionLocal, init_db
from llm_platform.eval.mcp_gateway import McpGateway


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield


class TestMcpGateway:
    def test_register_and_get_tool(self):
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            uid = uuid.uuid4().hex[:8]
            tool = gw.register_tool(
                name=f"echo-{uid}",
                schema={"handler": "echo", "input_schema": {}},
            )
            assert tool.name == f"echo-{uid}"
            assert tool.enabled is True

            found = gw.get_tool(f"echo-{uid}")
            assert found is not None
            assert found.id == tool.id
        finally:
            db.close()

    def test_call_echo_tool(self):
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            uid = uuid.uuid4().hex[:8]
            gw.register_tool(
                name=f"echo-{uid}",
                schema={"handler": "echo", "input_schema": {}},
            )
            result = gw.call_tool(f"echo-{uid}", {"message": "hi"}, tenant_id=1, project_id=1)
            assert result["success"] is True
            assert result["result"]["echo"] == "hi"
        finally:
            db.close()

    def test_call_search_tool(self):
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            uid = uuid.uuid4().hex[:8]
            gw.register_tool(
                name=f"search-{uid}",
                schema={"handler": "search", "input_schema": {}},
            )
            result = gw.call_tool(f"search-{uid}", {"query": "test"}, tenant_id=1, project_id=1)
            assert result["success"] is True
            assert len(result["result"]["results"]) == 3
        finally:
            db.close()

    def test_call_nonexistent_tool(self):
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            result = gw.call_tool("nonexistent", {}, tenant_id=1, project_id=1)
            assert result["success"] is False
            assert "不存在" in result["error"]
        finally:
            db.close()

    def test_call_disabled_tool(self):
        db = SessionLocal()
        try:
            gw = McpGateway(db)
            uid = uuid.uuid4().hex[:8]
            tool = gw.register_tool(
                name=f"echo-{uid}",
                schema={"handler": "echo", "input_schema": {}},
            )
            tool.enabled = False
            db.commit()

            result = gw.call_tool(f"echo-{uid}", {"message": "hi"}, tenant_id=1, project_id=1)
            assert result["success"] is False
            assert "禁用" in result["error"]
        finally:
            db.close()
