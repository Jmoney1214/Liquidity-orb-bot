"""Broker adapters: a common interface plus a simulated paper broker."""

from .base import Broker
from .paper import PaperBroker

__all__ = ["Broker", "PaperBroker"]
