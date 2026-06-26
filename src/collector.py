from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .client import HostelworldClient
from .config import AppConfig, CityConfig, load_config
from .parser import parse_availability
from .storage import InventoryStore

logger = logging.getLogger(__name__)


def read_hostel_ids(path: Path, *, excluded_ids: frozenset[int] | None = None) -> list[int]:
    if not path.exists():
        raise FileNotFoundError(f"Hostel IDs file not found: {path}")

    excluded = excluded_ids or frozenset()
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or stripped.startswith("#"):
            continue
        property_id = int(stripped)
        if property_id in excluded:
            continue
        ids.append(property_id)
    if not ids:
        raise ValueError(f"No hostel IDs found in {path}")
    return ids


def stay_date_window(horizon_days: int, *, anchor: date | None = None) -> list[date]:
    start = anchor or date.today()
    return [start + timedelta(days=offset) for offset in range(1, horizon_days + 1)]


def resolve_property_name(
    client: HostelworldClient,
    store: InventoryStore,
    property_id: int,
    *,
    city: CityConfig,
) -> str:
    try:
        payload = client.get_property(property_id)
        name = str(payload.get("name") or payload.get("transName") or f"Property {property_id}")
        store.upsert_hostel(
            property_id,
            name,
            city_slug=city.slug,
            country=city.country,
        )
        return name
    except Exception as exc:  # noqa: BLE001 - fallback to cached name
        logger.warning("Could not fetch property metadata for %s: %s", property_id, exc)
        with store.connect() as connection:
            row = connection.execute(
                "SELECT name FROM hostels WHERE hostel_id = ?",
                (property_id,),
            ).fetchone()
        if row:
            return str(row["name"])
        return f"Property {property_id}"


def run_collection(
    config: AppConfig | None = None,
    *,
    property_ids: list[int] | None = None,
    city_slug: str | None = None,
    horizon_days: int | None = None,
    anchor_date: date | None = None,
) -> int:
    config = config or load_config()
    client = HostelworldClient(config)
    store = InventoryStore(config.database_path)
    store.seed_cities(
        tuple((city.slug, city.name, city.country, city.city_id) for city in config.cities)
    )

    if property_ids is not None:
        cities_to_run = [config.city_by_slug(city_slug)] if city_slug else list(config.cities)
        if len(cities_to_run) != 1:
            raise ValueError("--property-id requires --city when multiple cities are configured")
        city_jobs = [(cities_to_run[0], property_ids)]
    else:
        cities = [config.city_by_slug(city_slug)] if city_slug else list(config.cities)
        city_jobs = [
            (city, read_hostel_ids(city.hostel_ids_path(config.root_dir), excluded_ids=config.excluded_property_ids))
            for city in cities
        ]

    dates = stay_date_window(horizon_days or config.collector.horizon_days, anchor=anchor_date)
    total_properties = sum(len(ids) for _, ids in city_jobs)

    started_at = datetime.now(timezone.utc)
    run_id = store.start_run(started_at)
    requests_total = 0
    requests_failed = 0
    property_names: dict[int, str] = {}

    logger.info(
        "Starting collection run %s for %s properties across %s cities and %s stay dates",
        run_id,
        total_properties,
        len(city_jobs),
        len(dates),
    )

    try:
        for city, ids in city_jobs:
            logger.info("Collecting %s (%s) — %s properties", city.name, city.slug, len(ids))
            for property_id in ids:
                if property_id not in property_names:
                    property_names[property_id] = resolve_property_name(
                        client, store, property_id, city=city,
                    )

                property_name = property_names[property_id]
                for stay_date in dates:
                    requests_total += 1
                    scraped_at = datetime.now(timezone.utc)
                    try:
                        payload = client.get_availability(property_id, stay_date)
                        room_records, summary = parse_availability(
                            payload,
                            property_id=property_id,
                            property_name=property_name,
                            stay_date=stay_date,
                            scraped_at=scraped_at,
                        )
                        store.save_property_summary(run_id, summary, city_slug=city.slug)
                        store.save_room_records(run_id, room_records, city_slug=city.slug)
                        logger.info(
                            "Collected %s | %s | %s | beds=%s priv=%s avail=%s",
                            city.name,
                            property_name,
                            stay_date.isoformat(),
                            summary.total_dorm_beds,
                            summary.total_private_rooms,
                            summary.has_availability,
                        )
                    except Exception as exc:  # noqa: BLE001 - continue other dates
                        requests_failed += 1
                        logger.exception(
                            "Failed city=%s property=%s stay_date=%s: %s",
                            city.slug,
                            property_id,
                            stay_date.isoformat(),
                            exc,
                        )

        status = "completed" if requests_failed == 0 else "completed_with_errors"
        store.finish_run(
            run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            properties_count=total_properties,
            stay_dates_count=len(dates),
            requests_total=requests_total,
            requests_failed=requests_failed,
            notes=None if requests_failed == 0 else f"{requests_failed} requests failed",
        )
        retention = config.storage.snapshot_retention_days
        if retention > 0:
            pruned = store.prune_old_snapshots(retention)
            logger.info(
                "Pruned snapshots older than %s days: %s",
                retention,
                pruned,
            )
        logger.info(
            "Collection run %s finished: %s total, %s failed",
            run_id,
            requests_total,
            requests_failed,
        )
        return run_id
    except Exception:
        store.finish_run(
            run_id,
            finished_at=datetime.now(timezone.utc),
            status="failed",
            properties_count=total_properties,
            stay_dates_count=len(dates),
            requests_total=requests_total,
            requests_failed=requests_failed,
            notes="Run aborted due to unexpected error",
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Hostelworld availability for configured cities.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--city", type=str, default=None, help="Collect only this city slug (e.g. ooty, kodaikanal)")
    parser.add_argument("--hostel-ids", type=Path, default=None, help="Override hostel IDs file (requires --city)")
    parser.add_argument("--horizon-days", type=int, default=None, help="Days ahead to collect")
    parser.add_argument(
        "--property-id",
        action="append",
        type=int,
        dest="property_ids",
        help="Collect only specific property IDs (repeatable; use with --city)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Collect only N days ahead instead of full horizon (for testing)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    config = load_config(args.config)
    property_ids = args.property_ids
    if args.hostel_ids:
        if not args.city:
            parser.error("--hostel-ids requires --city")
        property_ids = read_hostel_ids(args.hostel_ids, excluded_ids=config.excluded_property_ids)
    horizon = args.days or args.horizon_days
    run_collection(
        config,
        property_ids=property_ids,
        city_slug=args.city,
        horizon_days=horizon,
    )


if __name__ == "__main__":
    main()
