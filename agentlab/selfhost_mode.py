"""Player text selected only for the self-hosted lab."""

import os


def scripted_notice() -> str:
    return ("<p class=note><b>Scripted practice mode:</b> Agent runs use a fixed stand-in, "
            "not a real AI model. Results do not measure whether a real agent would follow your input.</p>"
            if os.environ.get("AGENTLAB_BACKEND") == "scripted" else "")
