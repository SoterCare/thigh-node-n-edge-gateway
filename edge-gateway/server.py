#!/usr/bin/env python3
"""
SoterCare — server.py
Flask-SocketIO server (threading mode — stable on Windows).
Tails Redis Stream 'sotercare_history' and streams data to the
Vite dashboard at http://localhost:5173 via WebSocket.
"""

import threading
import time
import json
from flask import request # type: ignore

import redis as redis_lib # type: ignore
from flask import Flask, jsonify # type: ignore
from flask_cors import CORS # type: ignore
from flask_socketio import SocketIO, emit # type: ignore

app = Flask(__name__)
CORS(app) # Enable CORS for all API routes
app.config["SECRET_KEY"] = "sotercare-secret"

# threading mode — avoids eventlet monkey-patching issues on Windows
socketio = SocketIO(
    app,
    async_mode="threading",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

REDIS_STREAM = "sotercare_history"
r = redis_lib.Redis(host="localhost", port=6379, decode_responses=True)


def redis_tail():
    """Background thread: reads Redis Stream and emits to all connected clients."""
    last_id = "$"
    print("[Server] Redis tail thread running.")
    while True:
        try:
            results = r.xread({REDIS_STREAM: last_id}, count=10, block=300)
            if results:
                for _, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        socketio.emit("sensor_update", fields)
        except redis_lib.exceptions.ConnectionError:
            print("[Server] Redis connection lost. Retrying in 2s...")
            time.sleep(2)
        except Exception as e:
            print(f"[Server] Error: {e}")
            time.sleep(1)


@app.route("/api/status")
def api_status():
    try:
        length = r.xlen(REDIS_STREAM)
        latest = r.xrevrange(REDIS_STREAM, count=1)
        source = latest[0][1].get("source", "unknown") if latest else "none"
        
        # Optionally, get the last connected device
        last_device = None
        try:
            with open("last_device.json", "r") as f:
                last_device = json.load(f)
        except Exception:
            pass
            
        # Get the host's local IP to auto-fill dashboard configuration
        local_ip = ""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
            
        return jsonify({
            "stream_length": length, 
            "source": source, 
            "status": "ok",
            "last_device": last_device,
            "local_ip": local_ip
        })
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

@app.route("/api/scan")
def api_scan():
    """Triggers a BLE scan in gateway_master.py and waits for the result via Redis."""
    try:
        pubsub = r.pubsub()
        pubsub.subscribe("sotercare_responses")
        
        # Publish scan command
        r.publish("sotercare_commands", json.dumps({"cmd": "scan"}))
        
        # Wait for response (up to 10 seconds)
        start_time = time.time()
        while time.time() - start_time < 10.0:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if message:
                data = json.loads(message['data'])
                if data.get("cmd") == "scan_result":
                    return jsonify({"status": "ok", "devices": data.get("devices", [])})
            time.sleep(0.1)
            
        return jsonify({"status": "error", "message": "Scan timeout"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/configure", methods=["POST"])
def api_configure():
    """Triggers a BLE connection and payload write in gateway_master.py."""
    data = request.json
    if not data or not all(k in data for k in ("address", "ssid", "password", "ip")):
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
        
    try:
        pubsub = r.pubsub()
        pubsub.subscribe("sotercare_responses")
        
        req = {
            "cmd": "configure",
            "address": data["address"],
            "ssid": data["ssid"],
            "password": data["password"],
            "ip": data["ip"]
        }
        r.publish("sotercare_commands", json.dumps(req))
        
        # Wait for response (up to 15 seconds)
        start_time = time.time()
        while time.time() - start_time < 15.0:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if message:
                resp = json.loads(message['data'])
                if resp.get("cmd") == "configure_result":
                    return jsonify(resp)
            time.sleep(0.1)
            
        return jsonify({"status": "error", "message": "Configuration timeout"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Flushes previous connection data from the Edge Gateway."""
    try:
        import os
        if os.path.exists("last_device.json"):
            os.remove("last_device.json")
        return jsonify({"status": "ok", "message": "Connection data reset"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/wifi-current")
def api_wifi_current():
    """Extracts current active Wi-Fi SSID and Gateway IP."""
    import subprocess
    import socket
    try:
        # Get active wifi details
        result = subprocess.run(["sudo", "nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], capture_output=True, text=True, check=False)
        output = result.stdout
        
        ssid = ""
        for line in output.split('\n'):
            line = line.strip()
            # The format is active:ssid, e.g. "yes:SLT-Fiber-2.4G_Senon"
            if line.startswith('yes:'):
                ssidParts = line.split(':', 1)
                if len(ssidParts) > 1:
                    ssid = ssidParts[1].strip()
                break
                
        # Get local IP
        local_ip = "192.168.1.something"
        try:
            # Using a dummy socket connection to get the actual routed IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
                
        return jsonify({"status": "ok", "ssid": ssid, "ip": local_ip})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@socketio.on("connect")
def on_connect():
    print("[Server] Client connected.")
    # Send the most recent frame immediately so dashboard isn't blank
    try:
        latest = r.xrevrange(REDIS_STREAM, count=1)
        if latest:
            emit("sensor_update", latest[0][1])
    except Exception:
        pass


@socketio.on("disconnect")
def on_disconnect():
    print("[Server] Client disconnected.")


if __name__ == "__main__":
    t = threading.Thread(target=redis_tail, daemon=True)
    t.start()
    print("[Server] Starting on http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
