"""Configuration for jev, loaded from env vars and ~/.jev/config.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HOME_DIR = Path(os.environ.get("JEV_HOME", Path.home() / ".jev"))
CONFIG_PATH = HOME_DIR / "config.json"
DB_PATH = HOME_DIR / "jev.db"

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
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        data: dict = {}
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
        env_map = {
            "api_key": os.environ.get("OPENROUTER_API_KEY"),
            "model": os.environ.get("JEV_MODEL"),
            "base_url": os.environ.get("JEV_BASE_URL"),
        }
        data.update({k: v for k, v in env_map.items() if v})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "screenshot_width": self.screenshot_width,
            "replay_threshold": self.replay_threshold,
            "verify_done": self.verify_done,
        }
        CONFIG_PATH.write_text(json.dumps(payload, indent=2))
