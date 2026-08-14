"""Audit and repair historical FLARE experiment rows with swapped identity fields.

The original positional INSERT used CSV field order (timestamp, run_id, ...)
against a table whose first columns are (run_id, timestamp, ...). This utility
detects only rows matching that unmistakable type pattern. It is read-only
unless ``--apply`` is provided, and it creates a SQLite backup before writing.

Run while the API and orchestrator are stopped:

    python scripts/migrate_experiment_db.py
    python scripts/migrate_experiment_db.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "experiments" / "experiment.db"
SUSPECT_PREDICATE = (
    "typeof(run_id) IN ('text', 'integer', 'real') "
    "AND typeof(timestamp) = 'text' "
    "AND CAST(run_id AS REAL) > 1000000000"
)


def count_suspect_rows(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM runs WHERE {SUSPECT_PREDICATE}"
    ).fetchone()
    return int(row[0]) if row else 0


def create_backup(source: sqlite3.Connection, db_path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.backup-{timestamp}{db_path.suffix}")
    with sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    return backup_path


def migrate(db_path: Path, apply: bool = False) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"Experiment database not found: {db_path}")

    with sqlite3.connect(db_path, timeout=5.0) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "runs" not in tables:
            raise RuntimeError("Database does not contain the expected 'runs' table")

        suspect_count = count_suspect_rows(conn)
        print(f"Database: {db_path}")
        print(f"Rows with swapped run_id/timestamp signature: {suspect_count}")

        if not apply or suspect_count == 0:
            if suspect_count and not apply:
                print("Dry run only. Re-run with --apply after stopping FLARE services.")
            return suspect_count

        backup_path = create_backup(conn, db_path)
        print(f"Backup created: {backup_path}")

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"""
                UPDATE runs
                SET run_id = CAST(timestamp AS TEXT),
                    timestamp = CAST(run_id AS REAL)
                WHERE {SUSPECT_PREDICATE}
                """
            )
            remaining = count_suspect_rows(conn)
            if remaining:
                raise RuntimeError(f"Migration validation failed: {remaining} rows remain")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        print(f"Migrated {suspect_count} rows successfully.")
        return suspect_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a backup and repair detected rows (default is read-only audit)",
    )
    args = parser.parse_args()
    migrate(args.db, apply=args.apply)


if __name__ == "__main__":
    main()
