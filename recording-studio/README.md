# SoterCare Local Data Studio (BLE Data Forwarder)

This repository contains the **Local Data Studio**, a desktop application designed to wirelessly collect sensor data (IMU) from an ESP32 device via **Bluetooth Low Energy (BLE)** and format it for **Edge Impulse**. It consists of a Python-based GUI dashboard and a corresponding Arduino firmware sketch.

## 📂 Project Structure

- **`sotercare_local_studio.py`**: The main desktop application. It connects to the ESP32 via BLE, manages recording sessions, visualizes progress, and saves data in Edge Impulse-compatible JSON format.
- **`sampling-data-streamer-IMU-ardiuno/`**: Contains the firmware for the ESP32.
  - `sampling-data-streamer-IMU-ardiuno.ino`: The main Arduino sketch.
- **`SoterCare-icon.ico`**: Application icon file.

## 🚀 Prerequisites

### Hardware

- **ESP32 Development Board** (e.g., ESP32-S3, DOIT DEVKIT V1) equipped with an IMU (e.g., MPU6050, LSM6DS3).
- **USB Cable**: For uploading firmware and power.

### Software

- **Python 3.8+**: Ensure you have Python installed and added to your system PATH.
- **Arduino IDE**: With ESP32 Board Manager installed.
- **USB Drivers**: Ensure you have the correct drivers (CP210x or CH340) installed for your ESP32 board.

## 🛠️ Installation & Setup

### 1. Arduino Firmware Setup

1.  Navigate to the `sampling-data-streamer-IMU-ardiuno/` folder.
2.  Open `sampling-data-streamer-IMU-ardiuno.ino` in the Arduino IDE.
3.  **Install Dependencies**:
    - Go to **Sketch** -> **Include Library** -> **Manage Libraries...**
    - Search for and install **`MPU6050_light`** by _rfetick_.
    - _Note: BLE libraries are included in the ESP32 Board support package._
4.  **Upload**: Connect your ESP32 via USB, select the correct Board and Port, and click **Upload**.
5.  **Monitor**: Open the Serial Monitor (115200 baud). You should see the device initializing and advertising as "SoterCare_Studio_Node".

### 2. Python Application Setup

1.  Open your terminal or command prompt.
2.  Navigate to the project directory.
3.  **Install Required Dependencies**:
    Run the following command to install all necessary Python libraries:
    ```bash
    pip install customtkinter matplotlib bleak pyserial
    ```

---

## 🖥️ Usage Guide

### 1. Launching the App

To start the application, run the main Python script from your terminal:

```bash
python sotercare_local_studio.py
```

### 2. Startup Configuration

Once launched, a configuration dialog will appear:

1.  **Session Name**: Enter a unique name for this data collection session (e.g., "Session_01").
2.  **Select Data Save Folder**: Click the button to choose a folder where your recording files (JSON) and logs (CSV) will be saved.
3.  **Select Device**:
    - The app will automatically scan for available BLE devices.
    - Wait for the scan to complete and select your device (e.g., "SoterCare_Studio_Node") from the dropdown list.
    - If your device doesn't appear, ensure it is powered on (LED blinking Purple) and click **Refresh Devices**.
4.  **Confirm**: Click the Confirm button to proceed to the main dashboard.

### 3. Dashboard Overview

The dashboard is divided into two main sections:

#### **Left Panel: Control & Recording**

1.  **Session Configuration**:
    - Click **Config** (top right) to set up your labels.
    - **Add Label**: Enter movement names (e.g., "Walking", "Squat") and duration (e.g., 5s).
    - **Apply**: Save changes.
2.  **Contributor Details**:
    - Enter Participant Name, Age, and Sex.
    - Set the number of **Reps** (repetitions) per label.
    - Click **Start Session** to generate the recording list.
3.  **Recording Console**:
    - Use the generated buttons to record each movement.
    - **Blue Button**: Start recording a specific rep.
    - **Redo**: Re-take a specific recording if needed.

#### **Right Panel: Live Data**

- **Live Mode**: Toggle this to view real-time sensor data from the ESP32 to verify sensor placement.
- **Graphs**: Visualizes Accelerometer and Gyroscope data.

### 4. Preview Mode

The Studio features a dedicated standalone window for reviewing past recordings:
- **Launch**: Click the **Preview Mode** button located next to Live Mode.
- **Select Folder**: Browse to a previous session's directory to automatically load all stored JSON recordings into a scrollable list.
- **Navigate & View**: Use the `Up` and `Down` arrow keys or click the files in the left panel to instantly populate the Accelerometer and Gyroscope charts with the historical data.
- **Crop**: Use the Front and Back sliders to trim the data and click **Save Cropped Data** to permanently trim the file while retaining its structural integrity.
- **Delete**: Click **Delete This Recording** to permanently wipe the file from your local storage and strike it from your CSV tracking logs.

---

## 🔧 Device Status (LED Codes)

The ESP32 uses its onboard RGB LED to indicate status:

| Color      | Behavior      | Status                                              |
| :--------- | :------------ | :-------------------------------------------------- |
| **Blue**   | Blinking (3x) | **Startup**. Place device flat for calibration.     |
| **Blue**   | Solid         | **Calibrating**. Do not move device.                |
| **Green**  | Blinking (3x) | **Calibration Success**.                            |
| **Purple** | Blinking      | **Advertising**. Ready to connect via Bluetooth.    |
| **Green**  | Blinking (3x) | **Connected**. Successfully paired with Python app. |
| **Red**    | Blinking      | **Error**. Sensor initialization failed.            |
| **White**  | Solid         | **Recording**. Data is being streamed to app.       |

---

## 🔍 Troubleshooting

- **"ModuleNotFoundError"**:
  - If you see an error like `No module named 'bleak'`, run `pip install bleak` (or the missing module name).
- **Device Not Found**:
  - Ensure your PC has Bluetooth enabled.
  - Reset the ESP32 (press the RST button) and wait for the Purple blinking light.
- **Connection Failed**:
  - Close any other apps that might be using Bluetooth.
  - Restart the `sotercare_local_studio.py` script.
