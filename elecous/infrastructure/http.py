"""Polite HTTP client for legal collection (spec 2.22).

ELECOUS only uses official APIs, public datasets, open data portals, RSS
feeds and publicly accessible structured information. This client enforces:

* an honest User-Agent with contact information,
* a per-host politeness interval (never hammer a provider),
* bounded retries with exponential backoff,
* hard timeouts,
* immediate suspension signalling on 401/403/451 (access conditions changed;
  the connector suspends itself for re-evaluation rather than retrying) and
  respectful handling of 429 rate limits.

It never bypasses authentication, rate limits or technical protections.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from elecous.common.errors import ConnectorError, ConnectorSuspended
from elecous.infrastructure.config import Config, active_config

_host_lock = threading.Lock()
_last_request_at: dict[str, float] = {}


class HttpClient:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.config.get("http.user_agent", "ELECOUS/1.0")
        # Real long-running backfills (spaced by per-host politeness delays
        # of seconds to minutes) gain nothing from keep-alive, and a
        # connection idle that long risks the remote closing its end while
        # the local pool still considers it reusable — observed directly as
        # a process hung in CLOSE_WAIT, 0% CPU, no progress for 7+ minutes,
        # because a stale pooled socket's read never returns and never
        # cleanly raises the configured timeout. Forcing a fresh connection
        # per request eliminates this failure mode entirely.
        self.session.headers["Connection"] = "close"
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.timeout = float(self.config.get("http.timeout_seconds", 30))
        self.max_retries = int(self.config.get("http.max_retries", 3))
        self.backoff = float(self.config.get("http.backoff_seconds", 2.0))
        self.per_host_interval = float(self.config.get("http.per_host_interval", 1.0))

    def _politeness_wait(self, url: str) -> None:
        host = urlparse(url).netloc
        with _host_lock:
            last = _last_request_at.get(host, 0.0)
            wait = self.per_host_interval - (time.monotonic() - last)
            _last_request_at[host] = time.monotonic() + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._politeness_wait(url)
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(self.backoff * attempt)
                continue
            if response.status_code in (401, 403, 451):
                # Access conditions changed — suspend, never work around (2.22)
                raise ConnectorSuspended(
                    f"access refused ({response.status_code}) for {url}",
                    context={"status": response.status_code, "url": url},
                )
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", self.backoff * attempt * 2))
                time.sleep(min(retry_after, 120))
                last_error = ConnectorError(f"rate limited at {url}")
                continue
            if response.status_code >= 500:
                last_error = ConnectorError(f"server error {response.status_code} at {url}")
                time.sleep(self.backoff * attempt)
                continue
            if response.status_code >= 400:
                raise ConnectorError(
                    f"client error {response.status_code} at {url}",
                    context={"status": response.status_code, "body": response.text[:300]},
                )
            return response
        raise ConnectorError(f"request failed after {self.max_retries} attempts: {url}") from last_error

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        response = self.get(url, params=params, headers=headers)
        if response.status_code == 204 or not response.content:
            return None  # provider has no data for this request
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(f"non-JSON response from {url}", context={"body": response.text[:300]}) from exc
