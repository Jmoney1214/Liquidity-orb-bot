"""Broker adapters: a common interface plus a simulated paper broker."""

from .base import Broker
from .null import NullBroker
from .paper import PaperBroker

__all__ = ["Broker", "NullBroker", "PaperBroker"]
