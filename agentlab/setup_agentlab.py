"""Generate missing challenge flags under AGENTLAB_STATE. Use --new-flags for an event reset."""

import argparse
import json
import os
import secrets
from pathlib import Path

ROOT = Path(os.environ.get("AGENTLAB_STATE", Path(__file__).resolve().parent.parent / "runtime" / "agentlab"))


def flag(name: str) -> str:
    return f"RTA\x7b{name}-{secrets.token_hex(6)}}}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-flags", "--force", action="store_true", help="replace all event flags")
    a = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    flags_file = ROOT / "flags.json"
    names = {"a1": "poisoned-menu", "a2": "public-issue", "a3": "title-only", "a4": "config-to-shell",
             "a5": "carbon-copy", "a6": "mcp-form", "a7": "task-owner", "f2": "black-box", "f3": "siem"}
    flags = json.loads(flags_file.read_text()) if flags_file.exists() and not a.new_flags else {}
    # A challenge added later gets its flag here, and every existing flag stays as it is.
    for key, name in names.items():
        flags.setdefault(key, flag(name))
    flags_file.write_text(json.dumps(flags, indent=2))
    print(f"Flags are in {flags_file}")


if __name__ == "__main__":
    main()
