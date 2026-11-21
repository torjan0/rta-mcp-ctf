"""Sam's machine for A4: a fake shell over fake files and a fake network.

Nothing here starts a process, opens a file or touches the network. The files
are a dict, the network is one host that records what reaches the player's
hook, and the shell understands only the handful of commands the A4 page lists.
It is a small interpreter written for the challenge, so anything it does not
understand prints a shell-style error instead of failing the run.

Every Machine is built fresh for one agent run, and its limits count across
that run: 50 commands, 10 levels of nesting, 64 KB of output.
"""

from __future__ import annotations

import base64
import binascii
import posixpath
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote_plus, urlsplit

HOME = "/home/sam"
MAX_COMMANDS = 50
MAX_DEPTH = 10
MAX_OUTPUT = 65536
MAX_REQUESTS = 20
MAX_ARGS = 4096
MAX_PATH = 1024
MAX_PATH_PARTS = 32

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
PLAIN_RE = re.compile(r"[^\s\\#;&|<>(){}'\"$`]+")
QUOTED_RE = re.compile(r"[^\"\\$`]+")


class ShellLimit(Exception):
    """A run went past one of its limits. The message names which."""


class ShellSyntax(Exception):
    """The script could not be parsed."""


def _count(value: str, default: int) -> int:
    """Parse shell counts without accepting odd digits or constructing huge ints."""
    if not value.isascii() or not value.isdecimal():
        return default
    return min(int(value), MAX_OUTPUT + 1) if len(value) < 10 else MAX_OUTPUT + 1


def _star_match(pattern: str, text: str) -> bool:
    """Match stars within one path component without regex backtracking."""
    pieces = pattern.split("*")
    if len(pieces) == 1:
        return pattern == text
    if not text.startswith(pieces[0]) or not text.endswith(pieces[-1]):
        return False
    start, end = len(pieces[0]), len(text) - len(pieces[-1])
    for piece in pieces[1:-1]:
        found = text.find(piece, start, end)
        if found < 0:
            return False
        start = found + len(piece)
    return start <= end


@dataclass
class Seg:
    kind: str  # "lit", "var" or "sub"
    text: str
    quoted: bool


Word = list  # list[Seg]


@dataclass
class Command:
    words: list
    redirs: list  # (operator, Word), the operator like "1>", "1>>", "2>", "0<", "1>&", "&>"


# Parsing --------------------------------------------------------------------


def _find_close(src: str, i: int, close: str) -> int:
    """Index of the bracket that closes the one before i, skipping quotes."""
    depth = 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = src.find("'", i + 1)
            if j < 0:
                break
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < len(src) and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
            continue
        if c == "(" and close == ")":
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ShellSyntax("Syntax error: end of file unexpected (expecting \")\")")


def _dollar(src: str, i: int, quoted: bool) -> tuple[Seg, int]:
    """Parse the expansion that starts with the $ at i."""
    nxt = src[i + 1] if i + 1 < len(src) else ""
    if nxt == "(":
        j = _find_close(src, i + 2, ")")
        return Seg("sub", src[i + 2:j], quoted), j + 1
    if nxt == "{":
        j = src.find("}", i + 2)
        if j < 0:
            raise ShellSyntax("Syntax error: Missing '}'")
        name = src[i + 2:j]
        if not (NAME_RE.fullmatch(name) or name in ("?", "$") or name.isdigit()):
            raise ShellSyntax(f"Bad substitution: ${{{name}}}")
        return Seg("var", name, quoted), j + 1
    m = NAME_RE.match(src, i + 1)
    if m:
        return Seg("var", m.group(0), quoted), m.end()
    if nxt and (nxt in "?$" or nxt.isdigit()):
        return Seg("var", nxt, quoted), i + 2
    return Seg("lit", "$", quoted), i + 1


def lex(src: str) -> list[tuple[str, object]]:
    """Turn a script into ("word", Word), ("op", str) and ("redir", str) tokens."""
    toks: list[tuple[str, object]] = []
    word: list | None = None
    i, n = 0, len(src)

    # A word is built as [kind, [text pieces], quoted] so long literals join once, at the end.
    def add(seg: Seg) -> None:
        nonlocal word
        if word is None:
            word = []
        if word and seg.kind == "lit" and word[-1][0] == "lit" and word[-1][2] == seg.quoted:
            word[-1][1].append(seg.text)
        else:
            word.append([seg.kind, [seg.text], seg.quoted])

    def end() -> None:
        nonlocal word
        if word is not None:
            toks.append(("word", [Seg(k, "".join(parts), q) for k, parts, q in word]))
            word = None

    while i < n:
        c = src[i]
        if c in " \t\r":
            end()
            i += 1
        elif c == "\\":
            if i + 1 < n and src[i + 1] != "\n":
                add(Seg("lit", src[i + 1], True))
            i += 2
        elif c == "#" and word is None:
            while i < n and src[i] != "\n":
                i += 1
        elif c in "\n;":
            end()
            toks.append(("op", ";"))
            i += 1
        elif c == "&":
            end()
            if src.startswith("&&", i):
                toks.append(("op", "&&"))
                i += 2
            elif src.startswith("&>", i):
                toks.append(("redir", "&>"))
                i += 2
            else:
                toks.append(("op", ";"))  # no background jobs, it just runs
                i += 1
        elif c == "|":
            end()
            if src.startswith("||", i):
                toks.append(("op", "||"))
                i += 2
            else:
                toks.append(("op", "|"))
                i += 1
        elif c in "<>":
            fd = None
            if word is not None and len(word) == 1 and word[0][0] == "lit" and not word[0][2] \
                    and "".join(word[0][1]) in ("0", "1", "2"):
                fd, word = "".join(word[0][1]), None
            end()
            if c == "<":
                if src.startswith("<<", i):
                    raise ShellSyntax("here-documents are not supported")
                toks.append(("redir", (fd or "0") + "<"))
                i += 1
            elif src.startswith(">>", i):
                toks.append(("redir", (fd or "1") + ">>"))
                i += 2
            elif src.startswith(">&", i):
                toks.append(("redir", (fd or "1") + ">&"))
                i += 2
            else:
                toks.append(("redir", (fd or "1") + ">"))
                i += 1
        elif c in "(){}" and word is None:
            raise ShellSyntax(f"Syntax error: \"{c}\" unexpected")
        elif c in "(){}":
            add(Seg("lit", c, False))
            i += 1
        elif c == "'":
            j = src.find("'", i + 1)
            if j < 0:
                raise ShellSyntax("Syntax error: Unterminated quoted string")
            add(Seg("lit", src[i + 1:j], True))
            i = j + 1
        elif c == '"':
            add(Seg("lit", "", True))
            i += 1
            while True:
                if i >= n:
                    raise ShellSyntax("Syntax error: Unterminated quoted string")
                d = src[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and i + 1 < n and src[i + 1] in '"\\$`\n':
                    if src[i + 1] != "\n":
                        add(Seg("lit", src[i + 1], True))
                    i += 2
                elif d == "$":
                    seg, i = _dollar(src, i, True)
                    add(seg)
                elif d == "`":
                    j = src.find("`", i + 1)
                    if j < 0:
                        raise ShellSyntax("Syntax error: Unterminated quoted string")
                    add(Seg("sub", src[i + 1:j], True))
                    i = j + 1
                else:
                    m = QUOTED_RE.match(src, i)
                    add(Seg("lit", m.group(0) if m else d, True))
                    i = m.end() if m else i + 1
        elif c == "$":
            seg, i = _dollar(src, i, False)
            add(seg)
        elif c == "`":
            j = src.find("`", i + 1)
            if j < 0:
                raise ShellSyntax("Syntax error: EOF in backquote substitution")
            add(Seg("sub", src[i + 1:j], False))
            i = j + 1
        else:
            m = PLAIN_RE.match(src, i)
            add(Seg("lit", m.group(0) if m else c, False))
            i = m.end() if m else i + 1
    end()
    return toks


def parse(src: str) -> list[list[tuple[str | None, list[Command]]]]:
    """A script is a list of and-or lists, each a list of (connector, pipeline)."""
    script: list = []
    andor: list = []
    pipeline: list[Command] = []
    cmd = Command([], [])
    connector: str | None = None
    redir: str | None = None

    def close_cmd(required: bool) -> None:
        nonlocal cmd
        if redir:
            raise ShellSyntax("Syntax error: newline unexpected")
        if cmd.words or cmd.redirs:
            pipeline.append(cmd)
        elif required:
            raise ShellSyntax("Syntax error: \"|\" unexpected")
        cmd = Command([], [])

    def close_pipeline(required: bool) -> None:
        nonlocal pipeline, connector
        close_cmd(required)
        if pipeline:
            andor.append((connector, pipeline))
        elif required:
            raise ShellSyntax("Syntax error: \"&&\" unexpected")
        pipeline = []

    for kind, val in lex(src):
        if kind == "word":
            if redir:
                cmd.redirs.append((redir, val))
                redir = None
            else:
                cmd.words.append(val)
        elif kind == "redir":
            if redir:
                raise ShellSyntax("Syntax error: redirection unexpected")
            redir = val
        elif val == "|":
            close_cmd(True)
        elif val in ("&&", "||"):
            close_pipeline(True)
            connector = val
        else:
            if connector and not pipeline and not cmd.words:
                raise ShellSyntax("Syntax error: \";\" unexpected")
            close_pipeline(False)
            connector = None
            if andor:
                script.append(andor)
            andor = []
    if connector and not pipeline and not cmd.words:
        raise ShellSyntax("Syntax error: end of file unexpected")
    close_pipeline(False)
    if andor:
        script.append(andor)
    return script


# The machine ----------------------------------------------------------------


class Machine:
    """Sam's laptop, as far as a start script can tell."""

    def __init__(self, files: dict[str, str], env: dict[str, str], hook_host: str, hook_id: str,
                 cwd: str = HOME):
        self.files = dict(files)
        self.env = dict(env)
        self.hook_host = hook_host.lower()
        self.hook_id = hook_id
        self.cwd = cwd
        self.requests: list[dict] = []
        self.status = 0
        self.commands = 0
        self.output = 0
        self._out: list[str] = []
        self._err_sinks: list[Callable[[str], None]] = []

    # Running

    def run(self, script: str) -> str:
        """Run a script and return everything it printed. Never raises."""
        self._out = []
        self._err_sinks = []
        try:
            self._script(script, 0, "", self._emit)
        except ShellLimit as e:
            self._out.append(f"sh: {e} limit reached\n")
        except ShellSyntax as e:
            self._out.append(f"sh: 1: {str(e)[:160]}\n")
        except (ValueError, OverflowError) as e:
            self._out.append(f"sh: invalid argument ({str(e)[:120]})\n")
        except RecursionError:
            self._out.append("sh: nesting limit reached\n")
        return "".join(self._out)

    def _emit(self, text: str) -> None:
        room = MAX_OUTPUT - self.output
        if len(text) > room:
            self._out.append(text[:max(room, 0)])
            self.output = MAX_OUTPUT
            raise ShellLimit("output")
        self._out.append(text)
        self.output += len(text)

    def _err(self, text: str) -> None:
        (self._err_sinks[-1] if self._err_sinks else self._emit)(text)

    @staticmethod
    def _cap(text: str) -> str:
        if len(text) > MAX_OUTPUT:
            raise ShellLimit("output")
        return text

    def _script(self, src: str, depth: int, stdin: str, emit: Callable[[str], None]) -> int:
        if depth > MAX_DEPTH:
            raise ShellLimit("nesting")
        if len(src) > 2 * MAX_OUTPUT:
            raise ShellLimit("input")
        for andor in parse(src):
            for connector, pipeline in andor:
                if connector == "&&" and self.status != 0:
                    continue
                if connector == "||" and self.status == 0:
                    continue
                data = stdin
                for cmd in pipeline:
                    data, self.status = self._command(cmd, depth, data)
                if data:
                    emit(data)
        return self.status

    def _command(self, cmd: Command, depth: int, stdin: str) -> tuple[str, int]:
        self.commands += 1
        if self.commands > MAX_COMMANDS:
            raise ShellLimit("command")
        words = list(cmd.words)
        assigns = []
        while words and self._assignment(words[0]):
            assigns.append(words.pop(0))
        if words and self._is_lit(words[0], "export"):
            assigns += [w for w in words[1:] if self._assignment(w)]
            words = []
        for w in assigns:
            name, value = self._assign_value(w, depth)
            self.env[name] = value
        argv: list[str] = []
        argv_size = 0
        for w in words:
            expanded = self._expand(w, depth)
            argv_size += sum(map(len, expanded))
            if argv_size > MAX_OUTPUT or len(argv) + len(expanded) > MAX_ARGS:
                raise ShellLimit("expansion")
            argv += expanded
        out_to, err_to, append = None, None, False
        for op, target_word in cmd.redirs:
            target = "".join(self._expand(target_word, depth, split=False))
            if op == "0<":
                content = self._read(target)
                if content is None:
                    self._err(f"sh: 1: cannot open {target}: No such file\n")
                    return "", 2
                stdin = content
            elif op in ("1>", "1>>"):
                out_to, append = target, op == "1>>"
            elif op == "2>" or op == "2>>":
                err_to = target
            elif op == "&>":
                out_to = err_to = target
            elif op == "1>&" and target == "2":
                out_to = "&2"
            # 2>&1 and anything else are accepted and change nothing here
        if not argv:
            return "", 0
        sink: list[str] = []
        if err_to is not None:
            self._err_sinks.append(self._collector(sink))
        try:
            out, status = self._builtin(argv, depth, stdin)
        finally:
            if err_to is not None:
                self._err_sinks.pop()
        if err_to is not None and err_to != "/dev/null":
            self._write(err_to, "".join(sink), False)
        out = self._cap(out)
        if out_to == "&2":
            self._err(out)
            out = ""
        elif out_to is not None:
            if out_to != "/dev/null":
                self._write(out_to, out, append)
            out = ""
        return out, status

    # Words

    @staticmethod
    def _is_lit(word: list, text: str) -> bool:
        return len(word) == 1 and word[0].kind == "lit" and not word[0].quoted and word[0].text == text

    @staticmethod
    def _assignment(word: list) -> bool:
        return bool(word) and word[0].kind == "lit" and not word[0].quoted and bool(ASSIGN_RE.match(word[0].text))

    def _assign_value(self, word: list, depth: int) -> tuple[str, str]:
        name = ASSIGN_RE.match(word[0].text).group(1)
        rest = [Seg("lit", word[0].text[len(name) + 1:], False)] + word[1:]
        return name, "".join(self._expand(rest, depth, split=False))

    def _home(self) -> str:
        return self.env.get("HOME", HOME)

    def _tilde(self, text: str, at_start: bool) -> str:
        if at_start and text.startswith("~") and (len(text) == 1 or text[1] == "/"):
            text = self._home() + text[1:]
        # Generous on purpose: -d @~/x and --post-file=~/x work as a beginner expects.
        return re.sub(r"(?<=[@=])~(?=/|$)", lambda _m: self._home(), text)

    def _var(self, name: str) -> str:
        if name == "?":
            return str(self.status)
        if name == "$":
            return "4242"
        if name == "0":
            return "sh"
        if name.isdigit():
            return ""
        return self.env.get(name, "")

    def _expand(self, word: list, depth: int, split: bool = True) -> list[str]:
        fields = [""]
        quoted_any = False
        globbing = False
        size = 0
        for idx, seg in enumerate(word):
            if seg.quoted:
                quoted_any = True
            if seg.kind == "lit":
                text = seg.text
                if not seg.quoted:
                    text = self._tilde(text, idx == 0)
                    globbing |= "*" in text
                size += len(text)
                if size > MAX_OUTPUT:
                    raise ShellLimit("expansion")
                fields[-1] = self._cap(fields[-1] + text)
                quoted_any |= bool(text)
                continue
            if seg.kind == "var":
                val = self._var(seg.text)
            else:
                val = self._substitute(seg.text, depth)
            val = self._cap(val)
            size += len(val)
            if size > MAX_OUTPUT:
                raise ShellLimit("expansion")
            if seg.quoted or not split:
                fields[-1] = self._cap(fields[-1] + val)
                continue
            pieces = val.split()
            if pieces:
                fields[-1] = self._cap(fields[-1] + pieces[0])
                fields += pieces[1:]
                if len(fields) > MAX_ARGS:
                    raise ShellLimit("expansion")
        if fields == [""] and not quoted_any:
            return []
        if not (split and globbing):
            return fields
        out: list[str] = []
        size = 0
        for f in fields:
            expanded = self._glob(f) if "*" in f else [f]
            size += sum(map(len, expanded))
            if size > MAX_OUTPUT or len(out) + len(expanded) > MAX_ARGS:
                raise ShellLimit("expansion")
            out += expanded
        return out

    def _substitute(self, src: str, depth: int) -> str:
        buf: list[str] = []
        status = self.status
        self._script(src, depth + 1, "", self._collector(buf))
        self.status = status
        return "".join(buf).rstrip("\n")

    def _glob(self, pattern: str) -> list[str]:
        absolute = self._path(pattern)
        components = absolute.split("/")
        hits = []
        size = 0
        for p in sorted(set(self.files) | self._dirs()):
            parts = p.split("/")
            if len(parts) == len(components) and all(
                    (not part.startswith(".") or pat.startswith(".")) and _star_match(pat, part)
                    for pat, part in zip(components, parts)):
                hit = p if pattern.startswith("/") else posixpath.relpath(p, self.cwd)
                size += len(hit)
                if size > MAX_OUTPUT or len(hits) >= MAX_ARGS:
                    raise ShellLimit("expansion")
                hits.append(hit)
        return hits or [pattern]

    def _collector(self, buf: list[str]) -> Callable[[str], None]:
        size = 0

        def collect(text: str) -> None:
            nonlocal size
            size += len(text)
            if size > MAX_OUTPUT:
                raise ShellLimit("output")
            buf.append(text)

        return collect

    # Files

    def _path(self, p: str) -> str:
        if not p.startswith("/"):
            p = posixpath.join(self.cwd, p)
        if len(p) > MAX_PATH or p.count("/") > MAX_PATH_PARTS:
            raise ShellLimit("path")
        return posixpath.normpath(p)

    def _dirs(self) -> set[str]:
        dirs = {"/"}
        for f in self.files:
            self._path(f)
            d = posixpath.dirname(f)
            while d not in dirs:
                dirs.add(d)
                d = posixpath.dirname(d)
        return dirs

    def _read(self, p: str) -> str | None:
        return self.files.get(self._path(p))

    def _write(self, p: str, text: str, append: bool) -> None:
        path = self._path(p)
        self.files[path] = self._cap((self.files.get(path, "") if append else "") + text)

    def _input(self, name: str, args: list[str], stdin: str) -> tuple[str | None, int]:
        """The text of the named files, or stdin when there are none."""
        if not args or args == ["-"]:
            return stdin, 0
        parts = []
        collect = self._collector(parts)
        for a in args:
            if a == "-":
                collect(stdin)
                continue
            path = self._path(a)
            if path in self.files:
                collect(self.files[path])
            elif path in self._dirs():
                self._err(f"{name}: {a}: Is a directory\n")
                return None, 1
            else:
                self._err(f"{name}: {a}: No such file or directory\n")
                return None, 1
        return self._cap("".join(parts)), 0

    # Commands

    def _builtin(self, argv: list[str], depth: int, stdin: str) -> tuple[str, int]:
        name = posixpath.basename(argv[0]) if argv[0].startswith("/") else argv[0]
        fn = {
            "cat": self._cat, "ls": self._ls, "echo": self._echo, "env": self._env, "printenv": self._env,
            "whoami": lambda a, d, s: (self.env.get("USER", "sam") + "\n", 0),
            "id": lambda a, d, s: ("uid=1000(sam) gid=1000(sam) groups=1000(sam),27(sudo)\n", 0),
            "base64": self._base64, "head": self._head, "curl": self._curl, "wget": self._wget,
            "sh": self._sh, "bash": self._sh, "cd": self._cd, "pwd": lambda a, d, s: (self.cwd + "\n", 0),
            "true": lambda a, d, s: ("", 0), "false": lambda a, d, s: ("", 1),
        }.get(name)
        if fn is None:
            self._err(f"sh: {argv[0]}: command not found\n")
            return "", 127
        return fn(argv[1:], depth, stdin)

    def _cat(self, args, depth, stdin):
        text, status = self._input("cat", [a for a in args if not (a.startswith("-") and a != "-")], stdin)
        return text or "", status

    def _ls(self, args, depth, stdin):
        flags = "".join(a[1:] for a in args if a.startswith("-") and a != "-")
        targets = [a for a in args if not a.startswith("-")] or ["."]
        show_hidden = "a" in flags or "A" in flags
        dirs = self._dirs()
        out, status = [], 0
        for t in targets:
            path = self._path(t)
            if path in self.files:
                out.append(t + "\n")
            elif path in dirs:
                kids = {posixpath.relpath(p, path).split("/")[0] for p in set(self.files) | dirs
                        if p != path and p.startswith(path.rstrip("/") + "/")}
                kids = sorted(k for k in kids if show_hidden or not k.startswith("."))
                if len(targets) > 1:
                    out.append(f"{t}:\n")
                out += [k + "\n" for k in kids]
            else:
                self._err(f"ls: cannot access '{t}': No such file or directory\n")
                status = 2
        return "".join(out), status

    def _echo(self, args, depth, stdin):
        newline, escapes = True, False
        while args and re.fullmatch(r"-[neE]+", args[0]):
            newline &= "n" not in args[0]
            escapes = "e" in args[0] or (escapes and "E" not in args[0])
            args = args[1:]
        text = " ".join(args)
        if escapes:
            text = text.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
        return text + ("\n" if newline else ""), 0

    def _env(self, args, depth, stdin):
        return "".join(f"{k}={v}\n" for k, v in sorted(self.env.items())), 0

    def _base64(self, args, depth, stdin):
        decode, wrap, files = False, 76, []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-d", "--decode", "-D"):
                decode = True
            elif a == "-w" and i + 1 < len(args):
                wrap, i = _count(args[i + 1], 76), i + 1
            elif a.startswith("-w") and a[2:].isdigit():
                wrap = _count(a[2:], 76)
            elif a.startswith("--wrap="):
                wrap = _count(a[7:], 76)
            elif a.startswith("-") and a != "-":
                pass
            else:
                files.append(a)
            i += 1
        data, status = self._input("base64", files, stdin)
        if data is None:
            return "", status
        if decode:
            try:
                raw = base64.b64decode("".join(data.split()), validate=True)
            except (binascii.Error, ValueError):
                self._err("base64: invalid input\n")
                return "", 1
            return raw.decode("utf-8", errors="replace"), 0
        enc = base64.b64encode(data.encode("utf-8")).decode()
        if wrap == 0:
            return enc, 0
        return "".join(enc[i:i + wrap] + "\n" for i in range(0, len(enc), wrap)) or "\n", 0

    def _head(self, args, depth, stdin):
        lines, chars, files = 10, None, []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-n", "-c") and i + 1 < len(args):
                if a == "-n":
                    lines = _count(args[i + 1], 10)
                else:
                    chars = _count(args[i + 1], MAX_OUTPUT)
                i += 1
            elif re.fullmatch(r"-n\d+", a) or re.fullmatch(r"-\d+", a):
                lines = _count(a.lstrip("-n"), 10)
            elif re.fullmatch(r"-c\d+", a):
                chars = _count(a[2:], MAX_OUTPUT)
            elif a.startswith("--lines="):
                lines = _count(a[8:], 10)
            elif a.startswith("--bytes="):
                chars = _count(a[8:], MAX_OUTPUT)
            elif not a.startswith("-") or a == "-":
                files.append(a)
            i += 1
        data, status = self._input("head", files, stdin)
        if data is None:
            return "", status
        if chars is not None:
            return data[:chars], 0
        return "".join(data.splitlines(keepends=True)[:lines]), 0

    def _sh(self, args, depth, stdin):
        script = None
        rest = list(args)
        while rest and rest[0].startswith("-") and rest[0] != "-c":
            rest.pop(0)
        if rest and rest[0] == "-c":
            if len(rest) < 2:
                self._err("sh: 0: -c requires an argument\n")
                return "", 2
            script = rest[1]
        elif rest:
            script = self._read(rest[0])
            if script is None:
                self._err(f"sh: 0: cannot open {rest[0]}: No such file\n")
                return "", 2
        else:
            script, stdin = stdin, ""
        buf: list[str] = []
        status = self._script(script, depth + 1, stdin, self._collector(buf))
        return "".join(buf), status

    def _cd(self, args, depth, stdin):
        target = self._path(args[0]) if args else self._home()
        if target not in self._dirs():
            self._err(f"sh: 1: cd: can't cd to {args[0] if args else target}\n")
            return "", 2
        self.cwd = target
        return "", 0

    # The network

    def _http(self, method: str, url: str, body: str) -> tuple[int, str] | None:
        """Send one request. None when the host does not resolve."""
        if "://" not in url:
            url = "http://" + url
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != self.hook_host:
            return None
        mine = f"/h/{self.hook_id}"
        path = parts.path or "/"
        if path == mine or path.startswith(mine + "/"):
            if len(self.requests) >= MAX_REQUESTS:
                raise ShellLimit("request")
            self.requests.append({
                "time": time.strftime("%H:%M:%S"), "method": method.upper(), "path": path,
                "query": parts.query, "body": body[:MAX_OUTPUT],
            })
            return 200, "ok"
        return 404, "Not found"

    def _data_file(self, ref: str, stdin: str, tool: str) -> str:
        if ref == "-":
            return stdin
        text = self._read(ref)
        if text is None:
            self._err(f"Warning: Couldn't read data from file \"{ref}\", this makes an empty POST.\n")
            return ""
        return text

    CURL_VALUE = {"d", "X", "H", "o", "A", "u", "e", "b", "m", "x", "F", "T", "w", "K"}
    CURL_LONG_VALUE = {
        "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--request", "--header",
        "--output", "--user-agent", "--user", "--referer", "--cookie", "--max-time", "--connect-timeout",
        "--proxy", "--url", "--form", "--upload-file", "--write-out", "--retry", "--config",
    }

    def _curl(self, args, depth, stdin):
        opts: list[tuple[str, str | None]] = []
        urls: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--":
                urls += args[i + 1:]
                break
            if a.startswith("--"):
                if a in self.CURL_LONG_VALUE:
                    if i + 1 >= len(args):
                        self._err(f"curl: option {a}: requires parameter\n")
                        return "", 2
                    opts.append((a, args[i + 1]))
                    i += 2
                    continue
                opts.append((a, None))
            elif a.startswith("-") and len(a) > 1:
                for k, ch in enumerate(a[1:], 1):
                    if ch in self.CURL_VALUE:
                        val = a[k + 1:]
                        if not val:
                            if i + 1 >= len(args):
                                self._err(f"curl: option -{ch}: requires parameter\n")
                                return "", 2
                            i += 1
                            val = args[i]
                        opts.append(("-" + ch, val))
                        break
                    opts.append(("-" + ch, None))
            else:
                urls.append(a)
            i += 1
        data: list[str] = []
        collect_data = self._collector(data)
        method = None
        silent = show = get = False
        out_file = upload = None
        for o, v in opts:
            if o in ("-d", "--data", "--data-ascii"):
                collect_data(self._data_file(v[1:], stdin, "curl").replace("\r", "").replace("\n", "")
                            if v.startswith("@") else v)
            elif o == "--data-binary":
                collect_data(self._data_file(v[1:], stdin, "curl") if v.startswith("@") else v)
            elif o == "--data-raw":
                collect_data(v)
            elif o == "--data-urlencode":
                name, sep, content = v.partition("@") if "@" in v and "=" not in v.split("@")[0] else ("", "", "")
                if sep:
                    collect_data((name + "=" if name else "") + quote_plus(self._data_file(content, stdin, "curl")))
                elif "=" in v:
                    name, _, content = v.partition("=")
                    collect_data((name + "=" if name else "") + quote_plus(content))
                else:
                    collect_data(quote_plus(v))
            elif o in ("-F", "--form"):
                name, _, content = v.partition("=")
                if content.startswith(("@", "<")):
                    content = self._data_file(content[1:], stdin, "curl")
                collect_data(f"{name}={content}")
            elif o in ("-X", "--request"):
                method = v
            elif o in ("-o", "--output"):
                out_file = v
            elif o in ("-T", "--upload-file"):
                upload = v
            elif o == "--url":
                urls.append(v)
            elif o in ("-s", "--silent"):
                silent = True
            elif o in ("-S", "--show-error"):
                show = True
            elif o in ("-G", "--get"):
                get = True
        if not urls:
            self._err("curl: no URL specified!\n")
            return "", 2
        body = self._cap("&".join(data))
        if upload is not None:
            body = self._data_file(upload, stdin, "curl")
        out, status = [], 0
        for url in urls:
            u, b = url, body
            if get and data:
                u, b = url + ("&" if "?" in url else "?") + body, ""
            m = method or ("PUT" if upload is not None else "POST" if b else "GET")
            res = self._http(m, u, b)
            if res is None:
                host = urlsplit(u if "://" in u else "http://" + u).hostname or u
                if not silent or show:
                    self._err(f"curl: (6) Could not resolve host: {host}\n")
                status = 6
                continue
            out.append(res[1])
        text = "".join(out)
        if out_file is not None:
            if out_file not in ("/dev/null", "-"):
                self._write(out_file, text, False)
            if out_file != "-":
                text = ""
        return text, status

    def _wget(self, args, depth, stdin):
        quiet = False
        out_doc: str | None = None
        method = None
        body: str | None = None
        urls: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("--"):
                key, _, val = a.partition("=")
                if key in ("--post-data", "--body-data"):
                    body = val
                elif key in ("--post-file", "--body-file"):
                    body = self._data_file(val, stdin, "wget")
                elif key == "--method":
                    method = val
                elif key == "--output-document":
                    out_doc = val
                elif key == "--quiet":
                    quiet = True
            elif a.startswith("-") and len(a) > 1:
                for k, ch in enumerate(a[1:], 1):
                    if ch in "OUoe":
                        val = a[k + 1:]
                        if not val and i + 1 < len(args):
                            i += 1
                            val = args[i]
                        if ch == "O":
                            out_doc = val
                        break
                    if ch == "q":
                        quiet = True
            else:
                urls.append(a)
            i += 1
        if not urls:
            self._err("wget: missing URL\n")
            return "", 1
        out, status = [], 0
        for url in urls:
            m = method or ("POST" if body is not None else "GET")
            res = self._http(m, url, body or "")
            if res is None:
                host = urlsplit(url if "://" in url else "http://" + url).hostname or url
                if not quiet:
                    self._err(f"wget: unable to resolve host address '{host}'\n")
                status = 4
                continue
            if res[0] == 404:
                if not quiet:
                    self._err("ERROR 404: Not Found.\n")
                status = 8
                continue
            if out_doc == "-":
                out.append(res[1])
            else:
                name = out_doc or posixpath.basename(urlsplit(url).path) or "index.html"
                self._write(name, res[1], False)
        return "".join(out), status
