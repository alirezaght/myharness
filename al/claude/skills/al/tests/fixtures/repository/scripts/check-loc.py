#!/usr/bin/env python3
"""Small faithful directory-based LOC checker fixture."""
import argparse
from pathlib import Path

ALLOWLIST_PATH = Path(__file__).with_name("check-loc.allowlist")
parser = argparse.ArgumentParser()
parser.add_argument("directories", nargs="*", help="Directories to scan")
args = parser.parse_args()
for value in args.directories:
    d = Path(value)
    if not d.is_dir():
        raise SystemExit(f"not a directory: {value}")
    for source in d.rglob("*.py"):
        if len(source.read_text().splitlines()) > 500:
            raise SystemExit(f"LOC limit exceeded: {source}")
