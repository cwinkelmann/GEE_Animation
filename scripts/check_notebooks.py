#!/usr/bin/env python3
"""Fail when a tracked notebook would embarrass the repo.

Three rules, all of which have bitten or nearly bitten:
- no stored outputs (a notebook saved after a test run bloats every diff),
- no execution counts (same failure, smaller symptom),
- no code cell contains the operator's Earth Engine project id — a student
  running a cell must never silently render on HNEE's quota. Markdown may
  mention it (the carrot deployment docs do, honestly).
"""
import json
import subprocess
import sys

OPERATOR_PROJECT = "hnee-331218"


def problems(path) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            nb = json.load(f)
    except (OSError, ValueError) as e:
        return [f"unreadable as a notebook: {e}"]
    out = []
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        if cell.get("outputs"):
            out.append(f"cell {i}: stored outputs")
        if cell.get("execution_count") is not None:
            out.append(f"cell {i}: execution_count set")
        if OPERATOR_PROJECT in "".join(cell.get("source", [])):
            out.append(f"cell {i}: code hardcodes {OPERATOR_PROJECT}")
    return out


def main(paths=None) -> int:
    if not paths:
        paths = subprocess.run(["git", "ls-files", "*.ipynb"], check=True,
                               capture_output=True, text=True).stdout.split()
    bad = 0
    for p in paths:
        for msg in problems(p):
            print(f"{p}: {msg}")
            bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
