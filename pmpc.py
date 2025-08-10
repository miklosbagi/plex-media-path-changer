#!/usr/bin/env python3
"""
Rewrite Plex base paths inside the main SQLite DB safely.

Usage (dry run first):
  python3 plex_update_path.py \
    --db-dir "config/Plug-in Support/Databases" \
    --old "archive-media" \
    --new "media" \
    --dry-run

Then run for real (omit --dry-run) with Plex stopped.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# Columns that may store filesystem paths across Plex versions
CANDIDATE_COLUMNS = {"root_path", "file", "path"}


def find_main_db(db_dir: str) -> str:
    p = os.path.join(db_dir, "com.plexapp.plugins.library.db")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"Could not find {p}")
    return p


def backup_db(db_path: str):
    """Back up DB and any sidecar files (-wal/-shm) into a backups/ folder."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(db_path)
    bak_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(bak_dir, exist_ok=True)

    copies = []
    for suffix in ("", "-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            dst = os.path.join(bak_dir, f"{base}{suffix}.{ts}.bak")
            shutil.copy2(src, dst)
            copies.append(dst)
    return copies


def safe_tables(conn: sqlite3.Connection):
    """
    Return only 'real' tables.
    Skip virtual/extension-backed tables (spellfix1/fts/etc) that can error on inspection.
    """
    cur = conn.execute(
        """
        SELECT name, COALESCE(sql,'') AS sql
        FROM sqlite_master
        WHERE type='table'
        """
    )
    rows = cur.fetchall()
    out = []
    for name, sql in rows:
        s = (sql or "").upper()
        # Avoid virtual tables, or anything with USING <module> (e.g., FTS5, spellfix1, etc.)
        if "VIRTUAL TABLE" in s:
            continue
        if " USING " in s:
            continue
        out.append(name)
    return out


def table_columns(conn: sqlite3.Connection, table: str):
    """Return column names for a table; be defensive if it errors."""
    try:
        cur = conn.execute(f'PRAGMA table_info("{table}");')
        return [row[1] for row in cur.fetchall()]  # index 1 = column name
    except sqlite3.OperationalError:
        return []


def count_hits(conn: sqlite3.Connection, table: str, column: str, needle: str) -> int:
    cur = conn.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE INSTR("{column}", ?) > 0;',
        (needle,),
    )
    return int(cur.fetchone()[0])


def apply_replace(
    conn: sqlite3.Connection, table: str, column: str, old: str, new: str
) -> int:
    cur = conn.execute(
        f'UPDATE "{table}" '
        f'SET "{column}" = REPLACE("{column}", ?, ?) '
        f'WHERE INSTR("{column}", ?) > 0;',
        (old, new, old),
    )
    return cur.rowcount


def try_integrity_check(conn: sqlite3.Connection) -> str:
    """
    Run PRAGMA integrity_check. If custom tokenizers/extensions are missing,
    gracefully skip and report that we skipped.
    """
    try:
        cur = conn.execute("PRAGMA integrity_check;")
        res = cur.fetchone()
        return res[0] if res else "unknown"
    except sqlite3.OperationalError as e:
        # e.g., "unknown tokenizer: collating" or missing spellfix1
        return f"skipped ({e})"


def vacuum_in_fresh_connection(db_path: str):
    """Run VACUUM using a fresh connection to avoid extension hiccups."""
    try:
        c2 = sqlite3.connect(db_path)
        try:
            c2.execute("VACUUM;")
        finally:
            c2.close()
        print("VACUUM complete.")
    except sqlite3.OperationalError as e:
        print(f"VACUUM skipped ({e}).")


def main():
    ap = argparse.ArgumentParser(
        description="Rewrite base path segments in Plex SQLite DB."
    )
    ap.add_argument(
        "--db-dir",
        help="Directory containing com.plexapp.plugins.library.db",
    )
    ap.add_argument(
        "--db-file",
        help="Path to com.plexapp.plugins.library.db (overrides --db-dir)",
    )
    ap.add_argument(
        "--old",
        required=True,
        help="Old path segment to replace (e.g. 'archive-media' or '/archive-media/')",
    )
    ap.add_argument(
        "--new",
        required=True,
        help="New segment (e.g. 'media' or '/media/')",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying the DB",
    )
    args = ap.parse_args()

    if not args.db_file and not args.db_dir:
        print("Provide --db-dir or --db-file", file=sys.stderr)
        sys.exit(2)

    db_path = args.db_file or find_main_db(args.db_dir)

    if args.dry_run:
        print("== DRY RUN ==")
    print(f"DB: {db_path}")
    print(f"Replacing occurrences of '{args.old}' → '{args.new}'")

    if not args.dry_run:
        backups = backup_db(db_path)
        print("Backed up:", *backups, sep="\n  ")

    # Work on the DB
    conn = sqlite3.connect(db_path)
    try:
        # Expect Plex to be stopped; use a simple journal mode
        conn.execute("PRAGMA foreign_keys=OFF;")
        conn.execute("PRAGMA journal_mode=DELETE;")
        conn.execute("BEGIN;")

        tables = safe_tables(conn)
        total_hits = 0
        total_updated = 0
        touched = []

        for table in tables:
            cols = set(table_columns(conn, table))
            candidate_cols = sorted(CANDIDATE_COLUMNS & cols)
            if not candidate_cols:
                continue

            for col in candidate_cols:
                # Guard against odd tables that still error on SELECT
                try:
                    hits = count_hits(conn, table, col, args.old)
                except sqlite3.OperationalError as e:
                    print(f"Skipping {table}.{col} due to error: {e}")
                    continue

                if hits > 0:
                    touched.append((table, col, hits))
                    total_hits += hits
                    if not args.dry_run:
                        try:
                            updated = apply_replace(conn, table, col, args.old, args.new)
                            total_updated += updated
                        except sqlite3.OperationalError as e:
                            print(f"Failed updating {table}.{col}: {e}")

        if args.dry_run:
            if touched:
                print("\nWould touch the following columns:")
                for t, c, n in touched:
                    print(f"  {t}.{c}: {n} row(s)")
            else:
                print("\nNo matches found.")
            print(f"\nTotal rows containing '{args.old}': {total_hits}")
            conn.rollback()
            return

        if touched:
            print("\nUpdated columns:")
            for t, c, n in touched:
                print(f"  {t}.{c}: {n} row(s) matched")
        else:
            print("\nNo matches updated.")

        print(f"\nTotal rows updated: {total_updated}")

        # Integrity check (skip if tokenizer/ext missing)
        result = try_integrity_check(conn)
        print(f"\nPRAGMA integrity_check: {result}")
        if result not in ("ok",) and not result.startswith("skipped"):
            print("Integrity check failed; rolling back.", file=sys.stderr)
            conn.rollback()
            sys.exit(1)

        # Commit, then VACUUM in a fresh connection
        conn.commit()
    finally:
        conn.close()

    vacuum_in_fresh_connection(db_path)
    print("\nDone. Restart Plex and refresh libraries if needed.")


if __name__ == "__main__":
    main()
