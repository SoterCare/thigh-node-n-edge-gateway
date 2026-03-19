"""
SoterCare — backend_sync.py
Tails the local Redis stream written by gateway_master.py and forwards
batches to the SoterCare cloud backend via WebSocket (primary) or HTTP
fallback.  All flushing happens on a single background thread to avoid
race conditions and request floods.
"""

import os
import time
import json
import logging
import threading
import socketio
import requests
from typing import Dict, Any, List
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

SERVER_BASE_URL   = os.getenv("SERVER_BASE_URL", "https://backend.sotercare.com")
DEVICE_KEY        = os.getenv("DEVICE_KEY")
RASPBERRY_API_KEY = os.getenv("RASPBERRY_API_KEY")
INGEST_MODE       = os.getenv("INGEST_MODE", "ws").lower()
BATCH_SIZE        = int(os.getenv("BATCH_SIZE", "10"))
FLUSH_INTERVAL_S  = float(os.getenv("FLUSH_INTERVAL_S", "5"))   # seconds between flushes
RETRY_BASE_S      = int(os.getenv("RETRY_BASE_MS", "1000")) / 1000
RETRY_MAX_S       = int(os.getenv("RETRY_MAX_MS", "60000")) / 1000
DEVICE_ID         = os.getenv("DEVICE_ID", "pi-ffc585939c23")
DISK_SPOOL_FILE   = "pending_logs.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("CloudSync")

if not DEVICE_KEY:
    logger.error("FATAL: DEVICE_KEY is not set in .env — exiting.")
    raise SystemExit(1)


# ── Gateway Client ────────────────────────────────────────────────────────────
class GatewayClient:
    def __init__(self) -> None:
        self.sio = socketio.Client(
            reconnection=True,
            reconnection_delay=2,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )
        self._queue: List[Dict[str, Any]] = []
        self._lock   = threading.Lock()
        self._id_seq = 0

        self.ws_authenticated = False

        self.counters: Dict[str, int] = {
            "generated": 0, "sent": 0, "failed": 0,
        }

        self._setup_socket_events()
        self._load_spool()

    # ── Socket.IO ─────────────────────────────────────────────────────────────
    def _setup_socket_events(self) -> None:
        @self.sio.on("connect", namespace="/realtime")
        def _on_connect():
            logger.info("WS connected — sending device_auth")
            # Send as event (some backends) and mark authenticated immediately
            # since backend may not send a confirmation event
            self.sio.emit("device_auth", {
                "device_id":  DEVICE_ID,
                "device_key": DEVICE_KEY,
                "timestamp":  int(time.time() * 1000),
            }, namespace="/realtime")
            # Treat connect itself as auth success — backend validates device_key server-side
            self.ws_authenticated = True
            logger.info("WS marked as authenticated — will send via WebSocket")

        # Also listen for explicit auth confirmations in case the backend does emit them
        for _evt in ("auth_success", "authenticated", "device_authenticated", "auth_ok"):
            @self.sio.on(_evt, namespace="/realtime")
            def _on_auth(data=None, evt=_evt):
                logger.info(f"WS auth confirmed via '{evt}': {data}")
                self.ws_authenticated = True

        @self.sio.on("connect_error", namespace="/realtime")
        def _on_error(data):
            logger.error(f"WS connect error: {data}")
            self.ws_authenticated = False

        @self.sio.on("disconnect", namespace="/realtime")
        def _on_disconnect():
            logger.warning("WS disconnected — falling back to HTTP")
            self.ws_authenticated = False

        # Log any unexpected events from the server for debugging
        @self.sio.on("*", namespace="/realtime")
        def _on_any(event, data=None):
            logger.info(f"[WS EVENT] '{event}' → {data}")

    # ── Disk spool ────────────────────────────────────────────────────────────
    def _load_spool(self) -> None:
        if not os.path.exists(DISK_SPOOL_FILE):
            return
        try:
            with open(DISK_SPOOL_FILE) as f:
                for line in f:
                    if line.strip():
                        self._queue.append(json.loads(line))
            logger.info(f"Loaded {len(self._queue)} spooled records")
        except Exception as e:
            logger.error(f"Spool load error: {e}")

    def _save_spool(self, snapshot: List[Dict]) -> None:
        """Persist queue snapshot to disk (call WITHOUT holding _lock)."""
        try:
            with open(DISK_SPOOL_FILE, "w") as f:
                for r in snapshot:
                    f.write(json.dumps(r) + "\n")
        except Exception as e:
            logger.error(f"Spool save error: {e}")

    # ── Device registration ───────────────────────────────────────────────────
    def register_device(self) -> None:
        url = f"{SERVER_BASE_URL}/devices/register"
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if RASPBERRY_API_KEY:
            headers["x-admin-key"] = RASPBERRY_API_KEY
        try:
            resp = requests.post(
                url,
                json={"device_id": DEVICE_ID, "device_key": DEVICE_KEY},
                headers=headers,
                timeout=10,
            )
            logger.info(f"Register → {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Registration failed (non-fatal): {e}")

    # ── WS connect (runs in background thread) ────────────────────────────────
    def connect_ws(self) -> None:
        try:
            logger.info(f"Connecting WS to {SERVER_BASE_URL}/realtime ...")
            # Pass credentials in Socket.IO v4 connection auth (handshake level)
            self.sio.connect(
                SERVER_BASE_URL,
                namespaces=["/realtime"],
                transports=["websocket"],
                wait_timeout=15,
                auth={
                    "device_id":  DEVICE_ID,
                    "device_key": DEVICE_KEY,
                }
            )
        except Exception as e:
            logger.error(f"WS initial connect failed: {e}")

    # ── Enqueue (called from Redis tail thread) ───────────────────────────────
    def add_record(self, record: Dict[str, Any]) -> None:
        """Thread-safe enqueue only. Flushing is handled by the flush thread."""
        with self._lock:
            self._id_seq += 1
            record["local_id"] = self._id_seq
            self._queue.append(record)
            self.counters["generated"] += 1

    # ── Flush thread ──────────────────────────────────────────────────────────
    def start_flush_thread(self) -> None:
        """Start the single background thread that drains the queue on a timer."""
        def _loop():
            retry_delay = RETRY_BASE_S
            while True:
                time.sleep(FLUSH_INTERVAL_S)
                with self._lock:
                    if not self._queue:
                        continue
                    batch = self._queue[:BATCH_SIZE]

                success = self._try_send(batch)

                with self._lock:
                    if success:
                        sent_ids = {r["local_id"] for r in batch}
                        self._queue = [r for r in self._queue if r["local_id"] not in sent_ids]
                        self.counters["sent"] += len(batch)
                        retry_delay = RETRY_BASE_S   # reset backoff
                        snap = list(self._queue)
                    else:
                        self.counters["failed"] += len(batch)
                        snap = list(self._queue)

                self._save_spool(snap)

                if not success:
                    retry_delay = min(retry_delay * 2, RETRY_MAX_S)
                    logger.warning(f"Send failed — backing off {retry_delay:.0f}s")
                    time.sleep(retry_delay)

        t = threading.Thread(target=_loop, daemon=True, name="FlushThread")
        t.start()
        logger.info(f"Flush thread started (interval={FLUSH_INTERVAL_S}s, batch={BATCH_SIZE})")

    def _try_send(self, batch: List[Dict]) -> bool:
        """Try WS first, then HTTP fallback."""
        if INGEST_MODE == "ws" and self.ws_authenticated:
            if self._send_ws(batch):
                return True
            logger.warning("WS send failed — trying HTTP fallback")

        return self._send_http(batch)

    def _send_ws(self, batch: List[Dict]) -> bool:
        try:
            self.sio.emit("device_data", {"logs": batch}, namespace="/realtime")
            logger.info(f"WS ↑ {len(batch)} records | total sent={self.counters['sent'] + len(batch)}")
            return True
        except Exception as e:
            logger.error(f"WS emit error: {e}")
            return False

    def _send_http(self, batch: List[Dict]) -> bool:
        url = f"{SERVER_BASE_URL}/logs/raspberry/sync"
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "x-device-key": DEVICE_KEY,  # type: ignore[assignment]
        }
        if RASPBERRY_API_KEY:
            headers["x-raspberry-key"] = RASPBERRY_API_KEY
        try:
            resp = requests.post(
                url,
                json={"device_id": DEVICE_ID, "logs": batch},
                headers=headers,
                timeout=15,
            )
            if resp.status_code in (200, 201, 202):
                logger.info(f"HTTP ↑ {len(batch)} records → {resp.status_code} | total sent={self.counters['sent'] + len(batch)}")
                return True
            logger.error(f"HTTP error {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"HTTP request failed: {e}")
            return False


# ── Main ──────────────────────────────────────────────────────────────────────
def run_gateway() -> None:
    client = GatewayClient()
    client.register_device()

    # WebSocket connection runs in a background daemon thread
    if INGEST_MODE == "ws":
        threading.Thread(target=client.connect_ws, daemon=True, name="WSConnect").start()

    # Single flush thread — the ONLY place that calls send
    client.start_flush_thread()

    # Redis tail — just enqueues, never flushes
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        last_id = "$"
        logger.info("Tailing Redis stream 'sotercare_history'...")

        while True:
            try:
                results = r.xread({"sotercare_history": last_id}, count=50, block=1000)
                if results:
                    for _, messages in results:
                        for msg_id, fields in messages:
                            last_id = msg_id
                            ts_s = float(fields.get("ts", time.time()))
                            client.add_record({
                                "id":          int(ts_s * 1000),
                                "heartRate":   0,
                                "spo2":        0,
                                "temperature": float(fields.get("temp", 0)),
                                "timestamp":   int(ts_s * 1000),
                                "accX":        float(fields.get("accX", 0)),
                                "accY":        float(fields.get("accY", 0)),
                                "accZ":        float(fields.get("accZ", 0)),
                                "gTotal":      float(fields.get("gTotal", 0)),
                                "ambientTemp": float(fields.get("ambientTemp", 0)),
                                "moisture":    int(fields.get("moisture", 0)),
                                "sos":         int(fields.get("sos", 0)),
                                "fallAlert":   int(fields.get("fallAlert", 0)),
                                "gaitLabel":   fields.get("gaitLabel", "N/A"),
                            })
            except redis.exceptions.ConnectionError:
                logger.warning("Redis unavailable — retrying in 5s")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Redis tail error: {e}")
                time.sleep(1)

    except ImportError:
        logger.warning("Redis not installed — falling back to dummy data")
        import itertools
        for _ in itertools.count():
            client.add_record({
                "id": int(time.time() * 1000),
                "heartRate": 75, "spo2": 98, "temperature": 36.6,
                "timestamp": int(time.time() * 1000),
                "accX": 0.1, "accY": 0.9, "accZ": 0.2, "gTotal": 0.92,
                "ambientTemp": 25.0, "moisture": 300,
                "sos": 0, "fallAlert": 0, "gaitLabel": "Walking",
            })
            time.sleep(1.0)


if __name__ == "__main__":
    try:
        run_gateway()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
