#!/usr/bin/env python3
"""Survey the SBPL surface actually used by the 511 Apple system profiles.

Output is the evidence base for policy/agent-sandbox.sb: which operations,
which filters, which modifiers, and which structural forms appear in the real
grammar shipped in /System/Library/Sandbox/Profiles.
"""
import collections
import glob
import os
import re
import sys

PROFILE_DIR = "/System/Library/Sandbox/Profiles"

COMMENT_RE = re.compile(r";[^\n]*")
OP_RE = re.compile(r"\((allow|deny)\s+([^\s()]+)")
FILTER_RE = re.compile(r"\(([a-z][a-z0-9-]+)\s*[\s\")]")
MOD_RE = re.compile(r"\(with\s+([a-z-]+)")
FORM_RE = re.compile(r"\((import|define|let|when|unless|if|with-filter|require-all|require-any|version|regex-quote|string-append|param|loadframework|system-attribute|require-not)\b")


def main() -> int:
    ops = collections.Counter()
    filters = collections.Counter()
    mods = collections.Counter()
    forms = collections.Counter()
    files = 0
    rows = 0
    for path in glob.glob(os.path.join(PROFILE_DIR, "*.sb")):
        with open(path, errors="ignore") as handle:
            text = COMMENT_RE.sub("", handle.read())
        files += 1
        for match in OP_RE.finditer(text):
            rows += 1
            ops[match.group(2).rstrip(")")] += 1
        for match in FILTER_RE.finditer(text):
            filters[match.group(1)] += 1
        for match in MOD_RE.finditer(text):
            mods[match.group(1)] += 1
        for match in FORM_RE.finditer(text):
            forms[match.group(1)] += 1

    print(f"profiles parsed: {files}   allow/deny clauses: {rows}\n")
    for title, counter, limit in (
        ("OPERATIONS", ops, 40),
        ("FILTERS", filters, 40),
        ("MODIFIERS (with ...)", mods, 25),
        ("STRUCTURAL FORMS", forms, 25),
    ):
        print(f"== {title} ==")
        for name, count in counter.most_common(limit):
            print(f"{count:6d}  {name}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
