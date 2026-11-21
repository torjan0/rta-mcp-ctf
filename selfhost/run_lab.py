"""Start the lab using CTFd login in every backend mode."""

import os
import sys

from backend import select_backend

backend = select_backend()
os.environ["AGENTLAB_BACKEND"] = backend
if backend == "scripted":
    sys.path.insert(0, "/app/agentlab")
    import dev_nospend

    dev_nospend.install()
    import app

    dev_nospend.install()
else:
    sys.path.insert(0, "/app/agentlab")
    import app

import uvicorn

print(f"Lab backend: {backend}", flush=True)
uvicorn.run(app.app, host="0.0.0.0", port=8100)
