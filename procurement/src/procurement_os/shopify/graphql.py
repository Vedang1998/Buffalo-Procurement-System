from __future__ import annotations

import random
import time
from typing import Any, Callable

import httpx

from .auth import ClientCredentialsTokenProvider, ShopifyConfig


class ShopifyGraphQLError(RuntimeError):
    pass


class ShopifyGraphQLClient:
    """Small, auditable GraphQL client with retry/backoff.

    It deliberately avoids a Shopify-specific SDK so the adapter remains portable.
    """

    def __init__(
        self,
        config: ShopifyConfig,
        token_provider: ClientCredentialsTokenProvider,
        *,
        transport: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
    ) -> None:
        self.config = config
        self.token_provider = token_provider
        self.http = transport or httpx.Client(timeout=60.0)
        self.sleep = sleep
        self.max_attempts = max_attempts

    def query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            token = self.token_provider.get_token()
            try:
                response = self.http.post(
                    self.config.graphql_url,
                    headers={
                        "Content-Type": "application/json",
                        "X-Shopify-Access-Token": token,
                    },
                    json={"query": query, "variables": variables or {}},
                )
                if response.status_code == 401 and attempt < self.max_attempts:
                    self.token_provider.invalidate()
                    continue
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt < self.max_attempts:
                        self._backoff(attempt, response.headers.get("Retry-After"))
                        continue
                response.raise_for_status()
                payload = response.json()
                errors = payload.get("errors") or []
                if errors:
                    throttled = any(
                        (e.get("extensions") or {}).get("code") == "THROTTLED"
                        or "thrott" in str(e.get("message", "")).lower()
                        for e in errors
                    )
                    if throttled and attempt < self.max_attempts:
                        self._backoff(attempt, None)
                        continue
                    raise ShopifyGraphQLError(str(errors))
                data = payload.get("data")
                if data is None:
                    raise ShopifyGraphQLError(f"GraphQL response omitted data: {payload}")
                return data
            except (httpx.HTTPError, ShopifyGraphQLError) as exc:
                last_error = exc
                if attempt >= self.max_attempts or isinstance(exc, ShopifyGraphQLError):
                    raise
                self._backoff(attempt, None)
        raise RuntimeError("Shopify query failed") from last_error

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 0.0
        else:
            delay = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.random() * 0.2
        self.sleep(delay)
