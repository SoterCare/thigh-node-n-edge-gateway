# SoterCare Thigh Node Firmware

The firmware for the **SoterCare Thigh Node** — a wearable health monitoring device built on an ESP32-S3. It reads data from multiple onboard sensors, displays a live interactive UI on an OLED screen, and streams sensor packets to the SoterCare Edge Gateway via Wi-Fi (UDP) or Bluetooth Low Energy (BLE), automatically managing which connection to use at any given time.

---

## Initial Setup: Wi-Fi Credentials

Wi-Fi credentials are **not** stored in the main firmware file. Before compiling, create a file named `env.h` in the same directory as the `.ino` file:

```cpp
#ifndef ENV_H
#define ENV_H

// --- Wi-Fi Credentials ---
const char* ssid     = "YOUR_WIFI_SSID_HERE";
const char* password = "YOUR_WIFI_PASSWORD_HERE";

// --- Gateway Configuration ---
const char* gatewayIP = "192.168.1.11";  // Change this to your PC/Pi IP address
const int udpPort = 1234;

#endif
```

---

## How It Works

On boot, the device initialises all hardware subsystems in sequence (OLED, Temp Sensor, IMU, BLE, Wi-Fi) and displays a live boot log on the OLED. Once ready, it enters the main loop running at ~60Hz, where it concurrently:

1. **Reads sensors** — IMU, temperature, and moisture are polled on their own schedules.
2. **Manages networking** — the dual-stack engine monitors both Wi-Fi and BLE, switching automatically:
   - Wi-Fi connected → BLE advertising is **paused** (saves power).
   - Wi-Fi drops → BLE advertising starts **immediately** so the gateway can connect.
   - While on BLE → Wi-Fi reconnect is retried every **5 seconds** in the background.
3. **Transmits data** — every ~16ms a sensor payload CSV is dispatched to the active connection.
4. **Drives the UI** — the OLED redraws at 4 FPS with live signal strength bars and the interactive menu system.
5. **Handles buttons** — edge-detected button presses drive menu navigation with haptic feedback.

---

## Hardware Pinout

| Component           | Pin(s)                                     | Notes                      |
| :------------------ | :----------------------------------------- | :------------------------- |
| **I2C Bus**         | SDA: 8, SCL: 9                             | OLED, IMU, Temp Sensor     |
| **Moisture Sensor** | A0: 4                                      | Analog, smoothed 500ms avg |
| **Buttons**         | UP: 11, ENTER: 12, DOWN: 13, Help Call: 14 | `INPUT_PULLUP`             |
| **Vibration Motor** | 10                                         | Haptic feedback (PWM)      |
| **RGB LED**         | 48                                         | WS2812 NeoPixel            |

---

## Sensors & Data

### IMU — MPU6050

Reads X, Y, Z acceleration via I2C at 400kHz. Auto-calibrated on boot (device must be flat).

### Temperature — MLX90614

Reads Object (skin surface) and Ambient temperature.

> I2C clock is dynamically switched to 100kHz for each MLX read, then restored to 400kHz. This is required as the MLX90614 is an SMBus device and **cannot operate at 400kHz**.

### Moisture — Analog Sensor

Reads a raw ADC value every 100ms and calculates a 5-sample running average (effective smoothing window: 500ms). Mapped to a 0–100% range. Only the analog (A0) output is used.

---

## Connectivity & Dual-Stack Networking

### Payload Schemas

| Mode  | CSV Format                                                                |
| :---- | :------------------------------------------------------------------------ |
| Wi-Fi | `AccX,AccY,AccZ,GyroX,GyroY,GyroZ,ObjTemp,AmbTemp,Moisture,RSSI,HelpCall` |
| BLE   | `AccX,AccY,AccZ,GyroX,GyroY,GyroZ,ObjTemp,AmbTemp,Moisture,0,HelpCall`    |

### Dual-Stack AUTO Mode (Default)

The device does not wait for a connection at boot. Instead, it fires a non-blocking `WiFi.begin()` and immediately starts advertising BLE simultaneously.

The connection engine (`checkNetworkStability`) runs every cycle and enforces the following state machine:

| State                           | Behaviour                                                                 |
| :------------------------------ | :------------------------------------------------------------------------ |
| No Wi-Fi, No BLE                | LED blinks alternating **Green ↔ Blue** every 500ms while hunting         |
| BLE connects first              | LED turns **solid Blue**. Wi-Fi reconnect is silently attempted every 10s |
| Wi-Fi connects (from BLE state) | Immediately switches to Wi-Fi. LED turns **solid Green**                  |
| Wi-Fi is primary                | LED stays **solid Green**. BLE remains advertised as backup               |
| Wi-Fi drops                     | Falls back to BLE if connected. Reconnect attempts continue every 10s     |

All state transitions are printed to both the USB Serial Monitor and the on-device OLED Serial Monitor buffer.

### Wi-Fi Only / BLE Only Modes

Can be forced via the OLED **Network Mode** menu. In forced modes, the auto-reconnect hunting engine is suspended.

---

## LED Indications

| Color                   | Meaning                                                  |
| :---------------------- | :------------------------------------------------------- |
| **Purple**              | Booting / Initializing                                   |
| **Blinking Green+Blue** | Actively searching for any connection (Wi-Fi or BLE)     |
| **Solid Green**         | Connected via Wi-Fi (primary)                            |
| **Solid Blue**          | Connected via BLE only; background Wi-Fi hunt is running |

---

## OLED User Interface

The 128×64 OLED screen provides a fully interactive menu driven by three navigation buttons. The screen sleeps after **60 seconds of inactivity** and can be woken by pressing any button.

### Button Controls

| Button                | Action                                                                                                      |
| :-------------------- | :---------------------------------------------------------------------------------------------------------- |
| **Any** (screen off)  | Wakes the screen                                                                                            |
| **UP / DOWN**         | Scrolls through menu items. Edge-detected — registers exactly once per press. Haptic feedback on each press |
| **ENTER** (tap < 1s)  | Selects the highlighted item / enters a submenu                                                             |
| **ENTER** (hold ≥ 1s) | Goes back to the previous menu. On Main Menu, turns the screen off                                          |
| **Help Call**         | Fires the vibration motor, logs "Help Call Triggered" to Serial Monitor                                     |

### Main Menu Header

The top row of the Main Menu always shows:

- **Left**: `SoterCare`. When the device is operating on BLE only (no Wi-Fi), a `[BT]` badge appears in the header centre.
- **Right**: Compact Nokia-style ascending signal bars — `W` for Wi-Fi RSSI, `B` for BLE. Bars dynamically decrease as signal weakens. When an interface has **no connection**, a pixel-art `×` cross is shown instead of bars.

### Full Menu Tree

```
Main Menu
├── Sensor Test
│   ├── IMU (MPU6050)    — Live AccX, AccY, AccZ
│   ├── MLX Temp         — Live Object & Ambient °C
│   └── Moisture         — Raw ADC + Moisture %
├── Error Log            — Last 5 system errors with uptime timestamps
├── Network Mode
│   ├── AUTO             — Dual-stack automatic mode (default)
│   ├── WI-FI ONLY       — Forces Wi-Fi only
│   ├── BLE ONLY         — Forces BLE only
│   └── Test Network     — Live diagnostic: IP, RSSI dBm, Gateway IP, status
└── Monitor              — On-device serial log viewer (last 20 messages, scrollable)
```

---

## Debugging Features

### Boot Screen

During startup, the OLED shows a live CLI-style log of each subsystem being initialised (e.g., `IMU Calibrating: Done`, `Wi-Fi: Starting Discovery...`).

### USB Serial Monitor

Connect at **115200 baud**. All `systemPrint()` calls (boot status, BLE events, Wi-Fi events, Help Call triggers) are mirrored here in real time.

### On-Device Error Log (`Main Menu → Error Log`)

A rolling 5-entry in-memory log captures anomalies: sensor init failures, I2C disconnects, Wi-Fi drops. Each entry includes an uptime timestamp (`MM:SS`).

### On-Device Serial Monitor (`Main Menu → Monitor`)

A 20-entry circular buffer stores all `systemPrint` messages. Viewable on the OLED — **newest messages appear at the bottom** (like a real terminal). Press **UP** to scroll to older entries, **DOWN** to return to the latest. A `∧` indicator appears in the bottom-right corner when older entries are available above.

### OLED Hot-Plug Recovery

Every 2 seconds the firmware pings the OLED's I2C address. If the display was disconnected and is reconnected, it is automatically re-initialised without rebooting the device.

---

## Dependencies (Arduino Libraries)

| Library              | Purpose                             |
| :------------------- | :---------------------------------- |
| `Wire`               | I2C bus                             |
| `WiFi` / `WiFiUdp`   | Wi-Fi connectivity and UDP packets  |
| `BLEDevice` / `BLE*` | Bluetooth Low Energy (Kolban/ESP32) |
| `MPU6050_light`      | IMU sensor driver                   |
| `Adafruit_MLX90614`  | Temperature sensor driver           |
| `Adafruit_SSD1306`   | OLED display driver                 |
| `Adafruit_NeoPixel`  | RGB LED driver                      |
