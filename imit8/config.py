"""Configuration for imit8, loaded from env vars and ~/.imit8/config.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HOME_DIR = Path(os.environ.get("IMIT8_HOME", Path.home() / ".imit8"))
CONFIG_PATH = HOME_DIR / "config.json"
DB_PATH = HOME_DIR / "imit8.db"

DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class Config:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    max_steps: int = 40
    screenshot_width: int = 1280
    action_delay: float = 0.15
    replay_threshold: int = 2
    verify_done: bool = True
    reasoning_effort: str = "low"  # gemini's thinking tokens are the "wait" — keep them small
    fallback_model: str = ""  # swap to this once if the primary model errors mid-run
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        data: dict = {}
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
        dotenv = _dotenv()
        env_map = {
            "api_key": os.environ.get("OPENROUTER_API_KEY") or dotenv.get("OPENROUTER_API_KEY"),
            "model": os.environ.get("IMIT8_MODEL") or dotenv.get("IMIT8_MODEL"),
            "base_url": os.environ.get("IMIT8_BASE_URL") or dotenv.get("IMIT8_BASE_URL"),
        }
        data.update({k: v for k, v in env_map.items() if v})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        payload = self._read_file()
        payload.update(
            {
                "model": self.model,
                "base_url": self.base_url,
                "max_steps": self.max_steps,
                "screenshot_width": self.screenshot_width,
                "replay_threshold": self.replay_threshold,
                "verify_done": self.verify_done,
                "reasoning_effort": self.reasoning_effort,
                "fallback_model": self.fallback_model,
            }
        )
        CONFIG_PATH.write_text(json.dumps(payload, indent=2))

    def save_api_key(self) -> None:
        """Persist the key in config.json (owner-only) so UI-entered keys stick."""
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        payload = self._read_file()
        payload["api_key"] = self.api_key
        CONFIG_PATH.write_text(json.dumps(payload, indent=2))
        CONFIG_PATH.chmod(0o600)

    @staticmethod
    def _read_file() -> dict:
        return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def _dotenv() -> dict[str, str]:
    """KEY=value lines from a repo-root .env (editable installs) or ~/.imit8/.env.

    Real environment variables still win — this is just a fallback so a cloned
    checkout or the installed app can keep secrets out of shell profiles.
    """
    values: dict[str, str] = {}
    candidates = [Path(__file__).resolve().parent.parent / ".env", HOME_DIR / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    return values
