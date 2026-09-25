"""HTTP client with a requests-per-second limit and retries with backoff."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
MAX_RETRIES = 6
MAX_BACKOFF = 30.0

log = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, headers: dict[str, str], max_per_second: float) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.min_interval = 1.0 / max_per_second
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def request(
        self, method: str, path: str, *, idempotent: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"

        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == MAX_RETRIES:
                    raise
                delay = self._backoff(attempt)
                log.warning("Network error on %s %s (%s); retrying in %.1fs", method, path, exc, delay)
                time.sleep(delay)
                continue

            retryable = response.status_code == 429 or (
                idempotent and response.status_code in RETRY_STATUSES
            )
            if retryable and attempt < MAX_RETRIES:
                delay = self._retry_after(response) or self._backoff(attempt)
                log.warning(
                    "HTTP %s on %s %s; retry %d/%d in %.1fs",
                    response.status_code, method, path, attempt + 1, MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue

            if not response.ok:
                # The body can echo the values sent, such as task titles, so it is
                # only logged at DEBUG level and kept out of the public error message.
                log.debug("Response body for %s %s: %s", method, path, response.text[:500])
                code = self._error_code(response)
                message = f"{method} {path} → HTTP {response.status_code}"
                raise requests.HTTPError(
                    f"{message} ({code})" if code else message, response=response
                )

            return response.json() if response.content else {}

        raise AssertionError("unreachable")

    @staticmethod
    def _error_code(response: requests.Response) -> str | None:
        # Notion returns `code` (e.g. "validation_error"); Todoist returns `error_tag`.
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return body.get("code") or body.get("error_tag")

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        try:
            return float(response.headers["Retry-After"])
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(MAX_BACKOFF, 2**attempt) + random.uniform(0, 1)
