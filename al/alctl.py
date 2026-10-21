#!/usr/bin/env python3
"""Entry point using only the installer bundled beside this file."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
