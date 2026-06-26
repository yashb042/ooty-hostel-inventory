from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from .inventory_metrics import occupancy_pct, room_record_units
from .parser import PropertySummaryRecord, RoomInventoryRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS cities (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    hostelworld_city_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hostels (
    hostel_id INTEGER PRIMARY KEY,
    city_slug TEXT NOT NULL,
    name TEXT NOT NULL,
    country TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (city_slug) REFERENCES cities(slug)
);

CREATE TABLE IF NOT EXISTS scrape_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    hostels_count INTEGER,
    stay_dates_count INTEGER,
    requests_total INTEGER,
    requests_failed INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS stay_date_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER,
    hostel_id INTEGER NOT NULL,
    city_slug TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    total_units INTEGER NOT NULL DEFAULT 0,
    available_units INTEGER NOT NULL DEFAULT 0,
    sold_units INTEGER NOT NULL DEFAULT 0,
    occupancy_pct REAL,
    total_dorm_beds INTEGER NOT NULL DEFAULT 0,
    total_private_rooms INTEGER NOT NULL DEFAULT 0,
    total_private_beds INTEGER NOT NULL DEFAULT 0,
    dorm_room_count INTEGER NOT NULL DEFAULT 0,
    private_room_count INTEGER NOT NULL DEFAULT 0,
    available_dorm_rooms INTEGER NOT NULL DEFAULT 0,
    available_private_rooms INTEGER NOT NULL DEFAULT 0,
    lowest_dorm_price REAL,
    lowest_private_price REAL,
    lowest_price REAL,
    currency TEXT,
    has_availability INTEGER NOT NULL DEFAULT 0,
    free_cancellation INTEGER NOT NULL DEFAULT 0,
    promotion_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(hostel_id, stay_date, scraped_at),
    FOREIGN KEY (snapshot_id) REFERENCES scrape_snapshots(id),
    FOREIGN KEY (hostel_id) REFERENCES hostels(hostel_id),
    FOREIGN KEY (city_slug) REFERENCES cities(slug)
);

CREATE TABLE IF NOT EXISTS room_type_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER,
    hostel_id INTEGER NOT NULL,
    city_slug TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    room_id INTEGER NOT NULL,
    room_name TEXT NOT NULL,
    room_category TEXT NOT NULL,
    basic_type TEXT,
    grade TEXT,
    capacity INTEGER,
    is_ensuite INTEGER NOT NULL DEFAULT 0,
    beds_available INTEGER,
    rooms_available INTEGER,
    total_units INTEGER NOT NULL DEFAULT 0,
    available_units INTEGER NOT NULL DEFAULT 0,
    sold_units INTEGER NOT NULL DEFAULT 0,
    lowest_price REAL,
    currency TEXT,
    has_availability INTEGER NOT NULL DEFAULT 0,
    UNIQUE(hostel_id, stay_date, scraped_at, room_id),
    FOREIGN KEY (snapshot_id) REFERENCES scrape_snapshots(id),
    FOREIGN KEY (hostel_id) REFERENCES hostels(hostel_id),
    FOREIGN KEY (city_slug) REFERENCES cities(slug)
);

CREATE INDEX IF NOT EXISTS idx_stay_date_inventory_stay
    ON stay_date_inventory(stay_date);
CREATE INDEX IF NOT EXISTS idx_stay_date_inventory_scraped
    ON stay_date_inventory(scraped_at);
CREATE INDEX IF NOT EXISTS idx_stay_date_inventory_hostel
    ON stay_date_inventory(hostel_id);
CREATE INDEX IF NOT EXISTS idx_stay_date_inventory_city
    ON stay_date_inventory(city_slug);
CREATE INDEX IF NOT EXISTS idx_stay_date_inventory_snapshot
    ON stay_date_inventory(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_room_type_inventory_stay
    ON room_type_inventory(stay_date);
CREATE INDEX IF NOT EXISTS idx_room_type_inventory_scraped
    ON room_type_inventory(scraped_at);
CREATE INDEX IF NOT EXISTS idx_room_type_inventory_hostel
    ON room_type_inventory(hostel_id);
CREATE INDEX IF NOT EXISTS idx_room_type_inventory_city
    ON room_type_inventory(city_slug);
CREATE INDEX IF NOT EXISTS idx_room_type_inventory_snapshot
    ON room_type_inventory(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_hostels_city ON hostels(city_slug);
CREATE INDEX IF NOT EXISTS idx_scrape_snapshots_started ON scrape_snapshots(started_at);
"""


class InventoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            if self._has_legacy_tables(connection) and not self._has_new_data(connection):
                self._migrate_legacy_schema(connection)

    def seed_cities(
        self,
        cities: tuple[tuple[str, str, str, int], ...],
    ) -> None:
        with self.connect() as connection:
            for slug, name, country, city_id in cities:
                connection.execute(
                    """
                    INSERT INTO cities (slug, name, country, hostelworld_city_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        name = excluded.name,
                        country = excluded.country,
                        hostelworld_city_id = excluded.hostelworld_city_id
                    """,
                    (slug, name, country, city_id),
                )

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _has_legacy_tables(self, connection: sqlite3.Connection) -> bool:
        return self._table_exists(connection, "property_inventory")

    def _has_new_data(self, connection: sqlite3.Connection) -> bool:
        if not self._table_exists(connection, "stay_date_inventory"):
            return False
        row = connection.execute("SELECT COUNT(*) AS n FROM stay_date_inventory").fetchone()
        return bool(row and row["n"] > 0)

    def _city_slug_for_name(
        self,
        connection: sqlite3.Connection,
        city_name: str | None,
    ) -> str | None:
        if not city_name:
            return None
        row = connection.execute(
            "SELECT slug FROM cities WHERE name = ? COLLATE NOCASE",
            (city_name,),
        ).fetchone()
        if row:
            return str(row["slug"])
        return city_name.strip().lower().replace(" ", "-")

    @staticmethod
    def _legacy_room_units(row: sqlite3.Row) -> tuple[int, int, int]:
        return room_record_units(
            type(
                "LegacyRoom",
                (),
                {
                    "capacity": row["capacity"],
                    "beds_available": row["beds_available"],
                    "rooms_available": row["rooms_available"],
                },
            )()
        )

    def _migrate_legacy_schema(self, connection: sqlite3.Connection) -> None:
        if not self._table_exists(connection, "collection_runs"):
            return

        for row in connection.execute(
            """
            SELECT id, started_at, finished_at, status, properties_count,
                   stay_dates_count, requests_total, requests_failed, notes
            FROM collection_runs
            """
        ).fetchall():
            connection.execute(
                """
                INSERT OR IGNORE INTO scrape_snapshots (
                    id, started_at, finished_at, status, hostels_count,
                    stay_dates_count, requests_total, requests_failed, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["started_at"],
                    row["finished_at"],
                    row["status"],
                    row["properties_count"],
                    row["stay_dates_count"],
                    row["requests_total"],
                    row["requests_failed"],
                    row["notes"],
                ),
            )

        if self._table_exists(connection, "properties"):
            for row in connection.execute(
                "SELECT property_id, name, city, country, updated_at FROM properties"
            ).fetchall():
                city_slug = self._city_slug_for_name(connection, row["city"]) or "unknown"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO hostels (hostel_id, city_slug, name, country, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["property_id"],
                        city_slug,
                        row["name"],
                        row["country"],
                        row["updated_at"],
                    ),
                )

        room_agg: dict[tuple[int, str, str], tuple[int, int, int]] = {}
        if self._table_exists(connection, "room_inventory"):
            for row in connection.execute(
                """
                SELECT property_id, stay_date, scraped_at, capacity,
                       beds_available, rooms_available
                FROM room_inventory
                """
            ).fetchall():
                key = (row["property_id"], row["stay_date"], row["scraped_at"])
                total, available, sold = self._legacy_room_units(row)
                prev = room_agg.get(key, (0, 0, 0))
                room_agg[key] = (prev[0] + total, prev[1] + available, prev[2] + sold)

        for row in connection.execute(
            """
            SELECT run_id, property_id, city, stay_date, scraped_at,
                   total_dorm_beds, total_private_beds,
                   total_private_rooms, total_dorm_beds AS _tdb,
                   dorm_room_count, private_room_count,
                   available_dorm_rooms, available_private_rooms,
                   lowest_dorm_price, lowest_private_price, lowest_price, currency,
                   has_availability, free_cancellation_available, promotion_count
            FROM property_inventory
            """
        ).fetchall():
            city_slug = self._city_slug_for_name(connection, row["city"]) or "unknown"
            key = (row["property_id"], row["stay_date"], row["scraped_at"])
            agg = room_agg.get(key)
            if agg:
                total_units, available_units, sold_units = agg
            else:
                total_units = int(row["total_dorm_beds"] or 0) + int(row["total_private_beds"] or 0)
                available_units = total_units if row["has_availability"] else 0
                sold_units = max(0, total_units - available_units)

            connection.execute(
                """
                INSERT OR IGNORE INTO stay_date_inventory (
                    snapshot_id, hostel_id, city_slug, stay_date, scraped_at,
                    total_units, available_units, sold_units, occupancy_pct,
                    total_dorm_beds, total_private_rooms, total_private_beds,
                    dorm_room_count, private_room_count,
                    available_dorm_rooms, available_private_rooms,
                    lowest_dorm_price, lowest_private_price, lowest_price, currency,
                    has_availability, free_cancellation, promotion_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["run_id"],
                    row["property_id"],
                    city_slug,
                    row["stay_date"],
                    row["scraped_at"],
                    total_units,
                    available_units,
                    sold_units,
                    occupancy_pct(total_units, sold_units),
                    row["total_dorm_beds"],
                    row["total_private_rooms"],
                    row["total_private_beds"],
                    row["dorm_room_count"],
                    row["private_room_count"],
                    row["available_dorm_rooms"],
                    row["available_private_rooms"],
                    row["lowest_dorm_price"],
                    row["lowest_private_price"],
                    row["lowest_price"],
                    row["currency"],
                    row["has_availability"],
                    row["free_cancellation_available"],
                    row["promotion_count"],
                ),
            )

        if self._table_exists(connection, "room_inventory"):
            for row in connection.execute(
                """
                SELECT run_id, property_id, city, stay_date, scraped_at,
                       room_id, room_name, room_category, basic_type, grade,
                       capacity, ensuite, beds_available, rooms_available,
                       lowest_price, currency, has_availability
                FROM room_inventory
                """
            ).fetchall():
                city_slug = self._city_slug_for_name(connection, row["city"]) or "unknown"
                total, available, sold = self._legacy_room_units(row)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO room_type_inventory (
                        snapshot_id, hostel_id, city_slug, stay_date, scraped_at,
                        room_id, room_name, room_category, basic_type, grade,
                        capacity, is_ensuite, beds_available, rooms_available,
                        total_units, available_units, sold_units,
                        lowest_price, currency, has_availability
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["run_id"],
                        row["property_id"],
                        city_slug,
                        row["stay_date"],
                        row["scraped_at"],
                        row["room_id"],
                        row["room_name"],
                        row["room_category"],
                        row["basic_type"],
                        row["grade"],
                        row["capacity"],
                        row["ensuite"],
                        row["beds_available"],
                        row["rooms_available"],
                        total,
                        available,
                        sold,
                        row["lowest_price"],
                        row["currency"],
                        row["has_availability"],
                    ),
                )

    def start_run(self, started_at: datetime) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scrape_snapshots (started_at, status)
                VALUES (?, 'running')
                """,
                (started_at.isoformat(),),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        status: str,
        properties_count: int,
        stay_dates_count: int,
        requests_total: int,
        requests_failed: int,
        notes: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scrape_snapshots
                SET finished_at = ?, status = ?, hostels_count = ?,
                    stay_dates_count = ?, requests_total = ?, requests_failed = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    finished_at.isoformat(),
                    status,
                    properties_count,
                    stay_dates_count,
                    requests_total,
                    requests_failed,
                    notes,
                    run_id,
                ),
            )

    def upsert_hostel(
        self,
        hostel_id: int,
        name: str,
        *,
        city_slug: str,
        country: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        timestamp = (updated_at or datetime.now(timezone.utc)).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO hostels (hostel_id, city_slug, name, country, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(hostel_id) DO UPDATE SET
                    name = excluded.name,
                    city_slug = excluded.city_slug,
                    country = excluded.country,
                    updated_at = excluded.updated_at
                """,
                (hostel_id, city_slug, name, country, timestamp),
            )

    def upsert_property(
        self,
        property_id: int,
        name: str,
        *,
        city: str | None = None,
        city_slug: str | None = None,
        country: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        slug = city_slug
        with self.connect() as connection:
            if not slug and city:
                slug = self._city_slug_for_name(connection, city) or city.lower()
            slug = slug or "unknown"
        self.upsert_hostel(
            property_id,
            name,
            city_slug=slug,
            country=country,
            updated_at=updated_at,
        )

    def save_stay_date_inventory(
        self,
        snapshot_id: int,
        summary: PropertySummaryRecord,
        *,
        city_slug: str,
        total_units: int,
        available_units: int,
        sold_units: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO stay_date_inventory (
                    snapshot_id, hostel_id, city_slug, stay_date, scraped_at,
                    total_units, available_units, sold_units, occupancy_pct,
                    total_dorm_beds, total_private_rooms, total_private_beds,
                    dorm_room_count, private_room_count,
                    available_dorm_rooms, available_private_rooms,
                    lowest_dorm_price, lowest_private_price, lowest_price, currency,
                    has_availability, free_cancellation, promotion_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    summary.property_id,
                    city_slug,
                    summary.stay_date.isoformat(),
                    summary.scraped_at.isoformat(),
                    total_units,
                    available_units,
                    sold_units,
                    occupancy_pct(total_units, sold_units),
                    summary.total_dorm_beds,
                    summary.total_private_rooms,
                    summary.total_private_beds,
                    summary.dorm_room_count,
                    summary.private_room_count,
                    summary.available_dorm_rooms,
                    summary.available_private_rooms,
                    summary.lowest_dorm_price,
                    summary.lowest_private_price,
                    summary.lowest_price,
                    summary.currency,
                    int(summary.has_availability),
                    int(summary.free_cancellation_available),
                    summary.promotion_count,
                ),
            )

    def save_property_summary(
        self,
        run_id: int,
        summary: PropertySummaryRecord,
        *,
        city: str | None = None,
        city_slug: str | None = None,
    ) -> None:
        slug = city_slug
        if not slug and city:
            with self.connect() as connection:
                slug = self._city_slug_for_name(connection, city) or city.lower()
        slug = slug or "unknown"
        self.save_stay_date_inventory(
            run_id,
            summary,
            city_slug=slug,
            total_units=0,
            available_units=0,
            sold_units=0,
        )

    def save_room_type_inventory(
        self,
        snapshot_id: int,
        records: list[RoomInventoryRecord],
        *,
        city_slug: str,
    ) -> None:
        if not records:
            return
        rows = []
        for record in records:
            total, available, sold = room_record_units(record)
            rows.append(
                (
                    snapshot_id,
                    record.property_id,
                    city_slug,
                    record.stay_date.isoformat(),
                    record.scraped_at.isoformat(),
                    record.room_id,
                    record.room_name,
                    record.room_category,
                    record.basic_type,
                    record.grade,
                    record.capacity,
                    int(record.ensuite),
                    record.beds_available,
                    record.rooms_available,
                    total,
                    available,
                    sold,
                    record.lowest_price,
                    record.currency,
                    int(record.has_availability),
                )
            )
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO room_type_inventory (
                    snapshot_id, hostel_id, city_slug, stay_date, scraped_at,
                    room_id, room_name, room_category, basic_type, grade,
                    capacity, is_ensuite, beds_available, rooms_available,
                    total_units, available_units, sold_units,
                    lowest_price, currency, has_availability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_room_records(
        self,
        run_id: int,
        records: list[RoomInventoryRecord],
        *,
        city: str | None = None,
        city_slug: str | None = None,
    ) -> None:
        slug = city_slug
        if not slug and city:
            with self.connect() as connection:
                slug = self._city_slug_for_name(connection, city) or city.lower()
        slug = slug or "unknown"
        self.save_room_type_inventory(run_id, records, city_slug=slug)

        if records:
            totals = sum(room_record_units(r)[0] for r in records)
            available = sum(room_record_units(r)[1] for r in records)
            sold = sum(room_record_units(r)[2] for r in records)
            first = records[0]
            with self.connect() as connection:
                connection.execute(
                    """
                    UPDATE stay_date_inventory
                    SET total_units = ?, available_units = ?, sold_units = ?, occupancy_pct = ?
                    WHERE hostel_id = ? AND stay_date = ? AND scraped_at = ?
                    """,
                    (
                        totals,
                        available,
                        sold,
                        occupancy_pct(totals, sold),
                        first.property_id,
                        first.stay_date.isoformat(),
                        first.scraped_at.isoformat(),
                    ),
                )

    def load_stay_date_inventory(self) -> pd.DataFrame:
        query = """
        SELECT
            sdi.id,
            sdi.snapshot_id,
            sdi.hostel_id,
            h.name AS hostel_name,
            c.name AS city,
            sdi.city_slug,
            sdi.stay_date,
            sdi.scraped_at,
            sdi.total_units,
            sdi.available_units,
            sdi.sold_units,
            sdi.occupancy_pct,
            sdi.total_dorm_beds,
            sdi.total_private_rooms,
            sdi.total_private_beds,
            sdi.dorm_room_count,
            sdi.private_room_count,
            sdi.available_dorm_rooms,
            sdi.available_private_rooms,
            sdi.lowest_dorm_price,
            sdi.lowest_private_price,
            sdi.lowest_price,
            sdi.currency,
            sdi.has_availability,
            sdi.free_cancellation,
            sdi.promotion_count,
            DATE(sdi.scraped_at) AS scrape_date
        FROM stay_date_inventory sdi
        LEFT JOIN hostels h ON h.hostel_id = sdi.hostel_id
        LEFT JOIN cities c ON c.slug = sdi.city_slug
        ORDER BY sdi.scraped_at DESC, sdi.stay_date ASC
        """
        with self.connect() as connection:
            return pd.read_sql_query(
                query,
                connection,
                parse_dates=["stay_date", "scraped_at", "scrape_date"],
            )

    def load_property_inventory(self) -> pd.DataFrame:
        df = self.load_stay_date_inventory()
        if df.empty:
            return df
        return df.rename(
            columns={
                "snapshot_id": "run_id",
                "hostel_id": "property_id",
                "hostel_name": "property_name",
                "total_units": "total_inventory",
                "available_units": "available_inventory",
                "sold_units": "sold_inventory",
                "occupancy_pct": "occupancy_rate",
                "free_cancellation": "free_cancellation_available",
            }
        )

    def load_room_type_inventory(self) -> pd.DataFrame:
        query = """
        SELECT
            rti.id,
            rti.snapshot_id,
            rti.hostel_id,
            h.name AS hostel_name,
            c.name AS city,
            rti.city_slug,
            rti.stay_date,
            rti.scraped_at,
            rti.room_id,
            rti.room_name,
            rti.room_category,
            rti.basic_type,
            rti.grade,
            rti.capacity,
            rti.is_ensuite AS ensuite,
            rti.beds_available,
            rti.rooms_available,
            rti.total_units,
            rti.available_units,
            rti.sold_units,
            rti.lowest_price,
            rti.currency,
            rti.has_availability,
            DATE(rti.scraped_at) AS scrape_date
        FROM room_type_inventory rti
        LEFT JOIN hostels h ON h.hostel_id = rti.hostel_id
        LEFT JOIN cities c ON c.slug = rti.city_slug
        ORDER BY rti.scraped_at DESC, rti.stay_date ASC
        """
        with self.connect() as connection:
            return pd.read_sql_query(
                query,
                connection,
                parse_dates=["stay_date", "scraped_at", "scrape_date"],
            )

    def load_room_inventory(self) -> pd.DataFrame:
        df = self.load_room_type_inventory()
        if df.empty:
            return df
        return df.rename(
            columns={
                "snapshot_id": "run_id",
                "hostel_id": "property_id",
                "hostel_name": "property_name",
            }
        )

    def load_scrape_snapshots(self) -> pd.DataFrame:
        with self.connect() as connection:
            return pd.read_sql_query(
                """
                SELECT
                    id,
                    started_at,
                    finished_at,
                    status,
                    hostels_count AS properties_count,
                    stay_dates_count,
                    requests_total,
                    requests_failed,
                    notes
                FROM scrape_snapshots
                ORDER BY started_at DESC
                """,
                connection,
                parse_dates=["started_at", "finished_at"],
            )

    def load_collection_runs(self) -> pd.DataFrame:
        return self.load_scrape_snapshots()

    def load_hostels(self) -> pd.DataFrame:
        query = """
        SELECT
            h.hostel_id,
            h.name AS hostel_name,
            h.city_slug,
            c.name AS city,
            h.country,
            h.updated_at
        FROM hostels h
        LEFT JOIN cities c ON c.slug = h.city_slug
        ORDER BY c.name, h.name
        """
        with self.connect() as connection:
            return pd.read_sql_query(query, connection)

    def latest_scrape_dates(self) -> list[date]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT DATE(scraped_at) AS scrape_date
                FROM stay_date_inventory
                ORDER BY scrape_date DESC
                """
            ).fetchall()
        return [date.fromisoformat(row["scrape_date"]) for row in rows]

    def prune_old_snapshots(self, retention_days: int) -> dict[str, int]:
        if retention_days <= 0:
            return {"stay_date_rows": 0, "room_rows": 0, "snapshot_rows": 0}

        cutoff = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as connection:
            stay_deleted = connection.execute(
                """
                DELETE FROM stay_date_inventory
                WHERE DATE(scraped_at) < DATE(?, '-' || ? || ' days')
                """,
                (cutoff, retention_days),
            ).rowcount
            room_deleted = connection.execute(
                """
                DELETE FROM room_type_inventory
                WHERE DATE(scraped_at) < DATE(?, '-' || ? || ' days')
                """,
                (cutoff, retention_days),
            ).rowcount
            snapshot_deleted = connection.execute(
                """
                DELETE FROM scrape_snapshots
                WHERE DATE(started_at) < DATE(?, '-' || ? || ' days')
                AND status != 'running'
                """,
                (cutoff, retention_days),
            ).rowcount

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("VACUUM")
            connection.commit()
        finally:
            connection.close()

        return {
            "stay_date_rows": stay_deleted or 0,
            "room_rows": room_deleted or 0,
            "snapshot_rows": snapshot_deleted or 0,
            "property_rows": stay_deleted or 0,
        }
