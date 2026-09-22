import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from safety import Guard, tier_of  # noqa: E402


def make_guard(tmp_path, confirm=lambda d: True, undo_makers=None, describers=None):
    calls = []

    def run(name, args):
        calls.append((name, args))
        return "Error: boom" if args.get("bad") else "ok"

    g = Guard(run, confirm, undo_makers, describers, db_path=str(tmp_path / "t.db"))
    return g, calls


def rows(g):
    return g.db.execute("SELECT tool, tier, status FROM actions ORDER BY id").fetchall()


def test_tiers():
    assert tier_of("get_time", {}) == 0
    assert tier_of("open_app", {"app": "notepad"}) == 1
    assert tier_of("find_file", {"name": "x"}) == 0
    assert tier_of("find_file", {"name": "x", "open": True}) == 2
    assert tier_of("delete_everything", {}) == 3


def test_unknown_tool_blocked(tmp_path):
    g, calls = make_guard(tmp_path)
    assert g.call("rm_rf", {}).startswith("Blocked")
    assert calls == []
    assert rows(g) == [("rm_rf", 3, "blocked")]


def test_tier2_denied_does_not_run(tmp_path):
    g, calls = make_guard(tmp_path, confirm=lambda d: False)
    out = g.call("find_file", {"name": "a", "open": True})
    assert out.startswith("Cancelled")
    assert calls == []
    assert rows(g)[0][2] == "denied"


def test_tier2_approved_runs(tmp_path):
    g, calls = make_guard(tmp_path, confirm=lambda d: True)
    g.call("find_file", {"name": "a", "open": True})
    assert len(calls) == 1
    assert rows(g)[0][2] == "done"


def test_tier2_nothing_to_confirm(tmp_path):
    g, calls = make_guard(tmp_path, describers={"find_file": lambda a: None})
    assert g.call("find_file", {"name": "a", "open": True}) == "No matching files."
    assert calls == []


def test_tier1_runs_without_confirm(tmp_path):
    def no_confirm(d):
        raise AssertionError("should not ask")

    g, calls = make_guard(tmp_path, confirm=no_confirm)
    g.call("set_volume", {"action": "up"})
    assert len(calls) == 1


def test_undo(tmp_path):
    undone = []
    g, _ = make_guard(tmp_path, undo_makers={"set_volume": lambda a, r: (lambda: undone.append(a))})
    g.call("set_volume", {"action": "up"})
    assert g.call("undo_last", {}) == "Undone."
    assert undone == [{"action": "up"}]
    statuses = [r[2] for r in rows(g)]
    assert "undone" in statuses
    assert g.call("undo_last", {}) == "Nothing to undo."


def test_error_not_undoable_and_logged(tmp_path):
    g, _ = make_guard(tmp_path, undo_makers={"open_app": lambda a, r: (lambda: None)})
    g.call("open_app", {"app": "notepad", "bad": True})
    assert rows(g)[0][2] == "error"
    assert g.call("undo_last", {}) == "Nothing to undo."