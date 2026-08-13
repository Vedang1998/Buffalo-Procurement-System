from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Callable, Any

import httpx


@dataclass(frozen=True)
class ShopifyConfig:
    shop: str
    client_id: str
    client_secret: str
    api_version: str = "2026-07"

    @classmethod
    def from_env(cls) -> "ShopifyConfig":
        missing = [
            name for name in ("SHOPIFY_SHOP", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET")
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(f"Missing Shopify environment variables: {', '.join(missing)}")
        shop = os.environ["SHOPIFY_SHOP"].strip()
        if shop.endswith(".myshopify.com"):
            shop = shop[:-len(".myshopify.com")]
        return cls(
            shop=shop,
            client_id=os.environ["SHOPIFY_CLIENT_ID"],
            client_secret=os.environ["SHOPIFY_CLIENT_SECRET"],
            api_version=os.getenv("SHOPIFY_API_VERSION", "2026-07"),
        )

    @property
    def shop_domain(self) -> str:
        return f"{self.shop}.myshopify.com"

    @property
    def graphql_url(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    @property
    def token_url(self) -> str:
        return f"https://{self.shop_domain}/admin/oauth/access_token"


@dataclass(frozen=True)
class AccessToken:
    token: str
    scope: str
    expires_at_monotonic: float


class ClientCredentialsTokenProvider:
    """Shopify Dev Dashboard client-credentials token cache.

    Shopify client-credentials access tokens expire in roughly 24 hours. The provider
    refreshes ahead of expiry and never exposes the client secret outside server-side code.
    """

    def __init__(
        self,
        config: ShopifyConfig,
        *,
        refresh_margin_seconds: int = 300,
        post_form: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.refresh_margin_seconds = refresh_margin_seconds
        self._post_form = post_form or httpx.post
        self._clock = clock
        self._cached: AccessToken | None = None

    def invalidate(self) -> None:
        self._cached = None

    def get_token(self) -> str:
        now = self._clock()
        if self._cached and now < self._cached.expires_at_monotonic - self.refresh_margin_seconds:
            return self._cached.token

        response = self._post_form(
            self.config.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Shopify token response omitted access_token")
        expires_in = int(payload.get("expires_in", 86399))
        self._cached = AccessToken(
            token=token,
            scope=str(payload.get("scope", "")),
            expires_at_monotonic=now + expires_in,
        )
        return token
