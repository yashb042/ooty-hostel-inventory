from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def _parse_price(value: dict[str, Any] | None) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    raw = value.get("value")
    currency = value.get("currency")
    if raw is None:
        return None, currency
    try:
        return float(Decimal(str(raw))), currency
    except (InvalidOperation, ValueError):
        return None, currency


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class RoomInventoryRecord:
    property_id: int
    property_name: str
    stay_date: date
    scraped_at: datetime
    room_id: int
    room_name: str
    room_category: str
    basic_type: str
    grade: str
    capacity: int | None
    ensuite: bool
    beds_available: int | None
    rooms_available: int | None
    lowest_price: float | None
    currency: str | None
    has_availability: bool


@dataclass
class PropertySummaryRecord:
    property_id: int
    property_name: str
    stay_date: date
    scraped_at: datetime
    total_dorm_beds: int
    total_private_rooms: int
    total_private_beds: int
    dorm_room_count: int
    private_room_count: int
    available_dorm_rooms: int
    available_private_rooms: int
    lowest_dorm_price: float | None
    lowest_private_price: float | None
    lowest_price: float | None
    currency: str | None
    has_availability: bool
    free_cancellation_available: bool
    promotion_count: int


def parse_availability(
    payload: dict[str, Any],
    *,
    property_id: int,
    property_name: str,
    stay_date: date,
    scraped_at: datetime,
) -> tuple[list[RoomInventoryRecord], PropertySummaryRecord]:
    rooms = payload.get("rooms") or {}
    room_records: list[RoomInventoryRecord] = []

    total_dorm_beds = 0
    total_private_rooms = 0
    total_private_beds = 0
    dorm_room_count = 0
    private_room_count = 0
    available_dorm_rooms = 0
    available_private_rooms = 0
    lowest_dorm_price: float | None = None
    lowest_private_price: float | None = None
    currency: str | None = None

    for category in ("dorms", "privates"):
        for room in rooms.get(category) or []:
            beds = _safe_int(room.get("totalBedsAvailable")) or 0
            rooms_avail = _safe_int(room.get("totalRoomsAvailable")) or 0
            capacity = _safe_int(room.get("capacity"))
            price, room_currency = _parse_price(room.get("lowestPricePerNight"))
            currency = room_currency or currency
            has_availability = beds > 0 or rooms_avail > 0

            room_records.append(
                RoomInventoryRecord(
                    property_id=property_id,
                    property_name=property_name,
                    stay_date=stay_date,
                    scraped_at=scraped_at,
                    room_id=int(room["id"]),
                    room_name=str(room.get("name") or ""),
                    room_category="dorm" if category == "dorms" else "private",
                    basic_type=str(room.get("basicType") or ""),
                    grade=str(room.get("grade") or ""),
                    capacity=capacity,
                    ensuite=str(room.get("ensuite") or "0") in {"1", "true", "True"},
                    beds_available=_safe_int(room.get("totalBedsAvailable")),
                    rooms_available=_safe_int(room.get("totalRoomsAvailable")),
                    lowest_price=price,
                    currency=room_currency,
                    has_availability=has_availability,
                )
            )

            if category == "dorms":
                dorm_room_count += 1
                total_dorm_beds += beds
                if has_availability:
                    available_dorm_rooms += 1
                if price is not None and (lowest_dorm_price is None or price < lowest_dorm_price):
                    lowest_dorm_price = price
            else:
                private_room_count += 1
                total_private_rooms += rooms_avail if rooms_avail else (1 if beds > 0 else 0)
                total_private_beds += beds
                if has_availability:
                    available_private_rooms += 1
                if price is not None and (lowest_private_price is None or price < lowest_private_price):
                    lowest_private_price = price

    property_lowest, property_currency = _parse_price(payload.get("lowestPricePerNight"))
    currency = property_currency or currency

    summary = PropertySummaryRecord(
        property_id=property_id,
        property_name=property_name,
        stay_date=stay_date,
        scraped_at=scraped_at,
        total_dorm_beds=total_dorm_beds,
        total_private_rooms=total_private_rooms,
        total_private_beds=total_private_beds,
        dorm_room_count=dorm_room_count,
        private_room_count=private_room_count,
        available_dorm_rooms=available_dorm_rooms,
        available_private_rooms=available_private_rooms,
        lowest_dorm_price=lowest_dorm_price,
        lowest_private_price=lowest_private_price,
        lowest_price=property_lowest,
        currency=currency,
        has_availability=any(record.has_availability for record in room_records),
        free_cancellation_available=bool(payload.get("freeCancellationAvailable")),
        promotion_count=len(payload.get("promotions") or []),
    )
    return room_records, summary


def records_to_dicts(records: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        row = asdict(record)
        row["stay_date"] = record.stay_date.isoformat()
        row["scraped_at"] = record.scraped_at.isoformat()
        output.append(row)
    return output
