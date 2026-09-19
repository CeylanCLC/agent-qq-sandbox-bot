#!/usr/bin/env python3
"""Compatibility launcher for deployments that used the historical root path."""
from pathlib import Path
import runpy
import sys


BOT_DIR = Path(__file__).resolve().parent / "bot"
sys.path.insert(0, str(BOT_DIR))
runpy.run_path(str(BOT_DIR / "qq_openclaw_bridge.py"), run_name="__main__")
