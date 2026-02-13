"""Integration smoke test: verify MQTT transport connects to Settrade sandbox.

Usage:
    uv run python scripts/test_mqtt_connection.py

Requires .env file with SETTRADE_APP_ID, SETTRADE_BROKER_ID, SETTRADE_APP_CODE.

Exit codes:
    0 — Connected and received at least 1 message
    1 — Missing credentials or connection/subscription failed
"""

import logging
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger: logging.Logger = logging.getLogger(__name__)

_PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env() -> None:
    """Load .env file into os.environ (simple key=value parser)."""
    env_path: str = os.path.join(_PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip optional 'export ' prefix
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            # Strip surrounding quotes from value
            value = value.strip().strip("'\"")
            os.environ.setdefault(key.strip(), value)


def main() -> int:
    """Test MQTT connection to Settrade sandbox.

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    load_env()

    app_id: str = os.environ.get("SETTRADE_APP_ID", "")
    app_secret: str = os.environ.get("SETTRADE_APP_SECRET", "")
    broker_id: str = os.environ.get("SETTRADE_BROKER_ID", "")
    app_code: str = os.environ.get("SETTRADE_APP_CODE", "")
    base_url: str | None = os.environ.get("SETTRADE_BASE_URL") or None

    if not all([app_id, app_secret, broker_id, app_code]):
        logger.error(
            "Missing credentials. "
            "Copy .env.sample to .env and fill in your values."
        )
        return 1

    from infra.settrade_mqtt import MQTTClientConfig, SettradeMQTTClient

    config: MQTTClientConfig = MQTTClientConfig(
        app_id=app_id,
        app_secret=app_secret,
        app_code=app_code,
        broker_id=broker_id,
        base_url=base_url,
    )

    received_count: int = 0

    def on_message(topic: str, payload: bytes) -> None:
        nonlocal received_count
        received_count += 1
        logger.info(
            "Message #%d on %s (%d bytes)",
            received_count,
            topic,
            len(payload),
        )

    client: SettradeMQTTClient = SettradeMQTTClient(config=config)

    try:
        logger.info("Connecting to Settrade sandbox...")
        client.connect()

        # Wait for MQTT-level connection (on_connect rc=0)
        for _ in range(10):
            if client.connected:
                break
            time.sleep(1)

        if not client.connected:
            logger.error("Failed to connect within 10 seconds")
            logger.info("Stats: %s", client.stats())
            return 1

        logger.info("Connected! Subscribing to AOT bid/offer...")
        client.subscribe(
            topic="proto/topic/bidofferv3/AOT",
            callback=on_message,
        )

        # Listen for messages
        logger.info("Listening for messages (15 seconds)...")
        time.sleep(15)

        stats: dict = client.stats()
        logger.info("Final stats: %s", stats)

        passed: bool = received_count > 0
        logger.info(
            "Test %s — received %d messages",
            "PASSED" if passed else "FAILED",
            received_count,
        )
        return 0 if passed else 1

    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 1
    except Exception:
        logger.exception("Connection test failed")
        return 1
    finally:
        client.shutdown()


if __name__ == "__main__":
    sys.exit(main())
