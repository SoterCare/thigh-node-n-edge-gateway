# SoterCare Edge Gateway

Raspberry Pi 5 edge gateway for the SoterCare wearable health monitoring system. Receives sensor data from the **SoterCare Thigh Node** (ESP32-S3) via Wi-Fi UDP (primary) or BLE fallback, runs an Edge Impulse gait analysis model, stores results to Redis, and serves a live Vite React dashboard.

---

## How the Thigh Node Connects

The ESP32-S3 Thigh Node uses a dual-stack strategy:

| Thigh Node State | Transport                              | Gateway receives                                                       |
| ---------------- | -------------------------------------- | ---------------------------------------------------------------------- |
| Wi-Fi connected  | UDP → **`<gateway-ip>:1234`** at ~60Hz | 8-field CSV: `AccX,AccY,AccZ,ObjTempC,AmbientTempC,Moisture,%RSSI,SOS` |
| Wi-Fi lost       | BLE notify on `SoterCare_BLE` at ~60Hz | 8-field CSV: `AccX,AccY,AccZ,ObjTempC,AmbientTempC,Moisture,0,SOS`     |

> The firmware hardcodes the UDP destination IP. Update `gatewayIP` in `thigh-node-firmware.ino` line ~36 to match your gateway machine's IP before flashing.

- BLE Device Name: `SoterCare_BLE`
- BLE TX Characteristic: `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART)
- The node retries Wi-Fi every 5 seconds while on BLE fallback.
- BLE advertising stops automatically when Wi-Fi is healthy.

---

## Architecture

```
Thigh Node
   │
   ├── UDP :1234 ──────────────────────────────────┐
   └── BLE (SoterCare_BLE) ──────────────────────────┤
                                                    ▼
                                            [ PM2 Runtime ]
                                     ┌──────────────────────────┐
                                     │    gateway_master.py     │
                                     │ (Receiver, Resampler, AI)│
                                     └──────────┬───────────────┘
                                                │ Redis Stream
                                                ▼
                                     ┌──────────────────────────┐
                                     │        server.py         │
                                     │    (Flask-SocketIO)      │
                                     └──────────┬───────────────┘
                                                │ WebSocket
                                                ▼
                                     ┌──────────────────────────┐
                                     │      dashboard-ui/       │
                                     │   (Vite React Kiosk)     │
                                     └──────────────────────────┘
```

---

## Project Structure

```
edge-gateway/
├── gateway_master.py       # Data receiver, resampler, AI pipeline, Redis writer
├── server.py               # Flask-SocketIO WebSocket server
├── dashboard-ui/           # Vite React live dashboard (http://localhost:5173)
│   ├── src/App.jsx
│   ├── src/index.css
│   └── package.json
├── model/
│   └── gait_model.eim      # Edge Impulse binary — add manually
├── scripts/
│   ├── setup_kiosk.sh      # Chromium kiosk autostart (Pi)
│   └── tune_redis.sh       # Redis in-memory tuning
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Setup — Windows / Dev Machine

### 1. Redis (via WSL)

```powershell
# PowerShell (Admin):
wsl --install
```

```bash
# Ubuntu (WSL):
sudo apt update && sudo apt install -y redis-server
sudo service redis-server start
redis-cli ping   # → PONG
```

### 2. Python Dependencies

```cmd
cd edge-gateway
python -m venv .venv
.venv\Scripts\activate
pip install flask flask-socketio simple-websocket redis bleak
```

> Skip `edge-impulse-linux` on Windows — the gateway handles its absence gracefully and sets gait label to `N/A`.

### 3. Dashboard Dependencies

```cmd
cd dashboard-ui
npm install
```

### 4. Firewall — Allow UDP Port 1234

```powershell
# PowerShell (Admin):
New-NetFirewallRule -DisplayName "SoterCare UDP 1234" -Direction Inbound -Protocol UDP -LocalPort 1234 -Action Allow
```

### 5. Update Firmware Gateway IP

Find your PC's IP with `ipconfig`. Open `thigh-node-firmware.ino` line ~36 and set:

```cpp
const char* gatewayIP = "YOUR_PC_IP";
```

Re-flash the Thigh Node.

---

---

## Running (Production - PM2)

The entire gateway stack is managed by **PM2**. This ensures all services (Gateway, Server, UI) start automatically on boot and restart if they crash.

| Command                           | Purpose                                      |
| --------------------------------- | -------------------------------------------- |
| `pm2 start ecosystem.config.js`   | Start the entire SoterCare stack             |
| `pm2 status`                      | Check health of all services                 |
| `pm2 logs`                        | View live logs for all services              |
| `pm2 restart all`                 | Restart the entire stack                     |
| `pm2 stop all`                    | Stop all services                            |

### Individual Debugging (4 terminals)

If running manually for development:

| Terminal | Command                           | Purpose                          |
| -------- | --------------------------------- | -------------------------------- |
| 1 (WSL)  | `sudo service redis-server start` | Redis data store                 |
| 2        | `python gateway_master.py`        | Receive from Thigh Node → Redis  |
| 3        | `python server.py`                | WebSocket bridge → Dashboard     |
| 4        | `cd dashboard-ui && npm run dev`  | Vite UI at http://localhost:5173 |

---

## Setup — Raspberry Pi 5 (Production)

### System Dependencies

```bash
sudo apt update && sudo apt install -y \
  python3-pip python3-venv redis-server \
  espeak-ng bluetooth bluez libbluetooth-dev nodejs npm
sudo systemctl enable redis-server
```

### Assign Static IP

```bash
# /etc/dhcpcd.conf:
interface eth0
static ip_address=192.168.1.x/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1
sudo systemctl restart dhcpcd
```

### Edge Impulse Model

Export as **Linux (aarch64)** from Edge Impulse → Deployment → Linux (AARCH64).

```bash
cp gait_model.eim edge-gateway/model/
chmod +x edge-gateway/model/gait_model.eim
pip install edge-impulse-linux
```

### Redis Tuning

```bash
bash scripts/tune_redis.sh
```

### Kiosk Mode (Raspberry Pi 5 / Wayland)

The Raspberry Pi 5 uses the **Wayland** display server (`labwc`). The kiosk setup script has been updated to support both X11 and Wayland environments.

```bash
# Set boot behaviour to Desktop Autologin and configure autostart:
bash scripts/setup_kiosk.sh
sudo reboot
```

- **Flags**: Includes `--autoplay-policy=no-user-gesture-required` for medical audio alerts and `--kiosk` for full-screen.
- **Autostart**: Configures `~/.config/labwc/autostart` (Wayland) and `~/.config/autostart/sotercare-kiosk.desktop` (X11).
- **Delay**: A 10-second delay is built into the browser launch to ensure PM2 services are fully initialized.

---

## Recent Dashboard Enhancements

### 1. Medical Audio Alerts

- **Siren System:** Triggers a 2s siren for high-gravity impacts (Falls), manual **Help Calls**, and **Risky Movement** gait detection.
- **Female Voice Profile:** Uses a pleasant female voice (warmup routine included) with comfort-oriented phrasing.
- **System Silence:** Routine status updates (Online/Offline) are silent.

### 2. Monitoring & UX

- **Dual Temperature:** Real-time monitoring of both Patient Skin and Room Ambient temperatures.
- **Activity Timeline Persistence:** Recent medical events are saved to `localStorage`.
- **Terminology:** "SOS" has been standardized to **"Help Call"** across the entire UI and backend.
- **Prioritized Gait Detection:** The dashboard focuses on **"standing up"** and **"sitting down"** transitions to reduce notification fatigue. Other states like "walking" or "stillness" are processed but not alerted.

### 3. Hardware Feedback & Connectivity

- **Range Warning Vibration:** The Thigh Node triggers a sharp 150ms vibration pulse every 3 seconds if Wi-Fi signal drops below `-80 dBm` (User out of range).
- **Native BLE RSSI:** True BLE signal strength is displayed dynamically on the OLED menu and edge dashboard timeline.
