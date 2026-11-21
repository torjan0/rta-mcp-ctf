"""End all lab sessions for a scoreboard name or uN key after a CTFd ban."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def end_sessions(root: Path, selector: str, *, by_key: bool = False) -> tuple[str, int]:
    with sqlite3.connect(root / "state.db", timeout=10) as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute("SELECT player FROM accounts WHERE player = ? OR (name = ? AND ?)",
                           (selector, selector, not by_key)).fetchall()
        if not rows:
            raise ValueError("No matching lab account.")
        if len(rows) > 1:
            raise ValueError("Ambiguous account. Use --key uN with the scoreboard user ID.")
        player, = rows[0]
        count = con.execute("DELETE FROM sessions WHERE player = ?", (player,)).rowcount
    return player, count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", action="store_true", help="select strictly by lab key")
    parser.add_argument("account", nargs="?", help="exact scoreboard name or uN (default: read stdin)")
    args = parser.parse_args()
    selector = args.account if args.account is not None else sys.stdin.read()
    root = Path(os.environ.get("AGENTLAB_STATE", Path(__file__).resolve().parent.parent / "runtime" / "agentlab"))
    try:
        player, count = end_sessions(root, selector, by_key=args.key)
    except ValueError as err:
        sys.exit(str(err))
    print(f"Ended {count} lab session(s) for {player}.")
