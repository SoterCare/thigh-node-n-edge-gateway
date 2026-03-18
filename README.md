# SoterCare: Wearable Health Monitoring System

SoterCare is an end-to-end wearable system for real-time gait analysis, fall detection, and patient monitoring. It consists of a wearable node, an AI-powered edge gateway, and a data collection suite.

## Project Architecture & Pipeline

The system follows a three-stage pipeline: **Collect → Train → Deploy**.

### 1. Collect (Recording Studio)
- **Component**: [`recording-studio/`](./recording-studio/)
- **Purpose**: Collect high-fidelity IMU data from patients to train the gait analysis model.
- **Workflow**:
    1. Flash the Studio Node firmware to an ESP32-S3.
    2. Run `sotercare_local_studio.py` on a PC.
    3. Record labeled movements (walking, sitting, standing, etc.).
    4. Export data as Edge Impulse compatibles JSON.

### 2. Train (Edge Impulse)
- **Platform**: [Edge Impulse](https://edgeimpulse.com)
- **Workflow**:
    1. Upload JSON files from the Recording Studio.
    2. Design an "Impulse" (Resample 50Hz → Spectral Analysis → Classifier).
    3. Train the model (EON Compiler optimized).
    4. Export as a **Linux (AARCH64)** `.eim` binary for the Raspberry Pi 5.

### 3. Deploy (Edge Gateway)
- **Component**: [`edge-gateway/`](./edge-gateway/)
- **Purpose**: Real-time monitoring, AI inference, and live dashboard.
- **Workflow**:
    1. Flash the [Thigh Node Firmware](./thigh-node-firmware/) to the wearable.
    2. Place the `.eim` model in `edge-gateway/model/`.
    3. Run `pm2 start ecosystem.config.js` on the Raspberry Pi 5.
    4. Monitor via the full-screen Kiosk UI (`http://localhost:5173`).

## Component Map

| Folder | Description |
| :--- | :--- |
| [`edge-gateway/`](./edge-gateway/) | The production heart: Python master, Flask server, and Vite React UI. |
| [`thigh-node-firmware/`](./thigh-node-firmware/) | ESP32-S3 production code: Wi-Fi/BLE Dual-stack, OLED UI, and Haptics. |
| [`recording-studio/`](./recording-studio/) | Data collection tool for model training. |
| [`model-tester-py/`](./model-tester-py/) | Utilities for local model verification outside the gateway. |

## Quick Commands (Production)

```bash
# Start everything on the Gateway
cd edge-gateway
pm2 start ecosystem.config.js

# View live dashboard logs
pm2 logs sotercare-ui

# Setup Kiosk Mode (Pi 5)
bash scripts/setup_kiosk.sh
```

## System Features
- **Dual-Stack Connectivity**: Seamless transition between Wi-Fi (UDP) and BLE.
- **Edge AI**: Local inference for high-privacy, low-latency gait detection.
- **Medical Kiosk**: Targeted dashboard for Raspberry Pi 5 with automated full-screen boot.
- **Haptic Range Awareness**: Wearable vibrates if the patient moves out of gateway range.
- **Smart Alerts**: Siren and voice alerts for falls and critical events.
