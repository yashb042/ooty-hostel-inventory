# Schema v2 migration

## New schema (normalized)

```
cities (slug PK)
  └── hostels (hostel_id PK, city_slug FK)
        ├── stay_date_inventory  — fact: hostel × stay_date × scrape snapshot
        └── room_type_inventory  — granular: room type per stay_date × snapshot

scrape_snapshots — collection run metadata (was collection_runs)
```

### Tables

| Table | Purpose |
|-------|---------|
| `cities` | Ooty, Kodaikanal reference (slug, name, country, hostelworld_city_id) |
| `hostels` | Hostelworld property dimension (was `properties`) |
| `scrape_snapshots` | Daily collection runs (was `collection_runs`) |
| `stay_date_inventory` | Per-hostel per-stay-date facts with **sold / available / total / occupancy_pct** |
| `room_type_inventory` | Room-level breakdown with same inventory metrics |

### Key improvements

- **First-class inventory metrics**: `total_units`, `available_units`, `sold_units`, `occupancy_pct` stored at both stay-date and room-type levels
- **Normalized names**: `hostel_id` instead of duplicated `property_name` on every row; joined at query time
- **City everywhere**: `city_slug` on all fact tables, joined to `cities.name` for display
- **Proper indexes** on stay_date, scraped_at, hostel_id, city_slug, snapshot_id

## Migration

Automatic on first `InventoryStore` init when legacy tables exist and new tables are empty.

Manual:

```bash
cd hostel-parsing
python scripts/migrate_inventory_schema.py          # migrate only
python scripts/migrate_inventory_schema.py --drop-legacy  # migrate + drop old tables
```

Legacy → new mapping:

| Legacy | New |
|--------|-----|
| `collection_runs` | `scrape_snapshots` |
| `properties` | `hostels` |
| `property_inventory` | `stay_date_inventory` |
| `room_inventory` | `room_type_inventory` |

Sold/available/total on stay-date rows is computed from room rows during migration when room data exists.

## Web export (v2)

New JSON files under `site/data/`:

- `stay_dates.json` — stay-date inventory (primary)
- `rooms.json` — room-type inventory
- `snapshots.json` — collection runs
- `hostels.json` — hostel reference
- `meta.json` — includes `schema_version: 2`

Legacy `properties.json` and `runs.json` are still exported for compatibility.

## Excluded hostels

Unchanged in `cities.yaml`: Pugal Holidays, RJ Inn, Sipas HideOut, Aakash Rooms.
