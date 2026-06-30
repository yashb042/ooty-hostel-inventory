#!/usr/bin/env python3
"""Migrate legacy SQLite schema (v1) to normalized schema (v2).

Usage:
    cd hostel-parsing
    python scripts/migrate_inventory_schema.py [--drop-legacy]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.storage import InventoryStore  # noqa: E402


LEGACY_TABLES = (
    "room_inventory",
    "property_inventory",
    "properties",
    "collection_runs",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate hostel inventory DB to schema v2.")
    parser.add_argument(
        "--drop-legacy",
        action="store_true",
        help="Drop legacy tables after successful migration",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="Path to inventory.db (default: from config.yaml)",
    )
    args = parser.parse_args()

    config = load_config()
    db_path = args.database or config.database_path
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    store = InventoryStore(db_path)
    store.seed_cities(
        tuple((city.slug, city.name, city.country, city.city_id) for city in config.cities)
    )

    with store.connect() as connection:
        stay_count = connection.execute("SELECT COUNT(*) FROM stay_date_inventory").fetchone()[0]
        room_count = connection.execute("SELECT COUNT(*) FROM room_type_inventory").fetchone()[0]
        snapshot_count = connection.execute("SELECT COUNT(*) FROM scrape_snapshots").fetchone()[0]
        hostel_count = connection.execute("SELECT COUNT(*) FROM hostels").fetchone()[0]
        legacy_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
                ",".join("?" for _ in LEGACY_TABLES)
            ),
            LEGACY_TABLES,
        ).fetchall()
        legacy_present = {row[0] for row in legacy_rows}

    print(f"Migrated database: {db_path}")
    print(f"  hostels:            {hostel_count}")
    print(f"  scrape_snapshots:   {snapshot_count}")
    print(f"  stay_date_inventory:{stay_count}")
    print(f"  room_type_inventory:{room_count}")
    print(f"  legacy tables left: {sorted(legacy_present) or 'none'}")

    if args.drop_legacy and legacy_present:
        connection = sqlite3.connect(db_path)
        try:
            for table in LEGACY_TABLES:
                if table in legacy_present:
                    connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.commit()
            connection.execute("VACUUM")
            print("Dropped legacy tables:", ", ".join(sorted(legacy_present)))
        finally:
            connection.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
