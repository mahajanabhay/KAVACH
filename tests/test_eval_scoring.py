import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval_hinglish import load_cases, load_defs, score  # noqa: E402


def test_exact_and_wrong_tool():
    c = {"tool": "open_app", "args": {"app": "notepad"}, "contains": {}}
    assert score(c, "open_app", {"app": "notepad"})
    assert not score(c, "open_app", {"app": "chrome"})
    assert not score(c, "web_search", {"app": "notepad"})


def test_contains_any_of_list_and_case():
    c = {"tool": "web_search", "args": {}, "contains": {"query": ["python", "पाइथन"]}}
    assert score(c, "web_search", {"query": "Python tutorial"})
    assert score(c, "web_search", {"query": "पाइथन ट्यूटोरियल"})
    assert not score(c, "web_search", {"query": "java"})


def test_false_matches_missing():
    c = {"tool": "find_file", "args": {"open": False}, "contains": {}}
    assert score(c, "find_file", {"name": "resume"})
    assert not score(c, "find_file", {"name": "resume", "open": True})


def test_no_tool_expected():
    c = {"tool": None, "args": {}, "contains": {}}
    assert score(c, None, {})
    assert not score(c, "open_app", {"app": "notepad"})


def test_numbers():
    c = {"tool": "set_reminder", "args": {"minutes": 5}, "contains": {"message": "chai"}}
    assert score(c, "set_reminder", {"minutes": 5.0, "message": "drink chai"})
    assert not score(c, "set_reminder", {"minutes": 10, "message": "chai"})


def test_command_set_matches_jarvis_tool_schema():
    _, _, tools = load_defs()
    by_name = {t["name"]: t["input_schema"]["properties"] for t in tools}
    ids = set()
    for c in load_cases():
        assert c["id"] not in ids
        ids.add(c["id"])
        if c["tool"] is None:
            continue
        assert c["tool"] in by_name, c
        for k, v in {**c["args"], **c["contains"]}.items():
            assert k in by_name[c["tool"]], c
            enum = by_name[c["tool"]][k].get("enum")
            if enum and k in c["args"]:
                assert v in enum, c