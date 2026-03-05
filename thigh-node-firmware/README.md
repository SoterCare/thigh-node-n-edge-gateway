# SoterCare Thigh Node Firmware

This directory contains the firmware for the SoterCare Thigh Node, a wearable monitoring device built around an ESP32. It reads data from multiple sensors, coordinates local UI feedback, and streams data either over Wi-Fi (UDP) or Bluetooth Low Energy (BLE).

## Initial Setup: Wi-Fi Credentials

Before you can run or compile this firmware, you need to create a `env.h` file in the same directory as the `.ino` file to securely store your Wi-Fi credentials.

This file is excluded from Git to prevent accidental leaks. Create the file and add the following contents:

```cpp
#ifndef ENV_H
#define ENV_H

// --- Wi-Fi Credentials ---
const char* ssid = "YOUR_WIFI_SSID_HERE";
const char* password = "YOUR_WIFI_PASSWORD_HERE";

#endif
```

## Core Features and Functions

- **Sensing:**
  - **IMU:** MPU6050 (Accelerometer & Gyroscope)
  - **Temperature:** MLX90614 (Ambient and Object Temperature)
  - **Moisture:** Analog (percentage)
- **Connectivity:**
  - **Wi-Fi:** Sends sensor payloads via UDP to a local Gateway IP (`192.168.1.100:1234`).
  - **BLE:** Uses the Nordic UART Service format to stream data as a fallback or forced mode.
  - **Network Modes:** Auto (Wi-Fi preferred, fallback to BLE), Wi-Fi Only, BLE Only. You can switch modes via the device's OLED menu.
- **Outputs:**
  - 128x64 OLED Display (SSD1306)
  - Single RGB NeoPixel (WS2812)
  - Vibration Motor for haptic feedback
- **Inputs:**
  - UP, DOWN, ENTER (Menu navigation)
  - SOS button for immediate alert logging

## Hardware Pinout

| Component           | Pin(s)                               | Notes                      |
| :------------------ | :----------------------------------- | :------------------------- |
| **I2C Bus**         | SDA: 8, SCL: 9                       | OLED, IMU, Temp Sensor     |
| **Moisture Sensor** | A0: 4                                | Analog read only           |
| **Buttons**         | UP: 11, ENTER: 12, DOWN: 13, SOS: 14 | Configured as INPUT_PULLUP |
| **Vibration Motor** | 10                                   | Haptic feedback            |
| **RGB LED**         | 48                                   | NeoPixel                   |

## How the User Interface (Buttons & Screen) Works

The node features an interactive OLED menu driven by three main navigation buttons. The screen automatically turns off after 60 seconds of inactivity to save power.

### Button Controls

- **Any Button (When screen is off):** Wakes up the screen.
- **UP / DOWN:** Scrolls through lists and options. Haptic feedback is triggered on press.
- **ENTER (Short Press):** Selects the highlighted item or enters a sub-menu.
- **ENTER (Hold for >800ms):** Goes back to the previous menu. Holding it on the main menu turns the screen off manually.
- **SOS Button:** Immediately triggers the vibration motor, logs "SOS Triggered" to the Serial Monitor, and wakes the screen.

### Menu Structure

1. **Main Menu** (Header features graphical "Nokia-style" ascending signal bars for Wi-Fi and BLE connections)
   - **Sensor Test:** Live view of local sensors.
     - _IMU (MPU6050):_ Live feed of X, Y, Z acceleration.
     - _MLX Temp:_ Live feed of Object and Ambient temperature in Celsius.
     - _Moisture:_ Live view of raw ADC value and calculated percentage.
   - **Error Log:** Displays the 5 most recent system errors.
   - **Network Mode:** Let you override connectivity. Switch between Auto, Wi-Fi Only, and BLE Only.
   - **Monitor:** A built-in serial monitor that mirrors the last 20 `Serial.print` operations directly to the OLED. Useful for debugging without a PC connection. Use the UP/DOWN buttons to scroll through the log buffer.

## LED Indications

A single RGB NeoPixel visually signifies the device's connection status:

- **Purple:** Booting up / Initializing.
- **Green:** Connected to Wi-Fi successfully.
- **Blue:** Wi-Fi setup failed or disconnected; operating in BLE mode.

## Debugging and Error Logging

The firmware has several debugging mechanisms built in:

1. **Boot Screen Logging:**
   During a system boot, the OLED display works as a live CLI, showing the initialization status of subsystems (e.g., `IMU Sensor: OK`, `Wi-Fi: Connecting...`).
2. **Over-Serial Logging:**
   Connect the ESP32 over USB at a `115200` baud rate to see duplicate live boot status printed out.
3. **On-Device Error Log:**
   A rolling in-memory error logger records the past 5 anomalies (e.g., initialization failures, Wi-Fi dropouts). These can be directly viewed on the device by navigating to `Main Menu -> Error Log`. They denote the uptime timestamp and a short log message.
