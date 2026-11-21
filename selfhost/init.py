"""Prepare a persistent CTFd secret and writable data volume before CTFd starts."""

from pathlib import Path
import os
import secrets

root = Path("/var/data")
root.mkdir(parents=True, exist_ok=True)
key = root / "secret_key"
if not key.exists():
    key.write_text(secrets.token_hex(48))
key.chmod(0o600)
for path in (root, root / "uploads", root / "logs"):
    path.mkdir(exist_ok=True)
for current, dirs, files in os.walk(root):
    os.chown(current, 1001, 1001)
    for name in dirs + files:
        os.chown(os.path.join(current, name), 1001, 1001)
