from __future__ import annotations

import logging
import random
import time
from datetime import date, timedelta
from typing import Any

import requests

from .config import AppConfig

logger = logging.getLogger(__name__)


class HostelworldClient:
    """Rate-limited client for the Hostelworld legacy API."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.api = config.api
        self.collector = config.collector
        self.session = requests.Session()
        self.session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en",
                "api-key": self.api.api_key,
                "origin": self.api.origin,
                "referer": self.api.referer,
                "user-agent": self.api.user_agent,
            }
        )

    def _sleep_between_calls(self) -> None:
        delay = random.uniform(
            self.collector.min_delay_seconds,
            self.collector.max_delay_seconds,
        )
        logger.debug("Sleeping %.2fs before next API call", delay)
        time.sleep(delay)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.api.base_url.rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, self.collector.max_retries + 1):
            try:
                response = self.session.request(method, url, timeout=45, **kwargs)
                if response.status_code == 429:
                    wait = self.collector.retry_backoff_seconds * attempt
                    logger.warning("Rate limited (429). Waiting %ss before retry.", wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("description"), list):
                    errors = payload["description"]
                    if errors and all(isinstance(item, dict) for item in errors):
                        message = "; ".join(
                            item.get("message", str(item)) for item in errors
                        )
                        raise RuntimeError(message)
                return payload
            except Exception as exc:  # noqa: BLE001 - retry wrapper
                last_error = exc
                wait = self.collector.retry_backoff_seconds * attempt
                logger.warning(
                    "Request failed (%s/%s) for %s: %s",
                    attempt,
                    self.collector.max_retries,
                    url,
                    exc,
                )
                if attempt < self.collector.max_retries:
                    time.sleep(wait)

        assert last_error is not None
        raise last_error

    def get_property(self, property_id: int | str) -> dict[str, Any]:
        self._sleep_between_calls()
        return self._request("GET", f"properties/{property_id}/", params={"application": self.api.application})

    def get_availability(self, property_id: int | str, stay_date: date) -> dict[str, Any]:
        self._sleep_between_calls()
        params = {
            "guests": self.api.guests,
            "num-nights": self.api.num_nights,
            "date-start": stay_date.isoformat(),
            "show-rate-restrictions": str(self.api.show_rate_restrictions).lower(),
            "application": self.api.application,
        }
        return self._request(
            "GET",
            f"properties/{property_id}/availability/",
            params=params,
        )

    def search_city_properties(
        self,
        city_id: int,
        stay_date: date,
        per_page: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        self._sleep_between_calls()
        params = {
            "city-id": city_id,
            "guests": self.api.guests,
            "num-nights": self.api.num_nights,
            "date-start": stay_date.isoformat(),
            "date-end": (stay_date + timedelta(days=self.api.num_nights)).isoformat(),
            "application": self.api.application,
            "per-page": per_page,
            "page": page,
        }
        return self._request("GET", "search/properties/", params=params)
