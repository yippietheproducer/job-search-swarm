"""Minimal OpenAI-compatible chat client — pure stdlib, no dependencies.

Works against any OpenAI-compatible `/v1/chat/completions` endpoint: a local
model proxy, a free gateway, or a paid API. Configure via environment:

    PRACUJ_GATEWAY   base URL (with or without the /v1 suffix)
    PRACUJ_MODEL     model name
    PRACUJ_API_KEY   optional bearer key

No third-party deps, so this package installs cleanly anywhere Python >= 3.11.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = os.environ.get("PRACUJ_GATEWAY", "https://opencode.ai/zen/v1")
DEFAULT_MODEL = os.environ.get("PRACUJ_MODEL", "hy3-free")
DEFAULT_API_KEY = os.environ.get("PRACUJ_API_KEY", "")


class FleetError(RuntimeError):
    """Raised when the gateway is unreachable or returns an error payload."""


class FleetClient:
    """Blocking chat client for the fleet's /v1/chat/completions endpoint."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        api_key: str = DEFAULT_API_KEY,
        timeout: int = 280,
        max_retries: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Zen's base already ends in /v1; strip it so we don't double up
        # when appending /v1/chat/completions below.
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2500,
    ) -> str:
        """Return the raw assistant text for a single-turn system+user prompt."""
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/v1/chat/completions"

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=body, method="POST")
            # zen (and some gateways) 403 requests from the default Python-urllib
            # User-Agent; send a neutral one so keyless calls go through.
            req.add_header("User-Agent", "pracuj-ai/1.0")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise FleetError(
                        f"response truncated at max_tokens={max_tokens}; "
                        "raise max_tokens or shorten the inputs"
                    )
                return choice["message"]["content"]
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:  # noqa: PERF203
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
        raise FleetError(f"fleet call failed after retries: {last_err}")
