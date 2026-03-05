#!/usr/bin/env python3
"""
SoterCare Dashboard — dashboard/app.py
Flask + flask-socketio server. Tails Redis Stream sotercare_history
and streams data to the browser UI via WebSocket.
"""

import threading
import time

import redis
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)
app.config["SECRET_KEY"] = "sotercare-secret"
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

REDIS_STREAM = "sotercare_history"
r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# Track current connection mode from Redis (set by gateway_master)
_last_id = "$"  # start from newest entries


def redis_tail_thread():
    """Background thread: tails Redis Stream and emits WebSocket events."""
    global _last_id
    print("[Dashboard] Redis tail thread started.")
    while True:
        try:
            results = r.xread({REDIS_STREAM: _last_id}, count=20, block=100)
            if results:
                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        _last_id = msg_id
                        socketio.emit("sensor_update", fields)
        except Exception as e:
            print(f"[Dashboard] Redis error: {e}")
            time.sleep(1)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Returns the latest connection mode and stream length."""
    try:
        length = r.xlen(REDIS_STREAM)
        latest = r.xrevrange(REDIS_STREAM, count=1)
        source = latest[0][1].get("source", "unknown") if latest else "unknown"
        return jsonify({"stream_length": length, "source": source, "status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@socketio.on("connect")
def on_connect():
    print("[Dashboard] Client connected.")


@socketio.on("disconnect")
def on_disconnect():
    print("[Dashboard] Client disconnected.")


if __name__ == "__main__":
    tail_thread = threading.Thread(target=redis_tail_thread, daemon=True)
    tail_thread.start()
    print("[Dashboard] Starting Flask-SocketIO on http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
