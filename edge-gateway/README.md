# SoterCare Edge Gateway

Raspberry Pi 5 edge gateway for the SoterCare wearable health monitoring system. Receives sensor data from the **SoterCare Thigh Node** (ESP32-S3) via Wi-Fi UDP or BLE fallback, runs an Edge Impulse gait analysis model, stores results to Redis, and serves a real-time touchscreen dashboard.

---

## Hardware Requirements

| Component        | Specification                                          |
| ---------------- | ------------------------------------------------------ |
| **Raspberry Pi** | Pi 5 (4GB or 8GB RAM recommended)                      |
| **Power Supply** | Official 27W USB-C PSU                                 |
| **Display**      | 5" DSI touchscreen (800×480) or HDMI equivalent        |
| **Networking**   | Ethernet or Wi-Fi on the same subnet as the Thigh Node |

---

## How the Thigh Node Connects

The ESP32-S3 Thigh Node firmware (`thigh-node-firmware.ino`) uses a dual-stack strategy:

| Thigh Node Mode | Data Path                                         | Pi receives                                                     |
| --------------- | ------------------------------------------------- | --------------------------------------------------------------- |
| Wi-Fi (primary) | UDP datagrams → **`192.168.1.100:1234`** at ~60Hz | 6-field CSV: `AccX,AccY,AccZ,ObjTempC,MoisturePercent,RSSI_dBm` |
| BLE (fallback)  | BLE notify on `MedNode_BLE` Nordic UART TX        | 5-field CSV: `AccX,AccY,AccZ,ObjTempC,MoisturePercent`          |

> **Critical:** The Pi **must** be assigned the static IP `192.168.1.100` on its local interface. The Thigh Node firmware hardcodes this as the UDP target.

The gateway automatically detects which transport is active. If UDP is silent for >2 seconds, the BLE fallback process activates and connects to `MedNode_BLE`. When UDP resumes, BLE is released.

---

## Setup

### 1. Assign Static IP to the Pi

```bash
# Edit /etc/dhcpcd.conf and add:
interface eth0        # or wlan0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1
sudo systemctl restart dhcpcd
```

### 2. Install System Dependencies

```bash
sudo apt update && sudo apt install -y \
  python3-pip python3-venv redis-server \
  espeak-ng bluetooth bluez libbluetooth-dev
sudo systemctl enable redis-server
```

### 3. Install Python Dependencies

```bash
cd edge-gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Place the Edge Impulse Model

Export your project as a **Linux (aarch64)** runner from Edge Impulse Dashboard → Deployment → Linux (AARCH64).  
Place the binary at:

```
edge-gateway/model/gait_model.eim
```

Make it executable:

```bash
chmod +x model/gait_model.eim
```

### 5. Tune Redis

```bash
bash scripts/tune_redis.sh
```

### 6. Run the Gateway

```bash
# Terminal 1
source .venv/bin/activate
python3 gateway_master.py

# Terminal 2
cd dashboard && python3 app.py
```

Open `http://localhost:5000` in a browser to see the live dashboard.

### 7. Set Up Kiosk Mode (optional, for Pi display)

```bash
bash scripts/setup_kiosk.sh
sudo reboot
```

The Pi will boot directly into the fullscreen dashboard.

---

## Python Dependencies (`requirements.txt`)

```
flask
flask-socketio
eventlet
redis
bleak
edge-impulse-linux
```

---

## Project Structure

```
edge-gateway/
├── gateway_master.py          # Main gateway process
├── dashboard/
│   ├── app.py                 # Flask-SocketIO dashboard server
│   └── templates/
│       └── index.html         # 800×480 real-time UI
├── model/
│   └── gait_model.eim         # Edge Impulse binary (add manually)
├── scripts/
│   ├── setup_kiosk.sh         # Chromium kiosk autostart setup
│   └── tune_redis.sh          # Redis in-memory optimisation
├── .gitignore
└── README.md
```
