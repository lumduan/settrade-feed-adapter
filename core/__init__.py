"""Core domain layer for Settrade Feed Adapter.

This package provides normalized event models and domain types
used throughout the feed adapter system. All event models are
Pydantic-based with frozen configuration for immutability.
"""

from core.events import BestBidAsk, BidAskFlag, FullBidOffer

__all__: list[str] = [
    "BestBidAsk",
    "BidAskFlag",
    "FullBidOffer",
]
