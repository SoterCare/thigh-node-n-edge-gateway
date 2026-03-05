#!/usr/bin/env python3
"""
SoterCare Edge Gateway — gateway_master.py
Raspberry Pi 5 main process. Receives sensor data from the Thigh Node via:
  - UDP (primary):  192.168.1.100:1234  at ~60Hz
  - BLE (fallback): MedNode_BLE, Nordic UART TX 6E400003  at ~60Hz
Resamples to 50Hz, runs Edge Impulse gait model, writes to Redis Stream.
"""

import asyncio
import multiprocessing
import os
import queue
import socket
import subprocess
import threading
import time
import math
from datetime import datetime

import redis
from bleak import BleakClient, BleakScanner

# ── Configuration ─────────────────────────────────────────────────────────────
UDP_HOST        = "0.0.0.0"
UDP_PORT        = 1234           # Must match udpPort in thigh-node-firmware.ino
BLE_DEVICE_NAME = "MedNode_BLE"  # Must match BLEDevice::init() in firmware
BLE_TX_UUID     = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # Nordic UART TX (notify)
BLE_SVC_UUID    = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"

REDIS_STREAM    = "sotercare_history"
REDIS_MAXLEN    = 1000  # ~20s of data at 50Hz

# Resampling: drop 1 in N to go from ~60Hz → 50Hz (drop every 6th)
RESAMPLE_DROP_N = 6

# Gait model — place your .eim file here after export from Edge Impulse
MODEL_PATH      = os.path.join(os.path.dirname(__file__), "model", "gait_model.eim")

# Fall detection: G_total < threshold for this many seconds = alert
FALL_G_THRESHOLD  = 0.15  # g
FALL_DURATION_S   = 3.0

# BLE activates if UDP is silent for this many seconds
BLE_TIMEOUT_S   = 2.0

# ── Shared state ──────────────────────────────────────────────────────────────
frame_queue         = multiprocessing.Queue(maxsize=500)
udp_last_seen       = multiprocessing.Value("d", 0.0)   # epoch float
connection_mode     = multiprocessing.Value("i", 0)     # 0=searching, 1=wifi, 2=ble
ble_stop_event      = multiprocessing.Event()


# ── Frame parser ──────────────────────────────────────────────────────────────
def parse_frame(raw: str, source: str) -> dict | None:
    """Parse a CSV line from the firmware into a dict.

    Wi-Fi  (6 fields): AccX,AccY,AccZ,ObjTempC,MoisturePercent,RSSI_dBm
    BLE    (5 fields): AccX,AccY,AccZ,ObjTempC,MoisturePercent
    """
    parts = raw.strip().split(",")
    if len(parts) < 5:
        return None
    try:
        return {
            "accX":     float(parts[0]),
            "accY":     float(parts[1]),
            "accZ":     float(parts[2]),
            "temp":     float(parts[3]),
            "moisture": int(parts[4]),
            "rssi":     int(parts[5]) if len(parts) >= 6 else None,
            "source":   source,
            "ts":       time.time(),
        }
    except (ValueError, IndexError):
        return None


# ── Process 1: UDP Listener ───────────────────────────────────────────────────
def udp_listener(q: multiprocessing.Queue, udp_ts: multiprocessing.Value):
    """Listens on UDP_PORT for datagrams from the Thigh Node."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_HOST, UDP_PORT))
    sock.settimeout(1.0)
    print(f"[UDP] Listening on {UDP_HOST}:{UDP_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(256)
            raw = data.decode("utf-8", errors="ignore")
            frame = parse_frame(raw, "wifi")
            if frame:
                udp_ts.value = time.time()
                with connection_mode.get_lock():
                    connection_mode.value = 1
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    pass
        except socket.timeout:
            pass
        except Exception as e:
            print(f"[UDP] Error: {e}")


# ── Process 2: BLE Fallback ───────────────────────────────────────────────────
def ble_fallback(q: multiprocessing.Queue, udp_ts: multiprocessing.Value, stop: multiprocessing.Event):
    """Activates BLE when UDP has been silent for >BLE_TIMEOUT_S seconds."""
    asyncio.run(_ble_main(q, udp_ts, stop))


async def _ble_main(q, udp_ts, stop):
    ble_active = False
    client: BleakClient | None = None

    while not stop.is_set():
        udp_age = time.time() - udp_ts.value
        wifi_alive = udp_age < BLE_TIMEOUT_S and udp_ts.value > 0

        if wifi_alive:
            # Wi-Fi is healthy — disconnect BLE if active
            if client and client.is_connected:
                print("[BLE] UDP resumed. Disconnecting BLE.")
                await client.disconnect()
                client = None
                ble_active = False
            await asyncio.sleep(0.5)
            continue

        if not ble_active:
            print(f"[BLE] UDP silent for {udp_age:.1f}s. Scanning for {BLE_DEVICE_NAME}...")
            with connection_mode.get_lock():
                connection_mode.value = 0  # searching

            device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=10.0)
            if device is None:
                print(f"[BLE] {BLE_DEVICE_NAME} not found. Retrying...")
                await asyncio.sleep(2.0)
                continue

            def notification_handler(sender, data):
                raw = data.decode("utf-8", errors="ignore")
                frame = parse_frame(raw, "ble")
                if frame:
                    try:
                        q.put_nowait(frame)
                    except queue.Full:
                        pass

            try:
                client = BleakClient(device)
                await client.connect()
                await client.start_notify(BLE_TX_UUID, notification_handler)
                ble_active = True
                with connection_mode.get_lock():
                    connection_mode.value = 2
                print(f"[BLE] Connected to {BLE_DEVICE_NAME}")
            except Exception as e:
                print(f"[BLE] Connection failed: {e}")
                client = None
                await asyncio.sleep(2.0)

        await asyncio.sleep(0.5)

    if client and client.is_connected:
        await client.disconnect()


# ── Process 3: Resampler + AI Pipeline + Redis Writer ────────────────────────
def pipeline(q: multiprocessing.Queue):
    """Reads frames, resamples to 50Hz, runs AI, writes to Redis."""
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)

    # Try to load Edge Impulse runner
    runner = None
    try:
        from edge_impulse_linux.runner import ImpulseRunner
        runner = ImpulseRunner(MODEL_PATH)
        model_info = runner.init()
        print(f"[AI] Model loaded: {model_info['project']['name']}")
    except Exception as e:
        print(f"[AI] Model not loaded ({e}). Gait label will be 'N/A'.")

    sample_count = 0
    fall_start: float | None = None
    fall_alerted = False

    imu_window = []  # accumulate samples for AI window

    while True:
        try:
            frame = q.get(timeout=1.0)
        except queue.Empty:
            continue

        # ── Resample: drop every N-th sample ──────────────────────────────
        sample_count += 1
        if sample_count % RESAMPLE_DROP_N == 0:
            continue

        # ── Derive G_total ─────────────────────────────────────────────────
        ax, ay, az = frame["accX"], frame["accY"], frame["accZ"]
        g_total = math.sqrt(ax**2 + ay**2 + az**2)

        # ── Fall detection: horizontal rest ───────────────────────────────
        fall_alert = 0
        if g_total < FALL_G_THRESHOLD:
            if fall_start is None:
                fall_start = time.time()
            elif time.time() - fall_start >= FALL_DURATION_S and not fall_alerted:
                fall_alert = 1
                fall_alerted = True
                print("[ALERT] Fall detected — triggering espeak-ng")
                subprocess.Popen(
                    ["espeak-ng", "Fall detected. Please check the patient."],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            fall_start = None
            fall_alerted = False

        # ── AI Gait Inference ──────────────────────────────────────────────
        gait_label = "N/A"
        if runner:
            imu_window.append([ax, ay, az])
            model_features = runner.get_input_features_count() if hasattr(runner, "get_input_features_count") else 150
            window_size = model_features // 3
            if len(imu_window) >= window_size:
                features = [v for sample in imu_window[-window_size:] for v in sample]
                try:
                    res = runner.classify({"features": features})
                    if res and "result" in res and "classification" in res["result"]:
                        classifications = res["result"]["classification"]
                        gait_label = max(classifications, key=classifications.get)
                except Exception as e:
                    print(f"[AI] Inference error: {e}")
                imu_window = imu_window[-window_size:]

        # ── Write to Redis Stream ──────────────────────────────────────────
        fields = {
            "accX":      f"{ax:.4f}",
            "accY":      f"{ay:.4f}",
            "accZ":      f"{az:.4f}",
            "gTotal":    f"{g_total:.4f}",
            "temp":      f"{frame['temp']:.2f}",
            "moisture":  str(frame["moisture"]),
            "rssi":      str(frame["rssi"]) if frame["rssi"] is not None else "N/A",
            "source":    frame["source"],
            "gaitLabel": gait_label,
            "fallAlert": str(fall_alert),
            "ts":        f"{frame['ts']:.3f}",
        }
        r.xadd(REDIS_STREAM, fields, maxlen=REDIS_MAXLEN, approximate=True)


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  SoterCare Edge Gateway  —  Starting")
    print("=" * 50)

    processes = [
        multiprocessing.Process(target=udp_listener,  args=(frame_queue, udp_last_seen), name="UDP", daemon=True),
        multiprocessing.Process(target=ble_fallback,  args=(frame_queue, udp_last_seen, ble_stop_event), name="BLE", daemon=True),
        multiprocessing.Process(target=pipeline,      args=(frame_queue,), name="Pipeline", daemon=True),
    ]

    for p in processes:
        p.start()
        print(f"[MAIN] Started process: {p.name} (pid={p.pid})")

    try:
        while True:
            time.sleep(5)
            mode = {0: "SEARCHING", 1: "WiFi", 2: "BLE"}.get(connection_mode.value, "?")
            q_size = frame_queue.qsize()
            print(f"[STATUS] Mode={mode}  Queue={q_size}")
    except KeyboardInterrupt:
        print("\n[MAIN] Shutting down...")
        ble_stop_event.set()
        for p in processes:
            p.terminate()
