"""示例 MCP 工具：search_tool（模拟搜索工具）。"""

from typing import Any


def handle_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """模拟搜索：返回固定搜索结果。"""
    query = arguments.get("query", "")
    return {
        "query": query,
        "results": [
            {"title": f"Result {i+1} for '{query}'", "url": f"https://example.com/{i+1}"}
            for i in range(3)
        ],
        "type": "search",
    }


TOOL_SCHEMA = {
    "name": "search",
    "description": "搜索工具：根据查询返回搜索结果",
    "handler": "search",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "required": ["query"],
    },
}
