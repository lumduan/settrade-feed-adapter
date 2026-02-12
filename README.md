# settrade-feed-adapter
A lightweight Python adapter for subscribing to real-time market data from **Settrade Open API** via MQTT, bypassing the official Python SDK.   Designed for algorithmic trading systems and market data pipelines where **low latency and minimal overhead** are critical.
แน่นอนครับ — ด้านล่างนี้คือ ตัวอย่าง README.md ภาษาอังกฤษ สำหรับ repo ชื่อ settrade-feed-adapter ที่เหมาะสำหรับการสร้าง Adapter Layer ที่ต่อกับ Settrade Open API แบบ low-latency (bypassing SDK) พร้อมอ้างอิงจากเอกสาร API ของ Settrade 📡📈 

⸻


# settrade-feed-adapter

A lightweight Python adapter for subscribing to real-time market data from **Settrade Open API** via MQTT, bypassing the official Python SDK.  
Designed for algorithmic trading systems and market data pipelines where **low latency and minimal overhead** are critical.

> This adapter connects to Settrade’s MQTT feed directly, parses protobuf messages using the official protobuf schemas, and emits normalized events to your own dispatcher or strategy engine.

Official Settrade API Docs: https://developer.settrade.com/open-api/api-reference/reference/sdkv2/python/market-mqtt-realtime-data/1_gettingStart  [oai_citation:1‡developer.settrade.com](https://developer.settrade.com/open-api/api-reference?utm_source=chatgpt.com)

---

## 📦 Features

- 🔌 Direct MQTT connection to Settrade’s real-time data feed (no SDK layer)
- 📡 Parse binary protobuf messages for depth & bid/offer data
- 🧠 Normalized data model for easier downstream processing
- ⚡ Minimal allocations & overhead for low-latency use cases
- 🧰 Easy integration with event dispatcher / strategy loops

---

## 🚀 What This Adapter Solves

The official SDK wraps protobuf parsing and MQTT callbacks with extra layers (like converting to Python dicts and spawning threads), which can add overhead for high-throughput market data.  
This adapter:

- Avoids socket thread per message
- Avoids automatic dict conversion
- Parses protobuf directly
- Emits normalized Python objects

---

## 🧠 Requirements

- Python 3.10+
- `paho-mqtt` for MQTT transport
- Protobuf definitions from the official Settrade SDK (`settrade_v2.pb.*`)
- A valid Settrade Open API **App ID**, **App Secret**, and **Broker ID** (from https://developer.settrade.com/open-api)  [oai_citation:2‡Medium](https://theerapatcha.medium.com/trading-thai-stock-market-using-settrade-open-api-58e4b3cebb81?utm_source=chatgpt.com)

---

## 🧩 Installation

The adapter itself isn’t published on PyPI yet (if it is later, replace with `pip install settrade-feed-adapter`):

```bash
git clone https://github.com/yourorg/settrade-feed-adapter.git
cd settrade-feed-adapter
pip install -r requirements.txt

Also install the official protobuf schemas for parsing:

pip install settrade-v2
```

⸻

📌 Quick Start

1. Fetch Host & Token

Use the REST API from settrade_v2.context.Context to fetch:
	•	MQTT host
	•	Token for subscribing to real-time feed

This typically requires your App ID, Secret & Broker ID.

2. Connect MQTT

Use paho.mqtt.client.Client over WebSocket with SSL (transport="websockets") on port 443, setting your authorization token in headers.

Subscribe to topics like:

proto/topic/bidofferv3/<SYMBOL>

3. Parse Protobuf

Using the protobuf class (e.g., BidOfferV3) from settrade_v2.pb.bidofferv3_pb2, parse the raw message payload:

pb = BidOfferV3()
pb.ParseFromString(msg.payload)

Extract fields like symbol, bid_price1, ask_price1, bid_volume1, ask_volume1, etc., then emit normalized events downstream.

⸻

📊 Event Model

Here is a recommended internal event model:

@dataclass(slots=True)
class BestBidAsk:
    symbol: str
    bid: float
    ask: float
    bid_vol: int
    ask_vol: int
    recv_ts: int  # client timestamp


⸻

🧪 Example Adapter Usage

from infra.settrade_mqtt import SettradeMQTTClient
from infra.settrade_adapter import SettradeBidOfferAdapter
from core.dispatcher import Dispatcher

ctx = Context(app_id, app_secret, broker_id)
mqtt_client = SettradeMQTTClient(ctx)
mqtt_client.connect()
mqtt_client.loop_start()

dispatcher = Dispatcher()
adapter = SettradeBidOfferAdapter(mqtt_client, dispatcher)

adapter.subscribe("AOT")

while True:
    for event in dispatcher.poll():
        process(event)  # your strategy logic


⸻

🧠 Why This Works Well

By bypassing the SDK’s higher-level subscription helpers (which convert to dicts and spawn threads), you eliminate unnecessary overhead in the message path and gain better control over:
	•	threading model
	•	buffer allocation
	•	opportunity to integrate shared memory / zero-copy queue
	•	latency profiling

This is crucial for building a production-grade, event-driven trading engine.

⸻

🛠 Development
	•	tests/
	•	docs/
	•	examples/
	•	benchmarks/

⸻

🆘 Notes
	•	Ensure your API credentials can fetch real-time feeds (some broker sandbox accounts may have limitations)  
	•	Market data structure may evolve — always refer to official Settrade docs

⸻

📄 License

MIT License

⸻

❤️ Contributing
	1.	Fork it
	2.	Build in a feature branch
	3.	Write tests
	4.	Submit a PR

⸻


---
