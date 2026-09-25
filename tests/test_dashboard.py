import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402


def _make_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
               " transcript TEXT, tool TEXT, args TEXT, tier INTEGER, status TEXT, result TEXT)")
    db.execute("CREATE TABLE trust (key TEXT PRIMARY KEY, streak INTEGER, promoted_until TEXT)")
    db.commit()
    return db


def test_missing_db_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DB", tmp_path / "nope.db")
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
    try:
        dashboard.build()
        assert False, "should have raised"
    except SystemExit:
        pass


def test_empty_db_renders_placeholders(tmp_path, monkeypatch):
    db_path = tmp_path / "actions.db"
    _make_db(db_path)
    monkeypatch.setattr(dashboard, "DB", db_path)
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
    path = dashboard.build()
    html = path.read_text(encoding="utf-8")
    assert "{{" not in html
    assert "None yet" in html
    assert "No actions yet" in html


def test_populated_db_shows_data_and_no_unreplaced_placeholders(tmp_path, monkeypatch):
    db_path = tmp_path / "actions.db"
    db = _make_db(db_path)
    db.execute("INSERT INTO actions (ts,transcript,tool,args,tier,status,result) VALUES"
               " ('t','open notepad','open_app','{}',1,'done','Opened notepad.')")
    db.execute("INSERT INTO actions (ts,transcript,tool,args,tier,status,result) VALUES"
               " ('t','open resume','find_file','{}',2,'auto_trusted','a/resume.pdf')")
    db.execute("INSERT INTO trust (key, streak, promoted_until) VALUES"
               " ('find_file:resume.pdf', 5, '2999-01-01T00:00:00')")
    db.commit()
    monkeypatch.setattr(dashboard, "DB", db_path)
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "out.html")
    html = dashboard.build().read_text(encoding="utf-8")
    assert "{{" not in html
    assert "open notepad" in html
    assert "find_file:resume.pdf" in html
    assert "2 actions logged" in html