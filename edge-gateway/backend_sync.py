"""
SoterCare — backend_sync.py  (Catch-Up Buffer Service)
Reads from the Redis buffer stream written by cloud_sync_thread in
gateway_master.py when the network is offline, and syncs those entries
to the cloud backend once connectivity is restored.

Normal operation: cloud_sync_thread handles real-time delivery directly
from the in-process cloud_q. This script mostly idles and only activates
when the gateway was offline and has buffered data to replay.
"""

import os
import time
import logging
import threading
import socketio
import redis
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

load_dotenv()

SERVER_BASE_URL      = os.getenv("SERVER_BASE_URL", "wss://backend.sotercare.com")
WS_SERVER_URL        = SERVER_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
DEVICE_KEY           = os.getenv("DEVICE_KEY")
DEVICE_ID            = os.getenv("DEVICE_ID", "pi-ffc585939c23")
CLOUD_BUFFER_STREAM  = "sotercare_cloud_buffer"   # written by gateway_master on network failure
CATCHUP_BATCH        = int(os.getenv("CATCHUP_BATCH", "50"))
CATCHUP_INTERVAL_S   = float(os.getenv("CATCHUP_INTERVAL_S", "1.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CatchUp")

if not DEVICE_KEY:
    logger.error("FATAL: DEVICE_KEY not set in .env — exiting.")
    raise SystemExit(1)


class CatchUpClient:
    def __init__(self):
        self.sio = socketio.Client(
            reconnection=True, reconnection_delay=2,
            reconnection_delay_max=30, logger=False, engineio_logger=False,
        )
        self.redis = redis.Redis(host="localhost", port=6379, decode_responses=True)
        self.ws_authenticated = False
        self._auth_done = threading.Event()
        self.total_sent = 0
        self._register_events()

    def _register_events(self):
        @self.sio.on("connect", namespace="/realtime")
        def _on_connect():
            self.ws_authenticated = False
            self._auth_done.clear()
            logger.info("[WS] Connected — sending device_auth…")

            def auth_ack(ack):
                logger.info(f"[WS] authAck: {ack}")
                self.ws_authenticated = True
                self._auth_done.set()

            self.sio.emit("device_auth", {
                "device_id": DEVICE_ID, "device_key": DEVICE_KEY,
                "timestamp": int(time.time() * 1000),
            }, namespace="/realtime", callback=auth_ack)

        @self.sio.on("exception", namespace="/realtime")
        def _on_exception(data):
            logger.error(f"[WS] exception: {data}")
            self.ws_authenticated = False
            self._auth_done.set()

        @self.sio.on("connect_error", namespace="/realtime")
        def _on_connect_error(data):
            self.ws_authenticated = False
            self._auth_done.set()

        @self.sio.on("disconnect", namespace="/realtime")
        def _on_disconnect():
            logger.warning("[WS] Disconnected")
            self.ws_authenticated = False
            self._auth_done.set()

    def connect_ws(self):
        while True:
            try:
                self.sio.connect(WS_SERVER_URL, namespaces=["/realtime"],
                                 transports=["websocket"], wait_timeout=15)
                self.sio.wait()
            except Exception as e:
                logger.error(f"[WS] Connect failed: {e} — retry in 5 s")
                time.sleep(5)

    def _parse_entry(self, fields: Dict[str, str]) -> Optional[Dict[str, Any]]:
        try:
            ts = float(fields.get("unix_timestamp", fields.get("ts", time.time())))
            return {
                "temp":           float(fields.get("temp", 0)),
                "ambientTemp":    float(fields.get("ambientTemp", 0)),
                "moisture":       int(float(fields.get("moisture", 0))),
                "gait_label":     str(fields.get("gait_label", "N/A")),
                "sos_trigger":    str(fields.get("sos_trigger", "False")) == "True",
                "fall_alert":     str(fields.get("fall_alert", "False")) == "True",
                "unix_timestamp": int(ts),
            }
        except Exception as e:
            logger.error(f"[PARSE] {e}")
            return None

    def _emit_batch(self, logs: List[Dict[str, Any]]) -> None:
        def _ack(ack_data):
            success = (
                (isinstance(ack_data, dict) and ack_data.get("success")) or
                (isinstance(ack_data, list) and ack_data and isinstance(ack_data[0], dict) and ack_data[0].get("success")) or
                bool(ack_data)
            )
            if success:
                self.total_sent += len(logs)
                logger.info(f"[CatchUp ↑] batch={len(logs)} total={self.total_sent}")
            else:
                logger.warning(f"[CatchUp] ack failure: {ack_data}")
        try:
            self.sio.emit("device_data", {"logs": logs}, namespace="/realtime", callback=_ack)
        except Exception as e:
            logger.error(f"[CatchUp] emit error: {e}")

    def run_catchup_loop(self):
        """
        Drains sotercare_cloud_buffer (filled when gateway_master was offline).
        Starts from the beginning of the stream (0-0) to replay any offline data.
        After drain, polls for new buffer entries every CATCHUP_INTERVAL_S.
        """
        last_id = "0-0"   # start from beginning to replay full buffer on startup
        drained = False
        logger.info(f"[CatchUp] Watching '{CLOUD_BUFFER_STREAM}' for offline-buffered data…")

        while True:
            try:
                results = self.redis.xread(
                    {CLOUD_BUFFER_STREAM: last_id}, count=CATCHUP_BATCH, block=2000
                )
                if results:
                    entries = []
                    new_last_id = last_id
                    for _, messages in results:
                        for msg_id, fields in messages:
                            new_last_id = msg_id
                            entry = self._parse_entry(fields)
                            if entry:
                                entries.append(entry)

                    if entries and self.ws_authenticated:
                        self._emit_batch(entries)
                        last_id = new_last_id
                        if not drained:
                            logger.info("[CatchUp] Buffered data drained — switching to monitor mode")
                            drained = True
                    elif not self.ws_authenticated:
                        logger.warning("[CatchUp] Not authenticated — waiting")
                        time.sleep(1)
                else:
                    # Stream empty — idle until new entries appear
                    if not drained:
                        logger.info("[CatchUp] No buffered data — monitoring for offline events")
                        drained = True
                    time.sleep(CATCHUP_INTERVAL_S)

            except redis.exceptions.ConnectionError:
                logger.warning("[REDIS] Unavailable — retry in 5 s")
                time.sleep(5)
            except Exception as e:
                logger.error(f"[CatchUp] Error: {e}")
                time.sleep(1)


def run_catchup():
    client = CatchUpClient()
    threading.Thread(target=client.connect_ws, daemon=True, name="WSConnect").start()
    logger.info("[CatchUp] Waiting for WS auth (up to 10 s)…")
    client._auth_done.wait(timeout=10.0)
    if client.ws_authenticated:
        logger.info("[CatchUp] Auth confirmed — starting catch-up loop")
    else:
        logger.warning("[CatchUp] Auth not confirmed — continuing (will retry)")
    client.run_catchup_loop()


if __name__ == "__main__":
    try:
        run_catchup()
    except KeyboardInterrupt:
        logger.info("[CatchUp] Shutting down.")
