#!/usr/bin/env python3
"""
SoterCare — server.py
Flask-SocketIO server (threading mode — stable on Windows).
Tails Redis Stream 'sotercare_history' and streams data to the
Vite dashboard at http://localhost:5173 via WebSocket.
"""

import threading
import time

import redis as redis_lib # type: ignore
from flask import Flask, jsonify # type: ignore
from flask_socketio import SocketIO, emit # type: ignore

app = Flask(__name__)
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
        return jsonify({"stream_length": length, "source": source, "status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


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
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False)
