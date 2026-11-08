"""示例 MCP 工具：echo_tool（回声工具）。"""


def handle_echo(arguments: dict) -> dict:
    """回声工具：返回输入消息。"""
    return {"echo": arguments.get("message", ""), "type": "echo"}


TOOL_SCHEMA = {
    "name": "echo",
    "description": "回声工具：返回输入消息",
    "handler": "echo",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "要回显的消息"},
        },
        "required": ["message"],
    },
}
