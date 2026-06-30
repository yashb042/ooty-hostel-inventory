from __future__ import annotations

from typing import Any


def room_units(
    *,
    capacity: int | None,
    beds_available: int | None,
    rooms_available: int | None,
) -> tuple[int, int, int]:
    """Return (total_units, available_units, sold_units) for one room row."""
    available = 0
    if beds_available is not None:
        available = int(beds_available)
    elif rooms_available is not None:
        available = int(rooms_available)

    if capacity is not None and int(capacity) > 0:
        total = int(capacity)
    else:
        total = available

    sold = max(0, total - available)
    return total, available, sold


def occupancy_pct(total: int, sold: int) -> float | None:
    if total <= 0:
        return None
    return round(sold / total, 4)


def room_record_units(record: Any) -> tuple[int, int, int]:
    return room_units(
        capacity=getattr(record, "capacity", None),
        beds_available=getattr(record, "beds_available", None),
        rooms_available=getattr(record, "rooms_available", None),
    )
