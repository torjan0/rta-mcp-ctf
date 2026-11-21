"""Choose the local backend without making a model request."""

import os


def select_backend(env=None):
    values = os.environ if env is None else env
    choice = values.get("AGENTLAB_BACKEND", "auto")
    if choice != "auto":
        if choice not in ("anthropic", "bedrock", "scripted"):
            raise ValueError("Choose anthropic, bedrock or scripted for AGENTLAB_BACKEND")
        return choice
    if values.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if values.get("AWS_ACCESS_KEY_ID") and values.get("AWS_SECRET_ACCESS_KEY"):
        return "bedrock"
    return "scripted"
