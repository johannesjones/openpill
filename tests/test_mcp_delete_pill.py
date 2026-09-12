"""MCP delete_pill is registered like REST DELETE (no Mongo)."""

from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server.py"


def test_delete_pill_tool_is_registered():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    fn = None
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_pill":
            fn = node
            break
    assert fn is not None, "server.py must define async delete_pill"
    assert any(
        (isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "tool")
        or (isinstance(d, ast.Attribute) and d.attr == "tool")
        for d in fn.decorator_list
    ), "delete_pill must be an @mcp.tool()"
    arg_names = [a.arg for a in fn.args.args]
    assert arg_names[0] == "pill_id"
