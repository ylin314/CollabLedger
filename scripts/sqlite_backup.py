#!/usr/bin/env python3
"""Verified SQLite backup/restore utility for local and container operation."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def resolved(path: str) -> Path:
    return Path(path).expanduser().resolve()


def integrity(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        raise RuntimeError(f"SQLite integrity_check failed for {path}: {result}")


def backup(database: Path, output: Path) -> None:
    if not database.is_file():
        raise FileNotFoundError(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output == database:
        raise ValueError("backup output must differ from database")
    temporary = output.with_suffix(output.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    source = sqlite3.connect(database)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    integrity(temporary)
    temporary.replace(output)
    print(f"verified backup created: {output}")


def restore(database: Path, source: Path, yes: bool) -> None:
    integrity(source)
    if not yes:
        raise RuntimeError("restore requires --yes; stop the application before restoring")
    database.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if database.exists():
        safety = database.with_name(f"{database.name}.pre-restore-{stamp}.bak")
        backup(database, safety)
    temporary = database.with_suffix(database.suffix + ".restore-partial")
    if temporary.exists():
        temporary.unlink()
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(temporary)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    integrity(temporary)
    temporary.replace(database)
    integrity(database)
    print(f"database restored and verified: {database}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_backup = sub.add_parser("backup")
    p_backup.add_argument("--database", required=True)
    p_backup.add_argument("--output", required=True)
    p_restore = sub.add_parser("restore")
    p_restore.add_argument("--database", required=True)
    p_restore.add_argument("--input", required=True)
    p_restore.add_argument("--yes", action="store_true")
    p_check = sub.add_parser("check")
    p_check.add_argument("--database", required=True)
    args = parser.parse_args()
    if args.command == "backup":
        backup(resolved(args.database), resolved(args.output))
    elif args.command == "restore":
        restore(resolved(args.database), resolved(args.input), args.yes)
    else:
        path = resolved(args.database); integrity(path); print(f"integrity ok: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
