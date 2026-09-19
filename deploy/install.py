#!/usr/bin/env python3
"""Backup-first installer for the bot or the optional website reference app."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def backup_target(target: Path, backup_root: Path) -> Path | None:
    if not target.exists() or not any(target.iterdir()):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{target.name}-{stamp}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, backup, symlinks=True)
    return backup


def copy_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def install_bot(args) -> None:
    target = args.target.resolve()
    backup = backup_target(target, args.backup_root)
    copy_tree(ROOT / "bot", target)
    if args.venv:
        subprocess.run([sys.executable, "-m", "venv", str(target / "venv")], check=True)
        subprocess.run([str(target / "venv/bin/pip"), "install", "-r", str(ROOT / "requirements.txt")], check=True)
    print(f"Bot files installed at {target}")
    if backup:
        print(f"Backup: {backup}")
    print("Next: create /etc/qq-openclaw-bot.env from examples/bot.env.example, then install the example systemd units.")


def install_website(args) -> None:
    target = args.target.resolve()
    if (target / "app.py").exists() and not args.replace_app:
        raise SystemExit(
            "Refusing to replace an existing Flask app. Re-run with --replace-app only after reviewing docs/MIGRATION.md."
        )
    backup = backup_target(target, args.backup_root)
    copy_tree(ROOT / "website", target)
    print(f"Website reference app installed at {target}")
    if backup:
        print(f"Backup: {backup}")
    print("Next: create a protected environment file from examples/website.env.example and run with Gunicorn behind HTTPS.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("bot", "website"))
    parser.add_argument("--target", type=Path)
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/qq-openclaw-bot"))
    parser.add_argument("--venv", action="store_true", help="Create venv and install Python dependencies (bot only).")
    parser.add_argument("--replace-app", action="store_true", help="Allow replacing an existing website app.py.")
    args = parser.parse_args()
    if args.target is None:
        args.target = Path("/opt/qq-openclaw-bot" if args.role == "bot" else "/opt/qq-bot-console")
    (install_bot if args.role == "bot" else install_website)(args)


if __name__ == "__main__":
    main()
