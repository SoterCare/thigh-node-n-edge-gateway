#!/usr/bin/env python3
"""
SoterCare Edge Gateway — gateway_master.py
Receives sensor data from the Thigh Node via:
  - UDP (primary):  0.0.0.0:1234  at ~60Hz
  - BLE (fallback): MedNode_BLE, Nordic UART TX characteristic
Resamples to 50Hz, runs Edge Impulse gait model, writes to Redis Stream.

Architecture: single process, asyncio for BLE + threading for UDP + Redis.
Avoids Windows multiprocessing spawn issues with shared memory.
"""

import asyncio
import math
import os
import queue
import socket
import subprocess
import threading
import time

import redis
from bleak import BleakClient, BleakScanner
from fall_detector import FallDetector

# ── Configuration ─────────────────────────────────────────────────────────────
UDP_HOST        = "0.0.0.0"
UDP_PORT        = 1234
BLE_DEVICE_NAME = "MedNode_BLE"
BLE_TX_UUID     = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

REDIS_STREAM    = "sotercare_history"
REDIS_MAXLEN    = 1000

RESAMPLE_DROP_N = 6        # Drop 1 in 6 → 60Hz → 50Hz
BLE_TIMEOUT_S   = 2.5      # Seconds of UDP silence before BLE activates
# Fall detection constants moved to fall_detector.py

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "gait_model.eim")

# ── Shared state (thread-safe, single process) ────────────────────────────────
frame_q        = queue.Queue(maxsize=2000)
udp_last_seen  = 0.0
udp_ever_seen  = False
connection_mode = "searching"
_state_lock    = threading.Lock()

# ── Hz diagnostic counters (thread-safe via lock) ─────────────────────────────
_diag_lock      = threading.Lock()
_udp_rx_count   = 0    # Frames received from firmware (UDP or BLE)
_redis_wr_count = 0    # Frames actually written to Redis
_drop_count     = 0    # Frames dropped by 20ms gate


def set_state(mode: str, ts: float | None = None):
    global connection_mode, udp_last_seen, udp_ever_seen
    with _state_lock:
        if mode:
            connection_mode = mode
        if ts is not None:
            udp_last_seen = ts
            udp_ever_seen = True


def wifi_is_alive() -> bool:
    """True only if a real UDP packet was received within BLE_TIMEOUT_S seconds."""
    with _state_lock:
        if not udp_ever_seen:
            return False
        return (time.time() - udp_last_seen) < BLE_TIMEOUT_S


# ── Frame parser ──────────────────────────────────────────────────────────────
def parse_frame(raw: str, source: str) -> dict | None:
    """
    Wi-Fi (8 fields): AccX,AccY,AccZ,ObjTempC,AmbientTempC,MoisturePercent,RSSI_dBm,SOS
    BLE   (8 fields): AccX,AccY,AccZ,ObjTempC,AmbientTempC,MoisturePercent,0,SOS
    """
    parts = raw.strip().split(",")
    if len(parts) < 6:
        return None
    try:
        rssi = int(parts[6]) if len(parts) >= 7 and parts[6].strip() != '0' else None
        sos  = int(parts[7]) if len(parts) >= 8 else 0
        return {
            "accX":        float(parts[0]),
            "accY":        float(parts[1]),
            "accZ":        float(parts[2]),
            "temp":        float(parts[3]),  # Object (Patient)
            "ambientTemp": float(parts[4]),  # Ambient (Room)
            "moisture":    int(float(parts[5])),
            "rssi":        rssi,
            "sos":         sos,
            "source":      source,
            "ts":          time.time(),
        }
    except (ValueError, IndexError):
        return None


# ── UDP Listener Thread ───────────────────────────────────────────────────────
def udp_listener_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Large receive buffer so the OS doesn't drop burst datagrams
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
    sock.bind((UDP_HOST, UDP_PORT))
    sock.settimeout(0.5)   # Short timeout keeps loop responsive
    print(f"[UDP] Listening on {UDP_HOST}:{UDP_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(512)
            raw = data.decode("utf-8", errors="ignore")
            frame = parse_frame(raw, "wifi")
            if frame:
                set_state("wifi", ts=time.time())
                with _diag_lock:
                    global _udp_rx_count
                    _udp_rx_count += 1
                try:
                    frame_q.put_nowait(frame)
                except queue.Full:
                    pass
        except socket.timeout:
            pass
        except Exception as e:
            print(f"[UDP] Error: {e}")


# ── BLE Async Task ────────────────────────────────────────────────────────────
async def ble_task():
    """
    Monitors UDP silence. When UDP drops out, connects to MedNode_BLE.
    Disconnects and stands down as soon as UDP resumes.
    """
    global connection_mode
    client: BleakClient | None = None
    ble_active = False

    # Give the UDP listener a few seconds to start before BLE kicks in
    await asyncio.sleep(4.0)
    print("[BLE] Watchdog started.")

    while True:
        if wifi_is_alive():
            # UDP is healthy — ensure BLE is disconnected
            if ble_active and client and client.is_connected:
                print("[BLE] UDP active. Disconnecting BLE.")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = None
                ble_active = False
            set_state("wifi")
            await asyncio.sleep(0.5)
            continue

        # UDP is silent — activate BLE if not already connected
        if not ble_active:
            with _state_lock:
                connection_mode = "searching"
            print("[BLE] UDP silent. Scanning for MedNode_BLE...")

            device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=10.0)
            if device is None:
                print("[BLE] MedNode_BLE not found. Will retry...")
                await asyncio.sleep(3.0)
                continue

            def on_notify(sender, data):
                raw = data.decode("utf-8", errors="ignore")
                frame = parse_frame(raw, "ble")
                if frame:
                    set_state("ble")
                    try:
                        frame_q.put_nowait(frame)
                    except queue.Full:
                        pass

            try:
                client = BleakClient(device, disconnected_callback=lambda _: None)
                await client.connect(timeout=10.0)
                await client.start_notify(BLE_TX_UUID, on_notify)
                ble_active = True
                set_state("ble")
                print(f"[BLE] Connected to {BLE_DEVICE_NAME}")
            except Exception as e:
                print(f"[BLE] Connection failed: {e}")
                client = None
                await asyncio.sleep(3.0)
                continue

        # While BLE is active, keep checking if UDP comes back
        await asyncio.sleep(0.5)

        # If BLE client dropped unexpectedly, reset
        if ble_active and client and not client.is_connected:
            print("[BLE] Connection dropped. Will reconnect.")
            client = None
            ble_active = False
            set_state("searching")


# ── Pipeline Thread: IMU → FallDetector + GaitAI → Redis ────────────────────
def pipeline_thread():
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)

    # ── Fall Detector (independent of gait AI) ────────────────────────────
    fd = FallDetector()

    # ── Gait AI: Edge Impulse model (optional) ────────────────────────────
    runner = None
    try:
        from edge_impulse_linux.runner import ImpulseRunner
        runner = ImpulseRunner(MODEL_PATH)
        info = runner.init()
        print(f"[AI] Model loaded: {info['project']['name']}")
    except Exception as e:
        print(f"[AI] Model unavailable ({e}). Gait = N/A")

    imu_window = []

    while True:
        try:
            frame = frame_q.get(timeout=0.5)
        except queue.Empty:
            continue

        # No rate limiting — firmware is hardware-capped at ~50Hz.
        # Just count and write every valid frame to Redis.
        with _diag_lock:
            global _redis_wr_count
            _redis_wr_count += 1


        ax, ay, az = frame["accX"], frame["accY"], frame["accZ"]
        g_total = math.sqrt(ax**2 + ay**2 + az**2)

        # ── Fall Detection (two-phase state machine in fall_detector.py) ──
        # Runs on every raw frame, completely independent of gait AI.
        fall_detected, fall_info = fd.update(g_total, frame["ts"])
        fall_alert = 1 if fall_detected else 0
        if fall_detected:
            try:
                subprocess.Popen(
                    ["espeak-ng", "Fall detected. Please check the patient."],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                # Ignore if espeak-ng is not installed (e.g. on Windows)
                pass

        # AI inference
        gait_label = "N/A"
        if runner:
            imu_window.append([ax, ay, az])
            win = getattr(runner, "get_input_features_count", lambda: 150)() // 3
            if len(imu_window) >= win:
                try:
                    features = [v for s in imu_window[-win:] for v in s]
                    res = runner.classify({"features": features})
                    cls = res.get("result", {}).get("classification", {})
                    if cls:
                        gait_label = max(cls, key=cls.get)
                except Exception:
                    pass
                imu_window = imu_window[-win:]

        # Write to Redis
        fields = {
            "accX":      f"{ax:.4f}",
            "accY":      f"{ay:.4f}",
            "accZ":      f"{az:.4f}",
            "gTotal":    f"{g_total:.4f}",
            "temp":        f"{frame['temp']:.2f}",
            "ambientTemp": f"{frame['ambientTemp']:.2f}",
            "moisture":    str(frame["moisture"]),
            "rssi":        str(frame["rssi"]) if frame["rssi"] is not None else "N/A",
            "sos":       str(frame.get("sos", 0)),   # Help Call button flag
            "source":    frame["source"],
            "gaitLabel": gait_label,
            "fallAlert": str(fall_alert),
            "ts":        f"{frame['ts']:.3f}",
        }
        try:
            r.xadd(REDIS_STREAM, fields, maxlen=REDIS_MAXLEN, approximate=True)
        except Exception as e:
            print(f"[Redis] Write error: {e}")


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 52)
    print("   SoterCare Edge Gateway  —  Starting")
    print("=" * 52)

    # Start UDP listener in background thread
    t_udp = threading.Thread(target=udp_listener_thread, daemon=True, name="UDP")
    t_udp.start()
    print(f"[MAIN] UDP listener thread started.")

    # Start pipeline (Redis writer) in background thread
    t_pipe = threading.Thread(target=pipeline_thread, daemon=True, name="Pipeline")
    t_pipe.start()
    print(f"[MAIN] Pipeline thread started.")

    async def main():
        # BLE watchdog runs as async task in main event loop
        ble = asyncio.create_task(ble_task())

        # Status reporter
        async def status_loop():
            while True:
                await asyncio.sleep(5)
                with _state_lock:
                    mode = connection_mode
                q_size = frame_q.qsize()
                # Snapshot and reset Hz counters
                with _diag_lock:
                    udp_hz  = _udp_rx_count  // 5
                    pipe_hz = _redis_wr_count // 5
                    drops   = _drop_count
                    globals().update(_udp_rx_count=0, _redis_wr_count=0, _drop_count=0)
                status = (
                    f"[STATUS] Mode={mode.upper():10s}  "
                    f"UDP_RX={udp_hz:3d}Hz  "
                    f"Redis_WR={pipe_hz:3d}Hz  "
                    f"Dropped={drops:4d}  "
                    f"Queue={q_size}"
                )
                print(status)
                # Warn if firmware is sending slow
                if udp_hz < 40 and mode != 'searching':
                    print(f"  [WARN] Firmware sending only {udp_hz}Hz — check firmware loop blocking")
                if pipe_hz < 40 and udp_hz >= 40:
                    print(f"  [WARN] Pipeline bottleneck: {udp_hz}Hz in, {pipe_hz}Hz to Redis")

        await asyncio.gather(ble, status_loop())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[MAIN] Shutting down.")
