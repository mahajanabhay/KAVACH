"""Generate a static local HTML dashboard over the KAVACH action log and trust table.

  python dashboard.py               # writes dashboard.html and opens it
  python dashboard.py --no-open
"""
import argparse
import sqlite3
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "jarvis_actions.db"
OUT = HERE / "dashboard.html"

TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>KAVACH Action Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h1 { margin-bottom: 0.2rem; }
  .sub { color: #888; margin-bottom: 1.5rem; }
  .stats { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }
  .stat { border: 1px solid #8883; border-radius: 8px; padding: 0.8rem 1.2rem; min-width: 120px; }
  .stat .n { font-size: 1.6rem; font-weight: 700; }
  .stat .l { font-size: 0.8rem; color: #888; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #8882; }
  th { color: #888; font-weight: 600; }
  .tier0 { color: #6a9; } .tier1 { color: #59c; } .tier2 { color: #c93; } .tier3 { color: #c55; }
  .status-done, .status-undone { color: #6a9; }
  .status-denied, .status-blocked, .status-cancelled { color: #c55; }
  .status-auto_trusted { color: #a6c; font-weight: 600; }
  .status-error, .status-undo_failed { color: #c33; }
  code { background: #8882; padding: 0.1rem 0.35rem; border-radius: 4px; }
  section { margin-bottom: 2rem; }
</style></head>
<body>
  <h1>KAVACH Action Dashboard</h1>
  <div class="sub">Generated {{generated}} &middot; {{n_actions}} actions logged</div>

  <div class="stats">
    <div class="stat"><div class="n">{{n_actions}}</div><div class="l">Total actions</div></div>
    <div class="stat"><div class="n">{{confirm_rate}}%</div><div class="l">Confirm rate (tier 2)</div></div>
    <div class="stat"><div class="n">{{n_trusted}}</div><div class="l">Currently auto-trusted</div></div>
    <div class="stat"><div class="n">{{n_undos}}</div><div class="l">Undos used</div></div>
  </div>

  <section>
    <h2>Currently trusted actions</h2>
    {{trust_table}}
  </section>

  <section>
    <h2>Most-used tools</h2>
    {{tool_table}}
  </section>

  <section>
    <h2>Recent actions</h2>
    {{recent_table}}
  </section>
</body></html>
"""


def build():
    if not DB.exists():
        raise SystemExit(f"No action log found at {DB}. Run KAVACH at least once first.")
    db = sqlite3.connect(DB)

    actions = db.execute(
        "SELECT ts, transcript, tool, args, tier, status, result FROM actions ORDER BY id DESC"
    ).fetchall()
    n_actions = len(actions)

    tier2 = [a for a in actions if a[4] == 2]
    confirmed = sum(1 for a in tier2 if a[5] in ("done", "auto_trusted"))
    confirm_rate = round(100 * confirmed / len(tier2)) if tier2 else 0
    n_undos = sum(1 for a in actions if a[5] == "undone")

    now = datetime.now().isoformat(timespec="seconds")
    trusted = db.execute(
        "SELECT key, streak, promoted_until FROM trust WHERE promoted_until > ? ORDER BY promoted_until DESC",
        (now,),
    ).fetchall()
    n_trusted = len(trusted)

    trust_rows = "".join(
        f"<tr><td><code>{key}</code></td><td>{streak}</td><td>{until}</td></tr>"
        for key, streak, until in trusted
    ) or "<tr><td colspan='3'><i>None yet — an action needs 5 confirms in a row to earn this.</i></td></tr>"
    trust_table = (
        "<table><tr><th>Action</th><th>Streak</th><th>Trusted until</th></tr>"
        f"{trust_rows}</table>"
    )

    tool_counts = {}
    for a in actions:
        tool_counts[a[2]] = tool_counts.get(a[2], 0) + 1
    tool_rows = "".join(
        f"<tr><td>{t}</td><td>{c}</td></tr>"
        for t, c in sorted(tool_counts.items(), key=lambda x: -x[1])
    ) or "<tr><td colspan='2'><i>No actions yet.</i></td></tr>"
    tool_table = "<table><tr><th>Tool</th><th>Count</th></tr>" + tool_rows + "</table>"

    recent_rows = "".join(
        f"<tr><td>{ts}</td><td>{transcript}</td><td>{tool}</td>"
        f"<td class='tier{tier}'>T{tier}</td><td class='status-{status}'>{status}</td>"
        f"<td>{(str(result) or '')[:60]}</td></tr>"
        for ts, transcript, tool, args, tier, status, result in actions[:50]
    ) or "<tr><td colspan='6'><i>No actions yet.</i></td></tr>"
    recent_table = (
        "<table><tr><th>Time</th><th>Said</th><th>Tool</th><th>Tier</th>"
        f"<th>Status</th><th>Result</th></tr>{recent_rows}</table>"
    )

    html = TEMPLATE
    for key, val in {
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "n_actions": n_actions, "confirm_rate": confirm_rate,
        "n_trusted": n_trusted, "n_undos": n_undos,
        "trust_table": trust_table, "tool_table": tool_table, "recent_table": recent_table,
    }.items():
        html = html.replace("{{%s}}" % key, str(val))
    OUT.write_text(html, encoding="utf-8")
    return OUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    path = build()
    print(f"Wrote {path}")
    if not a.no_open:
        webbrowser.open(path.as_uri())


if __name__ == "__main__":
    main()