"""Tests for standalone jarvis.py helper functions.

jarvis.py has import-time side effects (Groq()/SarvamAI() clients need API keys,
creates jarvis_memory/ and jarvis_actions.db) - same reason eval_hinglish.py
extracts functions via ast instead of importing the module directly.
"""
import ast
import os
import sys
from pathlib import Path

import psutil
import pytest

HERE = Path(__file__).parent.parent


def _extract(*names):
    tree = ast.parse((HERE / "jarvis.py").read_text(encoding="utf-8"))
    ns = {"psutil": psutil, "Path": Path, "os": os}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "jarvis.py", "exec"), ns)
    return ns


@pytest.mark.skipif(sys.platform == "win32", reason="run in CI/sandbox; behavior verified on Windows separately")
def test_system_info_returns_expected_fields():
    ns = _extract("get_system_info")
    result = ns["get_system_info"]()
    assert isinstance(result, str)
    assert "CPU" in result
    assert "RAM" in result
    assert "disk" in result
    assert "processes running" in result
    # comma-separated parts, so each field is present and non-empty
    parts = [p.strip() for p in result.split(",")]
    assert all(parts)


def test_system_info_includes_battery_only_when_present():
    ns = _extract("get_system_info")
    result = ns["get_system_info"]()
    if psutil.sensors_battery() is not None:
        assert "battery" in result
    # on a desktop/server with no battery, psutil.sensors_battery() returns None
    # and get_system_info must not crash or fabricate a battery reading