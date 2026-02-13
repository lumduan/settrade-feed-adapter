"""Core domain layer for Settrade Feed Adapter.

This package provides normalized event models, domain types, and the
event dispatcher used throughout the feed adapter system. All event
models are Pydantic-based with frozen configuration for immutability.
"""

from core.dispatcher import Dispatcher, DispatcherConfig, DispatcherStats
from core.events import BestBidAsk, BidAskFlag, FullBidOffer

__all__: list[str] = [
    "BestBidAsk",
    "BidAskFlag",
    "Dispatcher",
    "DispatcherConfig",
    "DispatcherStats",
    "FullBidOffer",
]
