from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CityConfig:
    slug: str
    name: str
    country: str
    city_id: int
    hostel_ids_file: str

    def hostel_ids_path(self, root_dir: Path) -> Path:
        return root_dir / self.hostel_ids_file


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    api_key: str
    origin: str
    referer: str
    user_agent: str
    guests: int
    num_nights: int
    show_rate_restrictions: bool
    application: str


@dataclass(frozen=True)
class CollectorConfig:
    horizon_days: int
    min_delay_seconds: float
    max_delay_seconds: float
    max_retries: int
    retry_backoff_seconds: float


@dataclass(frozen=True)
class StorageConfig:
    database_path: str
    snapshot_retention_days: int = 90


@dataclass(frozen=True)
class AppConfig:
    cities: tuple[CityConfig, ...]
    excluded_property_ids: frozenset[int]
    api: ApiConfig
    collector: CollectorConfig
    storage: StorageConfig
    root_dir: Path

    @property
    def database_path(self) -> Path:
        return self.root_dir / self.storage.database_path

    def city_by_slug(self, slug: str) -> CityConfig:
        for city in self.cities:
            if city.slug == slug:
                return city
        raise KeyError(f"Unknown city slug: {slug}")


def _load_cities(root_dir: Path, cities_file: str) -> tuple[CityConfig, ...]:
    path = root_dir / cities_file
    with path.open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    return tuple(CityConfig(**entry) for entry in raw["cities"])


def _load_excluded_property_ids(root_dir: Path, cities_file: str) -> frozenset[int]:
    path = root_dir / cities_file
    with path.open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    return frozenset(int(pid) for pid in raw.get("excluded_property_ids") or [])


def load_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or ROOT_DIR / "config.yaml"
    with path.open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)

    root_dir = path.parent
    cities_file = raw.get("cities_file", "cities.yaml")
    if "city" in raw:
        # Legacy single-city config fallback
        city = CityConfig(
            slug="ooty",
            name=raw["city"]["name"],
            country=raw["city"]["country"],
            city_id=raw["city"]["city_id"],
            hostel_ids_file=raw.get("collector", {}).get("hostel_ids_file", "data/hostel_ids.txt"),
        )
        cities = (city,)
        excluded_property_ids = frozenset()
    else:
        cities = _load_cities(root_dir, cities_file)
        excluded_property_ids = _load_excluded_property_ids(root_dir, cities_file)

    collector_raw = dict(raw.get("collector", {}))
    collector_raw.pop("hostel_ids_file", None)

    return AppConfig(
        cities=cities,
        excluded_property_ids=excluded_property_ids,
        api=ApiConfig(**raw["api"]),
        collector=CollectorConfig(**collector_raw),
        storage=StorageConfig(**raw["storage"]),
        root_dir=root_dir,
    )
