"""File- and shell-tool suite for the coding agent (MVP scope).

Security model: the agent executes inside the working directory with the
user's own privileges. ``run_command`` is intentionally unsandboxed — this is
a personal local CLI, not a remote service. The size caps below protect the
model's context window, not a security boundary.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List

READ_MAX_BYTES = 64 * 1024
WRITE_MAX_BYTES = 1024 * 1024
OUTPUT_MAX_BYTES = 24 * 1024
LIST_MAX_ENTRIES = 500


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    return head + "\n...[truncated {} chars]...\n".format(len(text) - limit) + tail


# Tolerant key/value extractor for malformed tool-call JSON. Some models emit
# literal newlines instead of \n escapes inside long string values, which breaks
# strict json.loads; this regex recovers the common `write_file(path, content)`
# shape without requiring the whole document to be valid JSON.
_ARG_PATTERN = re.compile(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)

# Manual unescaping: only the JSON escapes that appear in code; avoids
# codecs.unicode_escape, which corrupts non-ASCII (CJK) text.
_ESCAPE_RE = re.compile(r"\\([\"\\/bfnrt]|u[0-9a-fA-F]{4})")
_ESCAPE_MAP = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _unescape(value: str) -> str:
    def repl(match):
        ch = match.group(1)
        if ch in _ESCAPE_MAP:
            return _ESCAPE_MAP[ch]
        return chr(int(ch[1:], 16))  # \uXXXX

    return _ESCAPE_RE.sub(repl, value)


def salvage_arguments(raw: str) -> Dict:
    """Best-effort extraction of string arguments from malformed JSON.

    Returns the recovered ``{key: value}`` dict (first occurrence of each key
    wins) or an empty dict when nothing usable was found.
    """
    out: Dict = {}
    for key, value in _ARG_PATTERN.findall(raw):
        if key in out:
            continue
        out[key] = _unescape(value)
    return out


class ToolRunner:
    """Resolves tool calls against the real filesystem."""

    def __init__(self, workdir: str, dry_run: bool = False, cmd_timeout: int = 60) -> None:
        self.workdir = Path(workdir).resolve()
        self.dry_run = dry_run
        self.cmd_timeout = cmd_timeout

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.workdir / p
        return p

    def dispatch(self, name: str, arguments: Dict) -> str:
        if self.dry_run:
            return "Dry-run: {}({}) not executed".format(
                name, json.dumps(arguments, ensure_ascii=False)
            )
        if set(arguments) == {"_raw"}:
            arguments = salvage_arguments(arguments["_raw"])
            if not arguments:
                return "Error: could not parse tool arguments for {}".format(name)
        handler = getattr(self, "tool_" + name, None)
        if handler is None:
            return "Error: unknown tool {!r}".format(name)
        try:
            return handler(**arguments)
        except TypeError as err:
            return "Error: invalid arguments for {}: {}".format(name, err)

    def tool_read_file(self, path: str) -> str:
        p = self._resolve(path)
        try:
            data = p.read_bytes()
        except OSError as err:
            return "Error: cannot read {}: {}".format(p, err)
        if len(data) > READ_MAX_BYTES:
            text = _truncate(data.decode("utf-8", "replace"), READ_MAX_BYTES)
            return text + "\n...[file truncated at {} bytes, total {}]...".format(
                READ_MAX_BYTES, len(data)
            )
        return data.decode("utf-8", "replace")

    def tool_write_file(self, path: str, content: str) -> str:
        size = len(content.encode("utf-8"))
        if size > WRITE_MAX_BYTES:
            return "Error: content is {} bytes, exceeds {} — refused".format(size, WRITE_MAX_BYTES)
        p = self._resolve(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as err:
            return "Error: cannot write {}: {}".format(p, err)
        return "Wrote {} bytes to {}".format(size, p)

    def tool_list_dir(self, path: str) -> str:
        p = self._resolve(path)
        try:
            entries = sorted(os.listdir(p))
        except OSError as err:
            return "Error: cannot list {}: {}".format(p, err)
        if len(entries) > LIST_MAX_ENTRIES:
            omitted = len(entries) - LIST_MAX_ENTRIES
            entries = [*entries[:LIST_MAX_ENTRIES], "...[{}+ entries omitted]...".format(omitted)]
        if not entries:
            return "{} is empty".format(p)
        lines = []
        for entry in entries:
            marker = "/" if (p / entry).is_dir() else ""
            lines.append(entry + marker)
        return "\n".join(lines)

    def tool_run_command(self, command: str) -> str:
        try:
            proc = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=str(self.workdir),
                capture_output=True,
                text=True,
                timeout=self.cmd_timeout,
            )
        except subprocess.TimeoutExpired:
            return "Error: command timed out after {}s".format(self.cmd_timeout)
        except OSError as err:
            return "Error: cannot run command: {}".format(err)
        out = proc.stdout or ""
        if proc.stderr:
            out = (out + "\n[stderr]\n" + proc.stderr) if out else "[stderr]\n" + proc.stderr
        out = _truncate(out, OUTPUT_MAX_BYTES)
        if out:
            return "exit_code={}\n{}".format(proc.returncode, out)
        return "exit_code={}".format(proc.returncode)


TOOL_SPECS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file and return its text. "
                "Relative paths resolve against the working directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write text to a file, creating parent directories as needed. "
                "Overwrites existing files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries of a directory. Directories are suffixed with '/'.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command (bash -lc) in the working directory; "
                "returns exit code plus output."
            ),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Shell command line."}},
                "required": ["command"],
            },
        },
    },
]
