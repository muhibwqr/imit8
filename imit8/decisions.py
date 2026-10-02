"""Client for OpenRouter's alpha decisions endpoint — typed answers in ~100ms.

jev-style models answer narrow typed questions (noul/choice/score) about a
plain-text state. There are no images and no freeform generation, so a decision
costs a fraction of a vision round-trip — this is what the fast loop runs on.
"""

from __future__ import annotations

import requests

from .config import Config

DEFAULT_MODEL = "~typesafe/jev-latest"


class DecisionsError(RuntimeError):
    pass


class DecisionsClient:
    def __init__(self, config: Config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def decide(self, state: str, questions: dict, timeout: float = 10.0) -> dict:
        """POST /api/alpha/decisions -> {"answers": {name: typed answer}}."""
        if not self.config.api_key:
            raise DecisionsError("No OpenRouter API key. Set OPENROUTER_API_KEY.")
        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        resp = self.session.post(
            f"{base}/alpha/decisions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": DEFAULT_MODEL, "state": state, "questions": questions},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise DecisionsError(f"Decisions {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if "answers" not in payload:
            raise DecisionsError(f"Unexpected decisions response: {payload}")
        return payload["answers"]
