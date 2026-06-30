from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

import pandas as pd

from .collector import run_collection
from .config import load_config
from .storage import InventoryStore

logger = logging.getLogger(__name__)

STALE_RUN_HOURS = 3


def needs_daily_run(store: InventoryStore) -> tuple[bool, str]:
    """Decide if today's inventory snapshot still needs to be collected."""
    runs = store.load_collection_runs()
    if runs.empty:
        return True, "no prior runs"

    today = date.today()
    now = datetime.now(timezone.utc)

    in_progress = runs[runs["status"] == "running"]
    if not in_progress.empty:
        started = in_progress.iloc[0]["started_at"]
        if pd.notna(started):
            started_utc = _to_utc(started)
            age_hours = (now - started_utc).total_seconds() / 3600
            if age_hours < STALE_RUN_HOURS:
                return False, "collection already in progress"
            logger.warning("Stale running job detected (%.1fh old); allowing new run", age_hours)

    completed = runs[runs["status"].astype(str).str.startswith("completed", na=False)]
    for _, row in completed.iterrows():
        finished = row["finished_at"]
        if pd.isna(finished):
            continue
        if _to_local_date(finished) == today:
            return False, f"already completed today (run #{int(row['id'])})"

    return True, "today's collection not done yet"


def _to_utc(value: datetime) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize(timezone.utc).to_pydatetime()
    return ts.tz_convert(timezone.utc).to_pydatetime()


def _to_local_date(value: datetime) -> date:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.date()
    return ts.tz_convert(None).date()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run hostel inventory collection if not already done today.",
    )
    parser.add_argument("--force", action="store_true", help="Run even if today is already complete")
    parser.add_argument("--check-only", action="store_true", help="Print decision and exit")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    config = load_config()
    store = InventoryStore(config.database_path)
    store.seed_cities(
        tuple((city.slug, city.name, city.country, city.city_id) for city in config.cities)
    )

    if args.force:
        should_run, reason = True, "forced"
    else:
        should_run, reason = needs_daily_run(store)

    if args.check_only:
        print(f"should_run={should_run} reason={reason}")
        return 0 if not should_run else 1

    if not should_run:
        logger.info("Skipping collection: %s", reason)
        return 0

    logger.info("Starting collection: %s", reason)
    run_collection(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
