"""Safe action layer: permission tiers, confirmation, action log, undo.

Tier 0: read-only, auto-run.
Tier 1: reversible/harmless local action, auto-run and logged.
Tier 2: needs explicit confirmation (fails closed: no answer = denied).
Tier 3: blocked. Unknown tools default to this tier.
"""
import json
import sqlite3
from datetime import datetime, timedelta

TRUST_THRESHOLD = 5   # consecutive confirms (0 denials) needed to auto-promote an action
TRUST_TTL_DAYS = 30   # how long a promotion lasts before it decays back to needing confirmation

TIERS = {
    "get_time": 0,
    "recall": 0,
    "remember": 1,
    "open_app": 1,
    "web_search": 1,
    "play_music": 1,
    "set_reminder": 1,
    "set_volume": 1,
    "screenshot": 1,
    "find_file": 0,
    "undo_last": 1,
}


def trust_key(name, args):
    """Identify a specific action instance (e.g. one file, one app) so trust never generalizes across different targets."""
    ident = args.get("name") or args.get("app") or args.get("query") or json.dumps(args, sort_keys=True)
    return f"{name}:{ident}"


def tier_of(name, args):
    if name == "find_file" and args.get("open"):
        return 2  # opening an arbitrary file needs confirmation
    return TIERS.get(name, 3)  # unknown tools are blocked


class Guard:
    def __init__(self, run_tool, confirm, undo_makers=None, describers=None,
                 db_path="jarvis_actions.db"):
        self.run_tool = run_tool
        self.confirm = confirm
        self.undo_makers = undo_makers or {}
        self.describers = describers or {}
        self.transcript = ""
        self.stack = []  # (action_id, undo_fn)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS trust (key TEXT PRIMARY KEY,"
            " streak INTEGER, promoted_until TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts TEXT, transcript TEXT, tool TEXT, args TEXT, tier INTEGER,"
            " status TEXT, result TEXT)"
        )
        self.db.commit()

    def _trust_get(self, key):
        """Return current streak; reset (decay) an expired promotion back to 0."""
        row = self.db.execute("SELECT streak, promoted_until FROM trust WHERE key=?", (key,)).fetchone()
        if not row:
            return 0, False
        streak, promoted_until = row
        if promoted_until and promoted_until > datetime.now().isoformat(timespec="seconds"):
            return streak, True
        if promoted_until:  # promotion window passed -> decay back to untrusted
            self.db.execute("UPDATE trust SET streak=0, promoted_until=NULL WHERE key=?", (key,))
            self.db.commit()
            return 0, False
        return streak, False

    def _trust_bump(self, key, confirmed):
        if not confirmed:
            self.db.execute(
                "INSERT INTO trust (key, streak, promoted_until) VALUES (?, 0, NULL)"
                " ON CONFLICT(key) DO UPDATE SET streak=0, promoted_until=NULL", (key,),
            )
            self.db.commit()
            return
        streak, _ = self._trust_get(key)
        streak += 1
        promoted_until = None
        if streak >= TRUST_THRESHOLD:
            promoted_until = (datetime.now() + timedelta(days=TRUST_TTL_DAYS)).isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO trust (key, streak, promoted_until) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET streak=excluded.streak, promoted_until=excluded.promoted_until",
            (key, streak, promoted_until),
        )
        self.db.commit()

    def _log(self, name, args, tier, status, result):
        cur = self.db.execute(
            "INSERT INTO actions (ts, transcript, tool, args, tier, status, result)"
            " VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), self.transcript, name,
             json.dumps(args), tier, status, str(result)[:500]),
        )
        self.db.commit()
        return cur.lastrowid

    def call(self, name, args):
        if name == "undo_last":
            return self.undo()
        tier = tier_of(name, args)
        via_trust = False
        if tier >= 3:
            self._log(name, args, tier, "blocked", "")
            return "Blocked: that action is not allowed."
        if tier == 2:
            describe = self.describers.get(name, lambda a: f"Run {name}?")
            d = describe(args)
            if d is None:
                self._log(name, args, tier, "skipped", "nothing to do")
                return "No matching files."
            desc, key_override = d if isinstance(d, tuple) else (d, None)
            key = key_override or trust_key(name, args)
            _, trusted = self._trust_get(key)
            if trusted:
                via_trust = True
            elif not self.confirm(desc):
                self._trust_bump(key, confirmed=False)
                self._log(name, args, tier, "denied", desc)
                return "Cancelled: not confirmed."
            else:
                self._trust_bump(key, confirmed=True)
        result = self.run_tool(name, args)
        status = "error" if str(result).startswith("Error") else ("auto_trusted" if via_trust else "done")
        aid = self._log(name, args, tier, status, result)
        maker = self.undo_makers.get(name)
        if maker and status == "done":
            fn = maker(args, result)
            if fn:
                self.stack.append((aid, fn))
        return result

    def undo(self):
        if not self.stack:
            return "Nothing to undo."
        aid, fn = self.stack.pop()
        try:
            fn()
            status, msg = "undone", "Undone."
        except Exception as e:
            status, msg = "undo_failed", f"Undo failed: {e}"
        self.db.execute("UPDATE actions SET status=? WHERE id=?", (status, aid))
        self.db.commit()
        self._log("undo_last", {}, 1, "done", msg)
        return msg


def show_log(db_path="jarvis_actions.db", n=20):
    db = sqlite3.connect(db_path)
    rows = db.execute(
        "SELECT id, ts, tool, tier, status, transcript FROM actions ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    for r in reversed(rows):
        print(f"#{r[0]} {r[1]} T{r[3]} {r[2]:<12} {r[4]:<9} \"{r[5]}\"")


if __name__ == "__main__":
    show_log()