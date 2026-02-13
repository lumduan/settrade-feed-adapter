"""Example: Real-time BidOffer feed with inline latency measurement.

This script demonstrates the full pipeline:

    SettradeMQTTClient → BidOfferAdapter → Dispatcher → Strategy poll

With inline latency measurement on each received event and an
aggregated latency distribution summary (P50/P95/P99) at shutdown.

Prerequisites:
    1. Copy ``.env.sample`` to ``.env`` and fill in credentials:
       - ``SETTRADE_APP_ID``
       - ``SETTRADE_APP_SECRET``
       - ``SETTRADE_APP_CODE``
       - ``SETTRADE_BROKER_ID``
    2. Install dependencies: ``pip install -e .``
    3. Ensure market hours or use SANDBOX broker for testing.

Usage:
    python -m examples.example_bidoffer
    python -m examples.example_bidoffer --symbol PTT
    python -m examples.example_bidoffer --symbol AOT --log-every 100

Press Ctrl+C to stop.

Design notes:
    - Latency samples are capped at 1M entries (~28MB) to prevent
      OOM during extended runs.
    - The poll loop sleeps only when the queue is empty. Under
      continuous feed, the loop runs without sleep (strategy-driven
      design). CPU usage is bounded by the poll batch size.
    - recv_ts uses wall-clock (time.time_ns()), safe for same-process
      latency delta. Not suitable for cross-process comparison.
"""

import argparse
import logging
import os
import time

from core.dispatcher import Dispatcher, DispatcherConfig
from core.events import BestBidAsk
from infra.settrade_adapter import BidOfferAdapter, BidOfferAdapterConfig
from infra.settrade_mqtt import MQTTClientConfig, SettradeMQTTClient
from scripts.benchmark_utils import calculate_latency_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger: logging.Logger = logging.getLogger(__name__)

# Maximum latency samples to collect before stopping accumulation.
# 1M entries ≈ 28MB (int objects). Prevents OOM during extended runs.
_MAX_LATENCY_SAMPLES: int = 1_000_000


def main() -> None:
    """Run BidOffer feed example with latency measurement."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Real-time BidOffer feed with latency measurement",
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
        "--queue-maxlen",
        type=int,
        default=100_000,
        help="Dispatcher queue max length (default: 100000)",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=1,
        help=(
            "Log every Nth event (default: 1 = all). "
            "Use higher values for high-frequency feeds to avoid "
            "logging becoming a bottleneck."
        ),
    )
    args: argparse.Namespace = parser.parse_args()

    # Load credentials from environment variables
    app_id: str = os.environ.get("SETTRADE_APP_ID", "")
    app_secret: str = os.environ.get("SETTRADE_APP_SECRET", "")
    app_code: str = os.environ.get("SETTRADE_APP_CODE", "")
    broker_id: str = os.environ.get("SETTRADE_BROKER_ID", "")

    if not all([app_id, app_secret, app_code, broker_id]):
        logger.error(
            "Missing credentials. Set SETTRADE_APP_ID, SETTRADE_APP_SECRET, "
            "SETTRADE_APP_CODE, and SETTRADE_BROKER_ID environment variables. "
            "See .env.sample for reference."
        )
        return

    # Setup MQTT client
    mqtt_config: MQTTClientConfig = MQTTClientConfig(
        app_id=app_id,
        app_secret=app_secret,
        app_code=app_code,
        broker_id=broker_id,
    )
    mqtt_client: SettradeMQTTClient = SettradeMQTTClient(config=mqtt_config)

    # Setup dispatcher and adapter
    dispatcher: Dispatcher[BestBidAsk] = Dispatcher(
        config=DispatcherConfig(maxlen=args.queue_maxlen),
    )
    adapter: BidOfferAdapter = BidOfferAdapter(
        config=BidOfferAdapterConfig(),
        mqtt_client=mqtt_client,
        on_event=dispatcher.push,
    )

    # Connect and subscribe
    logger.info("Connecting to Settrade MQTT broker...")
    try:
        mqtt_client.connect()
    except Exception as exc:
        logger.exception("Failed to connect to MQTT broker: %s", exc)
        return

    adapter.subscribe(symbol=args.symbol)
    logger.info("Subscribed to %s — waiting for events...", args.symbol)

    # Strategy loop with latency measurement
    total_events: int = 0
    # Collect latency samples for distribution summary at shutdown.
    # Capped at _MAX_LATENCY_SAMPLES to prevent OOM during extended runs.
    # recv_ts is wall-clock (time.time_ns()) — safe for same-process delta.
    latency_samples_ns: list[int] = []
    samples_capped: bool = False

    try:
        while True:
            events: list[BestBidAsk] = dispatcher.poll(
                max_events=args.max_events,
            )

            for event in events:
                total_events += 1
                now_ns: int = time.time_ns()
                latency_ns: int = now_ns - event.recv_ts

                # Guard against negative latency (clock skew, NTP adjustment)
                if latency_ns >= 0 and not samples_capped:
                    if len(latency_samples_ns) < _MAX_LATENCY_SAMPLES:
                        latency_samples_ns.append(latency_ns)
                    else:
                        samples_capped = True
                        logger.info(
                            "Latency sample cap reached (%d), "
                            "stopping accumulation",
                            _MAX_LATENCY_SAMPLES,
                        )
                elif latency_ns < 0:
                    logger.warning(
                        "Negative latency detected: %dns (clock skew?)",
                        latency_ns,
                    )

                # Throttled logging to avoid bottleneck at high rates
                if total_events % args.log_every == 0:
                    latency_us: float = max(latency_ns, 0) / 1_000
                    logger.info(
                        "[%s] bid=%.2f ask=%.2f bid_vol=%d ask_vol=%d "
                        "latency=%.0fus (#%d)",
                        event.symbol,
                        event.bid,
                        event.ask,
                        event.bid_vol,
                        event.ask_vol,
                        latency_us,
                        total_events,
                    )

            if not events:
                time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        logger.info("Shutting down (received KeyboardInterrupt)...")
    finally:
        mqtt_client.shutdown()

        # Print final statistics
        mqtt_stats: dict = mqtt_client.stats()
        adapter_stats: dict = adapter.stats()
        dispatcher_stats = dispatcher.stats()

        logger.info("=" * 50)
        logger.info("Final Statistics")
        logger.info("-" * 50)
        logger.info("Total events processed: %d", total_events)
        logger.info(
            "MQTT messages received: %d",
            mqtt_stats["messages_received"],
        )
        logger.info(
            "Adapter parse errors: %d",
            adapter_stats["parse_errors"],
        )
        logger.info(
            "Dispatcher: pushed=%d, polled=%d, dropped=%d",
            dispatcher_stats.total_pushed,
            dispatcher_stats.total_polled,
            dispatcher_stats.total_dropped,
        )

        # Latency distribution summary (P50/P95/P99)
        if latency_samples_ns:
            stats = calculate_latency_stats(
                latencies_ns=latency_samples_ns,
            )
            logger.info("-" * 50)
            logger.info(
                "Latency Distribution (end-to-end, %d samples%s)",
                len(latency_samples_ns),
                " [capped]" if samples_capped else "",
            )
            logger.info(
                "  P50=%.0fus  P95=%.0fus  P99=%.0fus",
                stats.p50_us,
                stats.p95_us,
                stats.p99_us,
            )
            logger.info(
                "  min=%.0fus  max=%.0fus  mean=%.0fus",
                stats.min_us,
                stats.max_us,
                stats.mean_us,
            )

        logger.info("=" * 50)


if __name__ == "__main__":
    main()
