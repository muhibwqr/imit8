"""Thin OpenRouter client (chat completions with vision + tool calls)."""

from __future__ import annotations

import json
from typing import Any

import requests

from .config import Config


class LLMError(RuntimeError):
    pass


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": (
                "Press element N from the 'Interactive elements' list that "
                "accompanies the screenshot. Always prefer this over click when "
                "the list is present — it acts directly on the element without "
                "moving the pointer."
            ),
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "integer"}},
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click at a point in the screenshot's coordinate space.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type literal text with the keyboard.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch",
            "description": (
                "Open or bring focus to a target: an app name ('Calculator'), a "
                "URL ('https://…'), or a file path. Always prefer this over a "
                "launcher UI like Spotlight."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key",
            "description": "Press a key or chord, e.g. 'enter', 'cmd+space', 'ctrl+shift+t'.",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll at a point. Positive amount scrolls up, negative down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "amount": {"type": "integer"},
                },
                "required": ["x", "y", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag",
            "description": "Drag from one point to another with the left button held.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "number"},
                    "y1": {"type": "number"},
                    "x2": {"type": "number"},
                    "y2": {"type": "number"},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for the screen to settle before looking again.",
            "parameters": {
                "type": "object",
                "properties": {"seconds": {"type": "number", "maximum": 10}},
                "required": ["seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch",
            "description": (
                "Fire a planned sequence of actions back-to-back in a single step. "
                "Use it for fast-moving screens (games, live UIs) where acting once "
                "per screenshot is too slow, or to chain obvious steps without "
                "re-checking. Each action is an object like {\"name\": \"key\", "
                "\"keys\": \"space\"} or {\"name\": \"wait\", \"seconds\": 0.4} — "
                "any of click, type_text, key, scroll, drag, wait, launch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["actions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "The task is complete (or impossible). Always end with this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "summary": {"type": "string"},
                },
                "required": ["success", "summary"],
            },
        },
    },
]


class OpenRouterClient:
    def __init__(self, config: Config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def complete(
        self,
        messages: list[dict[str, Any]],
        timeout: float = 120.0,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.config.api_key:
            raise LLMError("No OpenRouter API key. Set OPENROUTER_API_KEY or run `imit8 config`.")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/imit8-agent/imit8",
            "X-Title": "imit8",
            **self.config.extra_headers,
        }
        body = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools if tools is not None else TOOLS,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "temperature": 0,
        }
        if self.config.reasoning_effort:
            body["reasoning"] = {"effort": self.config.reasoning_effort}
        resp = self.session.post(
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            data=json.dumps(body),
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise LLMError(f"OpenRouter {resp.status_code}: {resp.text[:400]}")
        payload = resp.json()
        if "choices" not in payload:
            raise LLMError(f"Unexpected OpenRouter response: {payload}")
        return payload["choices"][0]["message"]
