"""Example: Feed health monitoring with production guard rails.

This script demonstrates the full pipeline with feed integrity:

    SettradeMQTTClient → BidOfferAdapter → Dispatcher → Strategy poll
                                                         ↓
                                              FeedHealthMonitor
                                              Dispatcher.health()

Three production guard rail patterns:

    1. **Feed-dead detection** — ``is_feed_dead()`` → pause trading
    2. **Drop-rate guard** — ``health().drop_rate_ema`` > threshold
       → reduce position size
    3. **Reconnect detection** — ``connection_epoch`` change
       → reinitialize strategy state

Plus auction period awareness via ``is_auction()``.

Prerequisites:
    1. Copy ``.env.sample`` to ``.env`` and fill in credentials:
       - ``SETTRADE_APP_ID``
       - ``SETTRADE_APP_SECRET``
       - ``SETTRADE_APP_CODE``
       - ``SETTRADE_BROKER_ID``
    2. Install dependencies: ``pip install -e .``
    3. Ensure market hours or use SANDBOX broker for testing.

Usage:
    python -m examples.example_feed_health
    python -m examples.example_feed_health --symbol PTT
    python -m examples.example_feed_health --symbol AOT --max-gap 10.0

Press Ctrl+C to stop.

Design notes:
    - FeedHealthMonitor uses monotonic time only (NTP-immune).
    - Guard rails are **signal exposure, not enforcement** — the
      strategy decides what action to take based on health signals.
    - All time-based checks in the poll loop share a single
      ``perf_counter_ns()`` call for consistency and reduced syscalls.
"""

import argparse
import logging
import os
import time

from dotenv import load_dotenv

from core.dispatcher import Dispatcher, DispatcherConfig, DispatcherHealth
from core.events import BestBidAsk
from core.feed_health import FeedHealthConfig, FeedHealthMonitor
from infra.settrade_adapter import BidOfferAdapter, BidOfferAdapterConfig
from infra.settrade_mqtt import MQTTClientConfig, SettradeMQTTClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger: logging.Logger = logging.getLogger(__name__)

# Minimum interval between event logs (nanoseconds).
# Prevents log spam under high-frequency feeds.
_LOG_INTERVAL_NS: int = 1_000_000_000  # 1 second


def main() -> None:
    """Run feed health monitoring example with guard rails."""
    load_dotenv()

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Feed health monitoring with production guard rails",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="AOT",
        help="Stock symbol to subscribe to (default: AOT)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.05,
        help="Poll interval in seconds (default: 0.05)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=100,
        help="Max events per poll batch (default: 100)",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=5.0,
        help="Max gap seconds before feed is dead (default: 5.0)",
    )
    parser.add_argument(
        "--drop-threshold",
        type=float,
        default=0.01,
        help="Drop rate EMA threshold for warning (default: 0.01 = 1%%)",
    )
    args: argparse.Namespace = parser.parse_args()

    # Load credentials
    app_id: str = os.environ.get("SETTRADE_APP_ID", "")
    app_secret: str = os.environ.get("SETTRADE_APP_SECRET", "")
    app_code: str = os.environ.get("SETTRADE_APP_CODE", "")
    broker_id: str = os.environ.get("SETTRADE_BROKER_ID", "")

    if not all([app_id, app_secret, app_code, broker_id]):
        logger.error(
            "Missing credentials. Set SETTRADE_APP_ID, SETTRADE_APP_SECRET, "
            "SETTRADE_APP_CODE, and SETTRADE_BROKER_ID environment variables."
        )
        return

    # Setup components
    mqtt_client: SettradeMQTTClient = SettradeMQTTClient(
        config=MQTTClientConfig(
            app_id=app_id,
            app_secret=app_secret,
            app_code=app_code,
            broker_id=broker_id,
        ),
    )
    dispatcher: Dispatcher[BestBidAsk] = Dispatcher(
        config=DispatcherConfig(
            maxlen=100_000,
            drop_warning_threshold=args.drop_threshold,
        ),
    )
    adapter: BidOfferAdapter = BidOfferAdapter(
        config=BidOfferAdapterConfig(),
        mqtt_client=mqtt_client,
        on_event=dispatcher.push,
    )
    monitor: FeedHealthMonitor = FeedHealthMonitor(
        config=FeedHealthConfig(max_gap_seconds=args.max_gap),
    )

    # Connect and subscribe
    logger.info("Connecting to Settrade MQTT broker...")
    try:
        mqtt_client.connect()
    except Exception as exc:
        logger.exception("Failed to connect: %s", exc)
        return

    adapter.subscribe(symbol=args.symbol)
    logger.info(
        "Subscribed to %s — monitoring feed health...",
        args.symbol,
    )

    # Strategy state
    total_events: int = 0
    # None until first event — avoids false reconnect on startup
    last_epoch: int | None = None
    feed_was_dead: bool = False
    last_log_ns: int = 0

    try:
        while True:
            events: list[BestBidAsk] = dispatcher.poll(
                max_events=args.max_events,
            )

            # Capture monotonic time once per poll loop
            now_ns: int = time.perf_counter_ns()

            for event in events:
                total_events += 1

                # Update feed health monitor
                monitor.on_event(event.symbol, now_ns=now_ns)

                # ── Guard Rail 1: Reconnect detection ──────────
                if last_epoch is None:
                    last_epoch = event.connection_epoch
                elif event.connection_epoch != last_epoch:
                    logger.warning(
                        "Connection epoch changed: %d → %d. "
                        "Reinitializing strategy state.",
                        last_epoch,
                        event.connection_epoch,
                    )
                    last_epoch = event.connection_epoch
                    # Strategy would reinitialize here:
                    # - Clear cached order book
                    # - Cancel pending orders
                    # - Rebuild position from broker API

                # ── Auction period awareness ───────────────────
                if event.is_auction():
                    # During ATO/ATC, bid/ask reflect auction prices
                    # Strategy might skip limit orders during auction
                    pass

                # Time-based logging (at most once per second)
                if (now_ns - last_log_ns) > _LOG_INTERVAL_NS:
                    gap_ms: float | None = monitor.last_seen_gap_ms(
                        event.symbol,
                        now_ns=now_ns,
                    )
                    logger.info(
                        "[%s] bid=%.2f ask=%.2f epoch=%d "
                        "gap=%.1fms (#%d)",
                        event.symbol,
                        event.bid,
                        event.ask,
                        event.connection_epoch,
                        gap_ms or 0.0,
                        total_events,
                    )
                    last_log_ns = now_ns

            # ── Guard Rail 2: Feed-dead detection ──────────────
            if monitor.has_ever_received() and monitor.is_feed_dead(
                now_ns=now_ns,
            ):
                if not feed_was_dead:
                    logger.error(
                        "FEED DEAD — no events for %.1fs. "
                        "Strategy should pause trading.",
                        args.max_gap,
                    )
                    feed_was_dead = True
                    # Strategy would pause here:
                    # - Cancel all pending orders
                    # - Flatten positions (optional)
                    # - Set flag to prevent new orders
            elif feed_was_dead and events:
                # Recovery requires new events — not just clock drift
                logger.info("Feed recovered — resuming normal operation.")
                feed_was_dead = False

            # ── Guard Rail 3: Drop-rate detection ──────────────
            health: DispatcherHealth = dispatcher.health()
            if health.drop_rate_ema > args.drop_threshold:
                logger.warning(
                    "High drop rate: EMA=%.4f (threshold=%.4f). "
                    "Queue utilization=%.1f%%. "
                    "Strategy should reduce position size.",
                    health.drop_rate_ema,
                    args.drop_threshold,
                    health.queue_utilization * 100,
                )

            # ── Stale symbol detection ─────────────────────────
            stale: list[str] = monitor.stale_symbols(now_ns=now_ns)
            if stale:
                logger.warning(
                    "Stale symbols: %s — data may be outdated.",
                    ", ".join(stale),
                )

            if not events:
                time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        mqtt_client.shutdown()

        # Final statistics
        health = dispatcher.health()
        logger.info("=" * 60)
        logger.info("Final Statistics")
        logger.info("-" * 60)
        logger.info("Total events processed: %d", total_events)
        logger.info("Final connection epoch: %d", last_epoch or 0)
        logger.info(
            "Dispatcher: pushed=%d, dropped=%d, drop_ema=%.6f",
            health.total_pushed,
            health.total_dropped,
            health.drop_rate_ema,
        )
        logger.info(
            "Feed monitor: ever_received=%s, symbols_tracked=%d",
            monitor.has_ever_received(),
            monitor.tracked_symbol_count(),
        )
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
