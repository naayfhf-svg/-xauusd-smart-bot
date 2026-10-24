"""Read-only client for Capital.com's fixed demo environment.

Provider documentation: https://open-api.capital.com/
The provider's API keys are trading-capable. This client deliberately exposes
only authentication, account reads, and the GOLD market read; it never submits
orders. Credentials and session tokens remain in process memory.
"""

from __future__ import annotations

import math
import os
from typing import Any, Mapping

import requests


DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com"
_ALLOWED_REQUESTS = frozenset({
    ("POST", "/api/v1/session"),
    ("GET", "/api/v1/accounts"),
    ("GET", "/api/v1/markets/GOLD"),
})


class CapitalDemoError(RuntimeError):
    """Sanitized connection or provider-response error."""


class CapitalDemoClient:
    """Fixed-host demo client with no order, transfer, or account-write API."""

    def __init__(
        self,
        api_key: str,
        identifier: str,
        api_password: str,
        *,
        session: Any = None,
        timeout: tuple[float, float] = (5.0, 15.0),
    ) -> None:
        credentials = (api_key, identifier, api_password)
        if not all(isinstance(value, str) and value.strip() for value in credentials):
            raise CapitalDemoError("Missing Capital.com demo credentials")
        if (not isinstance(timeout, tuple) or len(timeout) != 2
                or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                           and math.isfinite(value) and value > 0 for value in timeout)):
            raise CapitalDemoError("Invalid request timeout")
        self._api_key = api_key
        self._identifier = identifier
        self._api_password = api_password
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout
        self._cst: str | None = None
        self._security_token: str | None = None

    @classmethod
    def from_env(cls, *, session: Any = None) -> CapitalDemoClient:
        """Read dedicated runtime variables; never read CLI arguments or files."""
        return cls(
            os.getenv("CAPITAL_DEMO_API_KEY", ""),
            os.getenv("CAPITAL_DEMO_IDENTIFIER", ""),
            os.getenv("CAPITAL_DEMO_API_PASSWORD", ""),
            session=session,
        )

    def __repr__(self) -> str:
        return f"CapitalDemoClient(demo=True, authenticated={self.authenticated})"

    @property
    def base_url(self) -> str:
        return DEMO_BASE_URL

    @property
    def authenticated(self) -> bool:
        return bool(self._cst and self._security_token)

    def _request(
        self, method: str, path: str, *, headers: Mapping[str, str],
        body: dict[str, Any] | None = None,
    ) -> Any:
        if (method, path) not in _ALLOWED_REQUESTS:
            raise CapitalDemoError("Unsupported demo operation")
        kwargs: dict[str, Any] = {
            "headers": dict(headers), "timeout": self._timeout,
            "allow_redirects": False,
        }
        if body is not None:
            kwargs["json"] = body
        try:
            response = self._session.request(method, DEMO_BASE_URL + path, **kwargs)
        except requests.RequestException:
            raise CapitalDemoError("Capital.com demo connection failed") from None
        if not 200 <= response.status_code < 300:
            # Never include provider bodies, request objects, or credential values.
            if response.status_code in (401, 403):
                self._cst = self._security_token = None
            raise CapitalDemoError(f"Capital.com demo HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise CapitalDemoError("Invalid Capital.com demo JSON response") from None
        if not isinstance(payload, dict):
            raise CapitalDemoError("Invalid Capital.com demo response structure")
        return payload

    def authenticate(self) -> None:
        """Authenticate to demo with the API key's custom password."""
        self._cst = self._security_token = None
        response = self._request(
            "POST", "/api/v1/session",
            headers={"X-CAP-API-KEY": self._api_key, "Accept": "application/json",
                     "Content-Type": "application/json"},
            body={"identifier": self._identifier, "password": self._api_password,
                  "encryptedPassword": False},
        )
        cst = response.headers.get("CST")
        token = response.headers.get("X-SECURITY-TOKEN")
        if not all(isinstance(value, str) and value.strip() for value in (cst, token)):
            raise CapitalDemoError("Missing Capital.com demo session headers")
        self._cst, self._security_token = cst, token

    def _auth_headers(self) -> dict[str, str]:
        if not self.authenticated:
            raise CapitalDemoError("Authenticate to the demo environment first")
        return {"CST": self._cst, "X-SECURITY-TOKEN": self._security_token,
                "Accept": "application/json"}

    def accounts(self) -> list[dict[str, Any]]:
        """Read demo accounts. No changes to account preferences or balances."""
        response = self._request("GET", "/api/v1/accounts", headers=self._auth_headers())
        accounts = self._json(response).get("accounts")
        if not isinstance(accounts, list) or not all(isinstance(item, dict) for item in accounts):
            raise CapitalDemoError("Invalid Capital.com demo accounts response")
        return accounts

    def gold_quote(self) -> dict[str, Any]:
        """Read GOLD bid/offer and preserve source timestamps without freshness claims."""
        response = self._request("GET", "/api/v1/markets/GOLD", headers=self._auth_headers())
        payload = self._json(response)
        instrument, snapshot = payload.get("instrument"), payload.get("snapshot")
        if not isinstance(instrument, dict) or instrument.get("epic") != "GOLD" or not isinstance(snapshot, dict):
            raise CapitalDemoError("Invalid Capital.com demo GOLD market response")
        try:
            if isinstance(snapshot.get("bid"), bool) or isinstance(snapshot.get("offer"), bool):
                raise ValueError
            bid, offer = float(snapshot["bid"]), float(snapshot["offer"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise CapitalDemoError("Invalid Capital.com demo GOLD prices") from None
        if not (math.isfinite(bid) and math.isfinite(offer) and 0 < bid <= offer):
            raise CapitalDemoError("Invalid Capital.com demo GOLD prices")
        return {
            "epic": "GOLD", "source": "Capital.com demo", "demo": True,
            "bid": bid, "offer": offer, "market_status": snapshot.get("marketStatus"),
            "updateTime": snapshot.get("updateTime"),
            "updateTimeUTC": snapshot.get("updateTimeUTC"),
            "timestamp": snapshot.get("timestamp"),
        }

    def close(self) -> None:
        self._cst = self._security_token = None
        self._session.close()
