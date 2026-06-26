from __future__ import annotations

from .config import load_config
from .storage import InventoryStore


def ensure_store(config_path=None) -> InventoryStore:
    """Create store, seed cities from config, and run legacy migration if needed."""
    config = load_config(config_path)
    store = InventoryStore(config.database_path)
    store.seed_cities(
        tuple(
            (city.slug, city.name, city.country, city.city_id)
            for city in config.cities
        )
    )
    return store
