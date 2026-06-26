from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import load_config
from .storage import InventoryStore


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if pd.isna(value):
        return None
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def dataframe_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append({key: _serialize_value(val) for key, val in row.items()})
    return records


def export_web_data(output_dir: Path) -> dict[str, Any]:
    config = load_config()
    store = InventoryStore(config.database_path)
    store.seed_cities(
        tuple((city.slug, city.name, city.country, city.city_id) for city in config.cities)
    )

    stay_dates = store.load_stay_date_inventory()
    rooms = store.load_room_type_inventory()
    snapshots = store.load_scrape_snapshots()
    hostels = store.load_hostels()

    excluded = config.excluded_property_ids
    if not stay_dates.empty and excluded:
        stay_dates = stay_dates[~stay_dates["hostel_id"].isin(excluded)]
    if not rooms.empty and excluded:
        rooms = rooms[~rooms["hostel_id"].isin(excluded)]
    if not hostels.empty and excluded:
        hostels = hostels[~hostels["hostel_id"].isin(excluded)]

    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    latest_stays = (
        stay_dates.sort_values("scraped_at", ascending=False)
        .drop_duplicates(subset=["hostel_id", "stay_date"])
        if not stay_dates.empty
        else stay_dates
    )

    cities_meta = [
        {
            "slug": city.slug,
            "name": city.name,
            "country": city.country,
            "city_id": city.city_id,
            "hostel_count": int(
                hostels[hostels["city_slug"] == city.slug]["hostel_id"].nunique()
            ) if not hostels.empty else 0,
        }
        for city in config.cities
    ]

    inventory_summary: dict[str, Any] = {}
    if not latest_stays.empty:
        total_cap = int(latest_stays["total_units"].sum())
        total_avail = int(latest_stays["available_units"].sum())
        total_sold = int(latest_stays["sold_units"].sum())
        inventory_summary = {
            "total_capacity": total_cap,
            "available_inventory": total_avail,
            "sold_inventory": total_sold,
            "occupancy_rate": round(total_sold / total_cap, 4) if total_cap > 0 else None,
            "hostels_with_capacity": int((latest_stays["total_units"] > 0).sum()),
        }

    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_version": 2,
        "cities": cities_meta,
        "horizon_days": config.collector.horizon_days,
        "stay_date_records": len(stay_dates),
        "room_records": len(rooms),
        "excluded_hostel_ids": sorted(excluded),
        "inventory_summary": inventory_summary,
        "hostels": sorted(stay_dates["hostel_name"].dropna().unique().tolist()) if not stay_dates.empty else [],
        "city_names": sorted(
            {str(c) for c in stay_dates["city"].dropna().unique()}
        ) if not stay_dates.empty else [c.name for c in config.cities],
        "scrape_dates": sorted(
            {str(d)[:10] for d in stay_dates["scrape_date"].dropna().unique()}
        ) if not stay_dates.empty else [],
        "stay_dates": sorted(
            {str(d)[:10] for d in stay_dates["stay_date"].dropna().unique()}
        ) if not stay_dates.empty else [],
    }

    (data_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (data_dir / "stay_dates.json").write_text(
        json.dumps(dataframe_to_records(stay_dates), indent=2),
        encoding="utf-8",
    )
    (data_dir / "rooms.json").write_text(
        json.dumps(dataframe_to_records(rooms), indent=2),
        encoding="utf-8",
    )
    (data_dir / "snapshots.json").write_text(
        json.dumps(dataframe_to_records(snapshots), indent=2),
        encoding="utf-8",
    )
    (data_dir / "hostels.json").write_text(
        json.dumps(dataframe_to_records(hostels), indent=2),
        encoding="utf-8",
    )

    # Legacy filenames for external consumers
    legacy_stays = stay_dates.rename(
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
    (data_dir / "properties.json").write_text(
        json.dumps(dataframe_to_records(legacy_stays), indent=2),
        encoding="utf-8",
    )
    (data_dir / "runs.json").write_text(
        json.dumps(dataframe_to_records(snapshots), indent=2),
        encoding="utf-8",
    )

    web_template = Path(__file__).resolve().parent.parent / "web" / "index.html"
    if web_template.exists():
        (output_dir / "index.html").write_text(web_template.read_text(encoding="utf-8"), encoding="utf-8")

    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SQLite inventory data for the public web dashboard.")
    parser.add_argument("--output", type=Path, default=Path("site"), help="Output directory for GitHub Pages")
    args = parser.parse_args()
    meta = export_web_data(args.output)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
