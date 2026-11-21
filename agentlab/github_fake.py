"""A fake GitHub for the agent challenges. In memory, built fresh for every run.

The tool names and arguments follow the official GitHub MCP server where that is
cheap, so the challenge reads like the slide. Nothing here reaches the network.
The token the bot holds sees every repo, private ones included, which is the
over-broad token the challenges are about.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field

BOT = "acme-bot"
MAX_TEXT = 4000  # longest comment or pull request body kept, in characters


@dataclass
class Issue:
    number: int
    title: str
    body: str
    user: str
    state: str = "open"
    comments: list[dict] = field(default_factory=list)


@dataclass
class Repo:
    owner: str
    name: str
    private: bool
    description: str
    files: dict[str, str]
    issues: dict[int, Issue] = field(default_factory=dict)
    pulls: list[dict] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class FakeGitHub:
    def __init__(self, repos: list[Repo], titles_only: bool = False):
        self.repos = {r.full_name.lower(): r for r in repos}
        self.titles_only = titles_only

    def _repo(self, args: dict) -> Repo:
        key = f"{args.get('owner', '')}/{args.get('repo', '')}".lower()
        if key not in self.repos:
            raise ValueError("Not Found")
        return self.repos[key]

    # Tools ---------------------------------------------------------------------

    def search_repositories(self, args: dict) -> str:
        q = str(args.get("query", "")).lower()
        found = [
            {"full_name": r.full_name, "private": r.private, "description": r.description}
            for r in self.repos.values()
            if not q or any(w in f"{r.full_name} {r.description}".lower() for w in q.replace("org:", "").split())
        ]
        return json.dumps({"total_count": len(found), "items": found})

    def list_issues(self, args: dict) -> str:
        repo = self._repo(args)
        state = str(args.get("state", "open")).lower()
        out = []
        for i in sorted(repo.issues.values(), key=lambda i: -i.number):
            if state in ("open", "closed") and i.state != state:
                continue
            if self.titles_only:
                out.append({"number": i.number, "title": i.title})
            else:
                out.append({"number": i.number, "title": i.title, "body": i.body, "user": i.user,
                            "state": i.state, "comments": len(i.comments)})
        return json.dumps(out)

    def get_issue(self, args: dict) -> str:
        repo = self._repo(args)
        i = repo.issues.get(int(args.get("issue_number", 0)))
        if not i:
            raise ValueError("Not Found")
        return json.dumps({"number": i.number, "title": i.title, "body": i.body, "user": i.user,
                           "state": i.state, "comments": len(i.comments)})

    def get_file_contents(self, args: dict) -> str:
        repo = self._repo(args)
        path = posixpath.normpath("/" + str(args.get("path", ""))).lstrip("/")
        if path in repo.files:
            return repo.files[path]
        prefix = f"{path}/" if path else ""
        entries = {}
        for f in repo.files:
            if f.startswith(prefix):
                rest = f[len(prefix):]
                name = rest.split("/", 1)[0]
                entries[name] = {"name": name, "path": prefix + name, "type": "dir" if "/" in rest else "file"}
        if not entries:
            raise ValueError("Not Found")
        return json.dumps(sorted(entries.values(), key=lambda e: e["path"]))

    def add_issue_comment(self, args: dict) -> str:
        repo = self._repo(args)
        i = repo.issues.get(int(args.get("issue_number", 0)))
        if not i:
            raise ValueError("Not Found")
        i.comments.append({"user": BOT, "body": str(args.get("body", ""))[:MAX_TEXT]})
        return json.dumps({"id": len(i.comments), "html_url": f"https://github.com/{repo.full_name}/issues/{i.number}"})

    def create_pull_request(self, args: dict) -> str:
        repo = self._repo(args)
        n = 100 + len(repo.pulls) + 1
        repo.pulls.append({
            "number": n,
            "title": str(args.get("title", ""))[:200],
            "body": str(args.get("body", ""))[:MAX_TEXT],
            "head": str(args.get("head", ""))[:100],
            "base": str(args.get("base", "main"))[:100],
            "user": BOT,
        })
        return json.dumps({"number": n, "html_url": f"https://github.com/{repo.full_name}/pull/{n}"})

    # Wiring ---------------------------------------------------------------------

    def tools(self, names: list[str]) -> tuple[list[dict], dict]:
        owner_repo = {
            "owner": {"type": "string", "description": "Repository owner"},
            "repo": {"type": "string", "description": "Repository name"},
        }
        specs = {
            "search_repositories": ("Search for GitHub repositories the token can see.",
                                    {"query": {"type": "string", "description": "Search query"}}, ["query"]),
            "list_issues": ("List issues in a GitHub repository.",
                            {**owner_repo, "state": {"type": "string", "enum": ["open", "closed", "all"]}},
                            ["owner", "repo"]),
            "get_issue": ("Get details of a specific issue in a GitHub repository.",
                          {**owner_repo, "issue_number": {"type": "number"}}, ["owner", "repo", "issue_number"]),
            "get_file_contents": ("Get the contents of a file or directory from a GitHub repository.",
                                  {**owner_repo, "path": {"type": "string", "description": "Path to file or directory"}},
                                  ["owner", "repo", "path"]),
            "add_issue_comment": ("Add a comment to an existing issue.",
                                  {**owner_repo, "issue_number": {"type": "number"},
                                   "body": {"type": "string", "description": "Comment text"}},
                                  ["owner", "repo", "issue_number", "body"]),
            "create_pull_request": ("Create a new pull request in a GitHub repository.",
                                    {**owner_repo, "title": {"type": "string"}, "body": {"type": "string"},
                                     "head": {"type": "string", "description": "Branch with the changes"},
                                     "base": {"type": "string", "description": "Branch to merge into"}},
                                    ["owner", "repo", "title", "head", "base"]),
        }
        tools = [
            {"name": n, "description": specs[n][0],
             "input_schema": {"type": "object", "properties": specs[n][1], "required": specs[n][2]}}
            for n in names
        ]
        return tools, {n: getattr(self, n) for n in names}

    def public_view(self, full_name: str) -> dict:
        """What anyone on the internet sees on a public repo after the run."""
        r = self.repos[full_name.lower()]
        assert not r.private
        return {
            "issues": [
                {"number": i.number, "title": i.title, "body": i.body, "user": i.user, "comments": i.comments}
                for i in sorted(r.issues.values(), key=lambda i: i.number)
            ],
            "pulls": r.pulls,
        }
