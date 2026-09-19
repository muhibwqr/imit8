"""jev — a local, open-source computer-use agent with flow memory."""

from .agent import Agent, Event, Result
from .computer import Computer
from .config import Config
from .flows import Flow, FlowStore

__version__ = "0.1.0"
__all__ = ["Agent", "Event", "Result", "Computer", "Config", "Flow", "FlowStore"]
