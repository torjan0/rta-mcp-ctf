"""Generate flags once and seed the local scoreboard after its setup page responds."""

import os
from pathlib import Path
import subprocess
import sys

from backend import select_backend

root = Path("/var/lib/agentlab")
root.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, "/app/agentlab/setup_agentlab.py"], check=True)
cmd = [sys.executable, "/app/deploy/aws/ctfd_seed.py",
       "--url", "http://ctfd:8000", "--lab-url", "http://127.0.0.1:8100/",
       "--admin-file", str(root / "ctfd_admin.json"),
       "--flags-file", str(root / "flags.json"),
       "--max-users", "10000", "--selfhost"]
if select_backend() == "scripted":
    cmd.append("--scripted")
subprocess.run(cmd, check=True)
print("Admin login is in the lab_state volume at /var/lib/agentlab/ctfd_admin.json", flush=True)
