import asyncio
import collections
import subprocess
import json
import time
import os
import threading
import queue
from typing import Any, Dict, cast
import customtkinter as ctk # type: ignore
from bleak import BleakScanner, BleakClient # type: ignore

# ================= CONFIGURATION =================
BLE_DEVICE_NAME = "D01 Prototype 1.0v SoterCare"
BLE_ADDRESS_OVERRIDE = "" 
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
NODE_WRAPPER_SCRIPT = os.path.join(os.path.dirname(__file__), "run-inference.js")

# Thread-safe queue for UI updates
ui_queue: queue.Queue[Dict[str, Any]] = queue.Queue()

def get_model_info():
    ui_queue.put({'type': 'status', 'text': 'Fetching model requirements...', 'color': 'yellow'})
    try:
        result = subprocess.run(
            ["node", NODE_WRAPPER_SCRIPT, "--info"],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout.strip())
        return data.get("input_features_count", 600)
    except Exception as e:
        ui_queue.put({'type': 'error', 'text': f"Error fetching model info: {e}"})
        return 600

# ================= BLE TRHEAD =================
class InferenceAppBackend:
    def __init__(self):
        self.features_count = get_model_info()
        self.samples_per_window = self.features_count // 6
        self.stride_samples = int(self.samples_per_window * 0.5) 
        
        self.buffer = collections.deque(maxlen=self.samples_per_window)
        self.incoming_buffer = ""
        self.total_samples_received = 0
        self.last_inference_sample_count = 0
        
        ui_queue.put({'type': 'info', 'text': f"Requires {self.features_count} features.\nStride: {self.stride_samples} samples."})

    def run_inference(self, data_window):
        flat_data = [item for sublist in data_window for item in sublist]
        if len(flat_data) != self.features_count: return
            
        features_str = ",".join(map(str, flat_data))
        
        start_time = time.time()
        try:
            result = subprocess.run(
                ["node", NODE_WRAPPER_SCRIPT, features_str],
                capture_output=True, text=True, check=False
            )
            assert result.stdout is not None
            inference_time: float = (time.time() - start_time) * 1000
            
            if not result.stdout.strip():
                 ui_queue.put({'type': 'error', 'text': f"Node failed (No Output): {result.stderr}"})
                 return

            stdout_str: str = str(result.stdout).strip()
            json_start_idx: int = int(stdout_str.find('{'))
            if json_start_idx != -1:
                # Use cast to satisfy slice indexing
                json_str: str = cast(str, stdout_str[json_start_idx:])
            else:
                json_str = stdout_str
                
            out_json = json.loads(json_str)
            
            if "error" in out_json:
                ui_queue.put({'type': 'error', 'text': f"Inference Error: {out_json['error']}"})
                return

            # Find top prediction
            top_label = "UNKNOWN"
            top_val = 0.0
            
            if "results" in out_json:
                 for res in out_json["results"]:
                     if res["value"] > top_val:
                         top_val = res["value"]
                         top_label = res["label"]
            
            anomaly_score = out_json.get("anomaly", 0)
            
            ui_queue.put({
                'type': 'inference', 
                'label': top_label, 
                'confidence': top_val, 
                'anomaly': anomaly_score,
                'time': inference_time
            })
            
        except json.JSONDecodeError:
            out_raw = result.stdout if result else ""
            out_str: str = str(out_raw)
            # Use explicit index list or slice in a way that doesn't trigger "Cannot index into str"
            summary_str = "".join([out_str[i] for i in range(min(100, len(out_str)))])
            ui_queue.put({'type': 'error', 'text': f"JSON Parse Failed. Output:\n{summary_str}"})
        except Exception as e:
            ui_queue.put({'type': 'error', 'text': f"Inference Exception: {e}"})

    def handle_ble_data(self, sender, data):
        try:
            text = data.decode('utf-8', errors='ignore')
            self.incoming_buffer += text
            
            while '\n' in self.incoming_buffer:
                line, self.incoming_buffer = self.incoming_buffer.split('\n', 1)
                line = line.strip()
                if not line: continue
                
                parts = line.split(',')
                if len(parts) == 6:
                    try:
                        vals = [float(x) for x in parts]
                        self.buffer.append(vals)
                        self.total_samples_received += 1
                        
                        # Buffer ready?
                        if len(self.buffer) == self.samples_per_window:
                            if (self.total_samples_received - self.last_inference_sample_count) >= self.stride_samples:
                                self.last_inference_sample_count = self.total_samples_received
                                # Spawn inference thread to prevent blocking BLE
                                threading.Thread(target=self.run_inference, args=(list(self.buffer),)).start()
                    except ValueError: pass
        except Exception as e:
            print(f"Data handler error: {e}")

    async def main_loop(self):
        address = BLE_ADDRESS_OVERRIDE
        if not address:
            ui_queue.put({'type': 'status', 'text': f"Scanning for '{BLE_DEVICE_NAME}'...", 'color': 'yellow'})
            devices = await BleakScanner.discover(timeout=5.0)
            for d in devices:
                if d.name and BLE_DEVICE_NAME in d.name:
                    address = d.address
                    ui_queue.put({'type': 'status', 'text': f"Found {d.name} at {address}", 'color': 'yellow'})
                    break
                    
            if not address:
                 ui_queue.put({'type': 'error', 'text': f"Could not find device '{BLE_DEVICE_NAME}'"})
                 return

        ui_queue.put({'type': 'status', 'text': f"Connecting to {address}...", 'color': 'orange'})
        
        try:
            async with BleakClient(address) as client:
                ui_queue.put({'type': 'status', 'text': "Connected! Buffering...", 'color': 'green'})
                await client.start_notify(TX_UUID, self.handle_ble_data)
                
                while True:
                    await asyncio.sleep(1.0)
        except Exception as e:
            ui_queue.put({'type': 'status', 'text': f"Disconnected: {e}", 'color': 'red'})


def start_ble_background():
    backend = InferenceAppBackend()
    asyncio.run(backend.main_loop())


# ================= GUI APP =================
class InferenceApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SoterCare Real-Time Inference")
        self.geometry("450x350")
        
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        
        # Status Header
        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="yellow", font=("Arial", 14, "bold"))
        self.lbl_status.grid(row=0, column=0, padx=20, pady=(20, 5))
        
        self.lbl_info = ctk.CTkLabel(self, text="Waiting for model info...", text_color="gray", font=("Arial", 12))
        self.lbl_info.grid(row=1, column=0, padx=20, pady=0)
        
        # Inference Display (Main Action)
        self.frame_result = ctk.CTkFrame(self, fg_color="#1a1a1a", corner_radius=15)
        self.frame_result.grid(row=2, column=0, padx=20, pady=15, sticky="nsew")
        self.frame_result.grid_columnconfigure(0, weight=1)
        
        self.lbl_action = ctk.CTkLabel(self.frame_result, text="WAITING", font=("Arial", 42, "bold"), text_color="#555555")
        self.lbl_action.pack(expand=True, pady=(20, 0))
        
        self.lbl_confidence = ctk.CTkLabel(self.frame_result, text="Confidence: --%", font=("Arial", 16))
        self.lbl_confidence.pack(pady=(5, 5))
        
        self.lbl_anomaly = ctk.CTkLabel(self.frame_result, text="Anomaly: --", font=("Arial", 14), text_color="orange")
        self.lbl_anomaly.pack(pady=(0, 20))
        
        self.lbl_timing = ctk.CTkLabel(self, text="Inference Time: -- ms", font=("Arial", 10), text_color="gray")
        self.lbl_timing.grid(row=3, column=0, pady=(0, 10))
        
        # Start background thread
        threading.Thread(target=start_ble_background, daemon=True).start()
        
        # Start UI poll queue
        self.poll_queue()

    def poll_queue(self):
        try:
            while not ui_queue.empty():
                msg = ui_queue.get_nowait()
                msg_type = msg.get('type')
                
                if msg_type == 'status':
                    self.lbl_status.configure(text=msg['text'], text_color=msg.get('color', 'white'))
                elif msg_type == 'info':
                    self.lbl_info.configure(text=msg['text'])
                elif msg_type == 'error':
                    self.lbl_status.configure(text="ERROR", text_color="red")
                    self.lbl_info.configure(text=msg['text'], text_color="red")
                elif msg_type == 'inference':
                    # Update large display
                    label = str(msg['label']).upper().replace("_", " ")
                    conf = float(msg['confidence']) * 100
                    anomaly = float(msg['anomaly'])
                    timing = float(msg['time'])
                    
                    self.lbl_action.configure(text=label)
                    
                    # Colorcode based on confidence
                    if conf > 80:
                        self.lbl_action.configure(text_color="#00C853") # Green
                    elif conf > 50:
                        self.lbl_action.configure(text_color="#FFD600") # Yellow
                    else:
                        self.lbl_action.configure(text_color="#D50000") # Red
                        
                    self.lbl_confidence.configure(text=f"Confidence: {conf:.1f}%")
                    
                    if anomaly > 1.0:
                        self.lbl_anomaly.configure(text=f"Anomaly: {anomaly:.2f}", text_color="red")
                    else:
                        self.lbl_anomaly.configure(text=f"Anomaly: {anomaly:.2f}", text_color="orange")
                        
                    self.lbl_timing.configure(text=f"Inference Time: {timing:.1f} ms")
                    
        except queue.Empty:
            pass
            
        self.after(50, self.poll_queue)


if __name__ == "__main__":
    app = InferenceApp()
    app.mainloop()
