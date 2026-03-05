#!/usr/bin/env python3
"""
SoterCare — server.py
Flask-SocketIO WebSocket server.
Tails Redis Stream 'sotercare_history' and pushes live data to the
Vite dashboard (http://localhost:5173) via WebSocket.
"""

import time
import redis as redis_lib
from flask import Flask, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "sotercare-secret"
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*",
                    logger=False, engineio_logger=False)

REDIS_STREAM = "sotercare_history"
r = redis_lib.Redis(host="localhost", port=6379, decode_responses=True)


def redis_tail():
    """Eventlet-compatible background task: tails Redis and emits to all clients."""
    last_id = "$"
    print("[Server] Redis tail started.")
    while True:
        try:
            results = r.xread({REDIS_STREAM: last_id}, count=10, block=200)
            if results:
                for _, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        socketio.emit("sensor_update", fields)
        except Exception as e:
            print(f"[Server] Redis error: {e}")
            socketio.sleep(1)


@app.route("/api/status")
def api_status():
    try:
        length = r.xlen(REDIS_STREAM)
        latest = r.xrevrange(REDIS_STREAM, count=1)
        source = latest[0][1].get("source", "unknown") if latest else "unknown"
        return jsonify({"stream_length": length, "source": source, "status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@socketio.on("connect")
def on_connect():
    print("[Server] Client connected — sending last frame.")
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
    socketio.start_background_task(redis_tail)
    print("[Server] Starting on http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
