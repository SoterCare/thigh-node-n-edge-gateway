"""
SoterCare — backend_sync.py
Tails the Redis stream written by gateway_master.py and forwards each
frame to the SoterCare cloud backend via WebSocket exclusively, exactly matching the
protocol in ws-prod-check.cjs:

  • Connect to wss://backend.sotercare.com  namespace: /realtime
  • On connect  → emit device_auth  → wait for ack callback
  • After auth  → emit device_data { logs: [singleEntry] } every 1 s
                 → wait for ack callback before sending next
"""

import os
import time
import logging
import threading
import socketio
import redis
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

SERVER_BASE_URL   = os.getenv("SERVER_BASE_URL", "https://backend.sotercare.com")
# socket.io url specifically using wss
WS_SERVER_URL     = SERVER_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

DEVICE_KEY        = os.getenv("DEVICE_KEY")
DEVICE_ID         = os.getenv("DEVICE_ID", "pi-ffc585939c23")
SEND_INTERVAL_S   = float(os.getenv("SEND_INTERVAL_S", "1.0"))   # 1 log per second (CJS parity)
REDIS_STREAM      = "sotercare_history"                            # written by gateway_master.py

RETRY_BASE_S      = int(os.getenv("RETRY_BASE_MS",  "1000")) / 1000
RETRY_MAX_S       = int(os.getenv("RETRY_MAX_MS",  "60000")) / 1000

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CloudSync")

if not DEVICE_KEY:
    logger.error("FATAL: DEVICE_KEY not set in .env — exiting.")
    raise SystemExit(1)

# ── Client ────────────────────────────────────────────────────────────────────
class GatewayClient:
    def __init__(self) -> None:
        self.sio = socketio.Client(
            reconnection=True,
            reconnection_delay=2,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )
        self.redis = redis.Redis(host="localhost", port=6379, decode_responses=True)

        self.ws_authenticated = False
        self.total_sent = 0

        # Event to signal when device_auth ack has been received
        self._auth_done = threading.Event()

        self._register_events()

    # ── Socket.IO events ─────────────────────────────────────────────────────
    # namespace is /realtime, mirroring CJS: io(".../realtime")
    def _register_events(self) -> None:

        @self.sio.on("connect", namespace="/realtime")
        def _on_connect():
            logger.info(f"[WS] Connected to {WS_SERVER_URL}/realtime — sending device_auth…")
            self.ws_authenticated = False
            self._auth_done.clear()
            
            def auth_ack(ack_data):
                logger.info(f"[WS] authAck: {ack_data}")
                self.ws_authenticated = True
                self._auth_done.set()

            self.sio.emit(
                "device_auth",
                {
                    "device_id":  DEVICE_ID,
                    "device_key": DEVICE_KEY,
                    "timestamp":  int(time.time() * 1000),
                },
                namespace="/realtime",
                callback=auth_ack
            )

        @self.sio.on("exception", namespace="/realtime")
        def _on_exception(data):
            logger.error(f"[WS] server exception: {data}")
            self.ws_authenticated = False
            self._auth_done.set()    # stop waiting

        @self.sio.on("connect_error", namespace="/realtime")
        def _on_connect_error(data):
            logger.error(f"[WS] connect_error: {data}")
            self.ws_authenticated = False
            self._auth_done.set()

        @self.sio.on("disconnect", namespace="/realtime")
        def _on_disconnect():
            logger.warning("[WS] Disconnected")
            self.ws_authenticated = False
            self._auth_done.set()

    # ── WS connection loop (background thread) ────────────────────────────────
    def connect_ws(self) -> None:
        """Connects to root server and accesses the /realtime namespace"""
        while True:
            try:
                logger.info(f"[WS] Connecting to {WS_SERVER_URL} for namespace /realtime …")
                self.sio.connect(WS_SERVER_URL, namespaces=["/realtime"], transports=["websocket"], wait_timeout=15)
                self.sio.wait()          # blocks until disconnected, then retries
            except Exception as e:
                logger.error(f"[WS] Connect failed: {e} — retry in 5 s")
                time.sleep(5)

    # ── Send one log entry via WS (mirrors CJS setInterval body) ─────────────
    def _send_ws_one(self, log_entry: Dict[str, Any]) -> bool:
        """
        emit("device_data", { logs: [logEntry] }, callback)  — like CJS.
        Waits for the ack callback before returning.
        """
        ack_event = threading.Event()
        ack_result = {"success": False}
        
        def data_ack(ack_data):
            if type(ack_data) == dict and ack_data.get("success"):
                ack_result["success"] = True
            elif type(ack_data) == list and len(ack_data) > 0 and type(ack_data[0]) == dict and ack_data[0].get("success"):
                ack_result["success"] = True
            elif ack_data:
                # Based on js script logs
                ack_result["success"] = True
            ack_event.set()

        try:
            self.sio.emit("device_data", {"logs": [log_entry]}, namespace="/realtime", callback=data_ack)
            
            # Wait up to 5 seconds for success ack
            ack_event.wait(timeout=5.0)
            
            if ack_result["success"]:
                self.total_sent += 1
                logger.info(f"[WS ↑] log sent | total={self.total_sent}")
                return True
            else:
                logger.warning("[WS] Emit timeout or no success ack returned")
                return False
        except Exception as e:
            logger.error(f"[WS] emit error: {e}")
            return False

    # ── Redis stream → log entry ──────────────────────────────────────────────
    def _parse_fields(self, fields: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        Converts a gateway_master Redis stream entry into the log shape that
        the backend expects (same fields as in ws-prod-check.cjs logEntry).
        """
        try:
            ts = float(fields.get("ts", time.time()))
            now = time.time()
            if ts > now + 60:
                logger.warning(f"[SKIP] Future timestamp {ts:.0f} (now={now:.0f}) — dropping frame")
                return None
            return {
                "temp":           float(fields.get("temp", 0)),
                "ambientTemp":    float(fields.get("ambientTemp", 0)),
                "moisture":       int(fields.get("moisture", 0)),
                "gait_label":     (
                    fields.get("gaitLabel", "N/A")
                    .lower().replace("_", " ").replace("-", " ").strip()
                ),
                "sos_trigger":    int(fields.get("sos", 0)) == 1,
                "fall_alert":     int(fields.get("fallAlert", 0)) == 1,
                "unix_timestamp": int(ts),
            }
        except Exception as e:
            logger.error(f"[PARSE] Error: {e} — fields={fields}")
            return None

    # ── Main sync loop ────────────────────────────────────────────────────────
    def run_sync_loop(self) -> None:
        """
        Tail `sotercare_history` and forward one frame per SEND_INTERVAL_S,
        mirroring the CJS setInterval(…, 1000) pattern.

        Cursor advances only after a successful send — no data loss on failures.
        """
        last_id = "$"           # start from newest data
        retry_delay = RETRY_BASE_S
        logger.info(f"[SYNC] Tailing '{REDIS_STREAM}' — 1 log/{SEND_INTERVAL_S}s (CJS parity)")

        while True:
            cycle_start = time.time()

            # ── Read next frame from Redis stream (block up to 1 s) ──────────
            entry: Optional[Dict[str, Any]] = None
            new_last_id = last_id
            try:
                results = self.redis.xread(
                    {REDIS_STREAM: last_id}, count=1, block=1000
                )
                if results:
                    for _, messages in results:
                        for msg_id, fields in messages:
                            new_last_id = msg_id
                            entry = self._parse_fields(fields)
            except redis.exceptions.ConnectionError:
                logger.warning("[REDIS] Unavailable — retry in 5 s")
                time.sleep(5)
                continue
            except Exception as e:
                logger.error(f"[REDIS] Read error: {e}")
                time.sleep(1)
                continue

            if entry is None:
                # No valid data — loop immediately (don't sleep, xread already blocked)
                continue

            # ── Send exclusively via WS ────────
            sent = False
            if self.ws_authenticated:
                sent = self._send_ws_one(entry)
            else:
                logger.warning("[SYNC] WS not authenticated yet — waiting")
                # wait a bit for ws to reconnect or authenticate
                time.sleep(1)

            if sent:
                last_id = new_last_id          # advance cursor on success
                retry_delay = RETRY_BASE_S

                # Pace to SEND_INTERVAL_S (CJS: setInterval 1000 ms)
                elapsed = time.time() - cycle_start
                sleep_for = max(0.0, SEND_INTERVAL_S - elapsed)
                if sleep_for > 0:
                    time.sleep(sleep_for)
            else:
                retry_delay = min(retry_delay * 2, RETRY_MAX_S)
                logger.warning(f"[SYNC] Send failed — backoff {retry_delay:.0f} s")
                time.sleep(retry_delay)

# ── Entry point ───────────────────────────────────────────────────────────────
def run_gateway() -> None:
    client = GatewayClient()

    threading.Thread(
        target=client.connect_ws, daemon=True, name="WSConnect"
    ).start()
    
    # Wait up to 10 s for WS to connect AND device_auth ack to arrive
    # before starting the sync loop (mirrors CJS flow where sends start
    # only inside the authAck callback)
    logger.info("[SYNC] Waiting for WS auth (up to 10 s)…")
    if client._auth_done.wait(timeout=10.0):
        if client.ws_authenticated:
            logger.info("[SYNC] WS auth confirmed — starting sync loop")
        else:
            logger.warning("[SYNC] WS auth disconnected or failed")
    else:
        logger.warning("[SYNC] WS auth timed out after 10 s")

    client.run_sync_loop()

if __name__ == "__main__":
    try:
        run_gateway()
    except KeyboardInterrupt:
        logger.info("[SYNC] Shutting down.")
