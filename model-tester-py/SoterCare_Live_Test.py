import asyncio
import time
import threading
import numpy as np
import subprocess
import json
import re
from collections import deque
from bleak import BleakScanner, BleakClient

# ================= CONFIGURATION =================
# Hardware IDs from your env.h and .ino file
DEVICE_NAME = "D01 Prototype 1.0v SoterCare"
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Edge Impulse Window Settings
FREQUENCY_HZ = 100                 # Matches Arduino #define FREQUENCY_HZ 100
WINDOW_SIZE_MS = 2500              # 2.5 seconds
WINDOW_INCREASE_MS = 250           # 250ms sliding window step

# Calculate sample counts
WINDOW_SAMPLES = int((WINDOW_SIZE_MS / 1000) * FREQUENCY_HZ)       # 250 samples
WINDOW_STEP_SAMPLES = int((WINDOW_INCREASE_MS / 1000) * FREQUENCY_HZ) # 25 samples

# Thread-safe data buffers
data_buffer = deque(maxlen=WINDOW_SAMPLES)
buffer_lock = threading.Lock()
new_samples_count = 0
is_connected = False

# ================= BLE HANDLER =================
def ble_data_handler(sender, data):
    global new_samples_count
    try:
        # Decode the CSV string from the Arduino
        text = data.decode('utf-8').strip()
        if not text: return
        
        parts = text.split(',')
        if len(parts) == 6:
            # Parse [AccX, AccY, AccZ, GyroX, GyroY, GyroZ]
            vals = [float(x) for x in parts]
            
            with buffer_lock:
                data_buffer.append(vals)
                new_samples_count += 1
    except Exception as e:
        pass # Ignore malformed packets

async def ble_connection_loop():
    global is_connected
    print(f"Scanning for '{DEVICE_NAME}'...")
    
    while True:
        try:
            # 1. Scan for the specific SoterCare device
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=5.0)
            
            if not device:
                print("Device not found. Retrying...")
                continue
                
            print(f"Found {device.name} [{device.address}]. Connecting...")
            
            # 2. Connect and subscribe
            async with BleakClient(device, timeout=10.0) as client:
                print("Connected! Waiting for data buffer to fill...")
                is_connected = True
                
                await client.start_notify(TX_UUID, ble_data_handler)
                
                # Keep connection alive
                while client.is_connected:
                    await asyncio.sleep(1.0)
                    
        except Exception as e:
            print(f"BLE Error: {e}. Reconnecting in 2s...")
            is_connected = False
            await asyncio.sleep(2.0)

def start_ble_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_connection_loop())

import subprocess
import json

# ================= INFERENCE LOOP =================
def run_inference():
    global new_samples_count
    
    # Check if Node is installed natively before trying
    try:
        subprocess.run(["node", "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except FileNotFoundError:
        print("\n[!] CRITICAL ERROR: Node.js is not installed or not in the system PATH.")
        print("Please download and install Node.js from https://nodejs.org/ to run the WASM model on Windows.")
        return

    try:
        print(f"\nModel Configured for Edge Impulse WASM (Node.js backend)")
        print(f"Expected Window: {WINDOW_SAMPLES} samples at {FREQUENCY_HZ}Hz\n")
        
        while True:
            if not is_connected:
                time.sleep(1)
                continue
                
            with buffer_lock:
                current_len = len(data_buffer)
                ready_to_infer = (current_len == WINDOW_SAMPLES) and (new_samples_count >= WINDOW_STEP_SAMPLES)
                
                if ready_to_infer:
                    # Make a copy of the buffer for inference
                    snapshot = list(data_buffer)
                    new_samples_count = 0 # Reset step counter
            
            if ready_to_infer:
                # 2. Flatten the 2D array into a 1D list as expected by Edge Impulse
                flat_features = np.array(snapshot).flatten().tolist()
                features_str = ",".join(map(str, flat_features))
                
                # 3. Invoke the standalone Node script as a subprocess
                node_script = "sotercare-final-model-wasm-v1/node/run-impulse.js"
                
                # We use subprocess to pass the flattened features to the node script
                try:
                    result = subprocess.run(
                        ["node", node_script, features_str],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    output = result.stdout
                    # Try to parse the node script's output. Since `run-impulse.js` prints an object to console,
                    # we must extract the label arrays out of the output string.
                    
                    if "results:" in output:
                        print("-" * 40)
                        
                        # Very basic parser since node prints JS objects rather than strict JSON
                        # Example: results: [ { label: 'Idle', value: 0.99609375 }, { label: 'Squat', value: 0.00390625 } ]
                        labels_dict = {}
                        
                        import re
                        matches = re.finditer(r"label:\s*'([^']+)',\s*value:\s*([\d.]+)", output)
                        for match in matches:
                            labels_dict[match.group(1)] = float(match.group(2))
                            
                        if labels_dict:
                            best_label = max(labels_dict, key=labels_dict.get)
                            best_val = labels_dict[best_label]
                            print(f"RESULT: {best_label.upper()} ({best_val:.2f})")
                            
                            for label, value in labels_dict.items():
                                if value > 0.1 and label != best_label:
                                    print(f"  Alt: {label}: {value:.2f}")
                        
                        # Look for anomaly detection
                        anomaly_match = re.search(r"anomaly:\s*([\d.]+)", output)
                        if anomaly_match:
                            anomaly_val = float(anomaly_match.group(1))
                            if anomaly_val > 0.3:
                                print(f"  [!] ANOMALY DETECTED: {anomaly_val:.2f}")

                except subprocess.CalledProcessError as e:
                    print(f"Subprocess Error:\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}")
                
            else:
                # Prevent CPU hogging while waiting for buffer to fill
                time.sleep(0.05) 

    except Exception as e:
        print(f"Inference Engine Error: {e}")

# ================= MAIN =================
if __name__ == "__main__":
    print("Starting SoterCare Live Windows Tester...")
    
    # Start BLE in a background thread
    ble_thread = threading.Thread(target=start_ble_thread, daemon=True)
    ble_thread.start()
    
    # Run Inference in the main thread
    run_inference()