from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

from .client import HostelworldClient
from .config import CityConfig, load_config

logger = logging.getLogger(__name__)


def discover_city_hostels(
    city: CityConfig,
    *,
    sample_date: date | None = None,
    per_page: int = 50,
) -> list[dict[str, int | str]]:
    config = load_config()
    client = HostelworldClient(config)
    stay_date = sample_date or (date.today() + timedelta(days=7))

    discovered: dict[int, dict[str, int | str]] = {}
    page = 1
    while True:
        payload = client.search_city_properties(city.city_id, stay_date, per_page=per_page, page=page)
        for item in payload.get("properties") or []:
            discovered[int(item["id"])] = {
                "property_id": int(item["id"]),
                "name": str(item.get("name") or ""),
            }

        pagination = payload.get("pagination") or {}
        next_page = pagination.get("next")
        if not next_page:
            break
        page += 1

    return sorted(discovered.values(), key=lambda row: str(row["name"]))


def write_hostel_ids(
    path: Path,
    hostels: list[dict[str, int | str]],
    *,
    merge: bool = True,
    excluded_ids: frozenset[int] | None = None,
) -> None:
    excluded = excluded_ids or frozenset()
    discovered_ids = {int(h["property_id"]) for h in hostels if int(h["property_id"]) not in excluded}
    hostels = [h for h in hostels if int(h["property_id"]) not in excluded]
    manual_extras: set[int] = set()
    if merge and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped or stripped.startswith("#"):
                continue
            property_id = int(stripped)
            if property_id in excluded:
                continue
            if property_id not in discovered_ids:
                manual_extras.add(property_id)

    lines = [
        f"# {path.stem.replace('_', ' ').title()} property IDs (one per line; lines starting with # are ignored)",
        "# Refresh with: python -m src.discover --city <slug>",
        "",
    ]
    for hostel in hostels:
        lines.append(f"{hostel['property_id']}  # {hostel['name']}")
    for property_id in sorted(manual_extras):
        lines.append(f"{property_id}  # manually retained")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover hostel IDs from Hostelworld search API.")
    parser.add_argument("--city", type=str, default=None, help="City slug (default: all configured cities)")
    parser.add_argument("--output", type=Path, default=None, help="Output hostel IDs file (single city only)")
    parser.add_argument("--no-merge", action="store_true", help="Replace file instead of merging IDs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()

    if args.output and not args.city:
        parser.error("--output requires --city")

    cities = [config.city_by_slug(args.city)] if args.city else list(config.cities)

    for city in cities:
        output = args.output or city.hostel_ids_path(config.root_dir)
        hostels = discover_city_hostels(city)
        write_hostel_ids(
            output,
            hostels,
            merge=not args.no_merge,
            excluded_ids=config.excluded_property_ids,
        )

        logger.info("Discovered %s hostels for %s (city_id=%s)", len(hostels), city.name, city.city_id)
        for hostel in hostels:
            logger.info("  %s - %s", hostel["property_id"], hostel["name"])
        logger.info("Wrote IDs to %s", output)


if __name__ == "__main__":
    main()
