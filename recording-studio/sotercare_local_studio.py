import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import time
import json
import os
import csv
import csv
import uuid
import random
import math
import queue
from datetime import datetime
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from collections import deque
import serial
import serial.tools.list_ports
import asyncio
from bleak import BleakScanner, BleakClient

# --- Connection Abstraction ---
class ConnectionAdapter:
    """
    Manages a PERSISTENT background connection (BLE ONLY).
    Pushes received data to a thread-safe queue.
    Auto-reconnects on failure.
    """
    def __init__(self, mode, connection_info, data_queue, status_callback):
        self.mode = "Bluetooth" # Enforced
        self.info = connection_info # dict with address (port)
        self.data_queue = data_queue # queue.Queue to push raw data strings
        self.status_callback = status_callback # func(is_connected: bool)
        
        self.stop_event = threading.Event()
        self.is_connected = False
        self.thread = None
        
        # UUIDs match Arduino
        self.RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
        self.TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._background_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def _background_loop(self):
        print(f"[BLE] Background Loop Started")
        
        while not self.stop_event.is_set():
            try:
                # Always Bluetooth
                asyncio.run(self._ble_manager())
            except Exception as e:
                print(f"Connection Manager Error: {e}")
                
            # If manager returns, it means disconnected. Wait before retry.
            if not self.stop_event.is_set():
                self._update_status(False)
                time.sleep(2.0) # Reconnect delay

    def _update_status(self, connected):
        self.is_connected = connected
        if self.status_callback:
             # Schedule on main thread if possible, or callback handles it
             self.status_callback(connected)

    # --- BLE Implementation ---
    async def _ble_manager(self):
        address = self.info["port"]
        print(f"BLE Connecting to {address}...")
        
        # Stability Delay: Allow scanner/OS stack to settle before connect attempt
        await asyncio.sleep(1.0)
        
        # Outer retry loop for initial connection stability
        for attempt in range(3):
            try:
                # Context manager handles connect/disconnect
                async with BleakClient(address, timeout=10.0, disconnected_callback=self._on_ble_disconnect) as client:
                    print(f"BLE Connected! (Attempt {attempt+1})")
                    self._update_status(True)
                    
                    await client.start_notify(self.TX_UUID, self._ble_handler)
                    
                    # Connection Loop
                    while not self.stop_event.is_set() and client.is_connected:
                        await asyncio.sleep(0.5)
                        
                    await client.stop_notify(self.TX_UUID)
                    # If we exit here, likely disconnected or stopped.
                    # We should return to allow _background_loop to handle re-init if needed
                    return 
            except Exception as e:
                print(f"BLE Connect Attempt {attempt+1} Failed: {e}")
                if attempt < 2: 
                    await asyncio.sleep(1.0)
                else:
                     # If failed 3 times, propagate error to trigger _background_loop wait
                     raise e
            
    def _on_ble_disconnect(self, client):
        print("!! BLE DISCONNECTED - IMMEDIATE DETECT !!")
        self._update_status(False)
        # We don't need to do anything else, the loop in _ble_manager will exit 
        # (client.is_connected becomes False) and _background_loop will loop.

    def _ble_handler(self, sender, data):
        try:
            text = data.decode('utf-8', errors='ignore')

            self.data_queue.put(text)
        except: pass

class SoterCareLocalStudio(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- Window Setup ---
        self.title("SoterCare Local Data Studio")
        self.geometry("1300x800")
        
        try:
            self.iconbitmap(os.path.join(os.path.dirname(__file__), "SoterCare-icon.ico"))
        except Exception as e:
            print(f"Icon load warning: {e}")

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")




        self.current_participant = {}
        self.is_recording = False
        self.stop_event = threading.Event()
        
        # --- Live Mode State ---
        self.view_mode = "recording" # "recording" or "live"
        self.live_data_buffer = deque(maxlen=200) # Store last 200 points
        self.live_thread = None
        self.live_event = threading.Event()
        
        # --- State Variables ---

        self.session_name = ""
        self.connection_mode = "Bluetooth" # Enforced
        self.bt_port = "" # BLE Address
        self.session_config = {
            "movements": [],
            "frequency": 50,
            "recordings_per_label": 1,
            "sensor_mode": "Both" # Both, Accelerometer, Gyroscope
        }
        self.last_device_file = "last_device.json" # Persist config path
        self.log_filename = "" # Will be set on config confirm
        
        # --- Recording State Tracking ---
        self.current_recording_num = {}  # Track which recording number for each label
        self.recording_buttons = {}  # Store main button references for updating
        self.redo_button_frames = {}  # Store redo button container frames
        self.completed_recordings = {}  # Track which recordings are complete: {"Walking": [True, False, True]}
        self.individual_redo_buttons = {}  # Store individual redo button references: {"Walking": [btn1, btn2, btn3, btn4]}
        self.individual_view_buttons = {}  # Store individual view button references: {"Walking": [btn1, btn2, btn3, btn4]}
        
        # --- Management Flags ---
        self.connection_loop_running = False
        self.startup_dialog = None
        
        # --- Connection Stability ---
        self.consecutive_failures = 0
        self.is_checking_connection = False
        
        # --- Data Viewing State ---
        self.full_recording_data = [] # Store original data when viewing
        self.current_viewing_filepath = "" # Track current file for saving
        self.current_viewing_meta = {} # Track movement/num for UI updates
        self.cropping_mode = False

        # --- GUI Initialization ---

        # Row 0: Header (Fixed)
        # Row 1: Config Card (Fixed)
        # Row 2: Participant Card (Fixed)
        # Row 3: Recording Console (Expands)
        # Row 4: Log (Fixed)
        
        self.grid_columnconfigure(0, weight=0, minsize=450)
        self.grid_columnconfigure(1, weight=1) # Graph Column

        self.grid_rowconfigure(0, weight=0) # Header
        # Row 1: Participant
        self.grid_rowconfigure(1, weight=0) 
        # Row 2: Recording (EXPANDS)
        self.grid_rowconfigure(2, weight=1) 
        # Row 3: Log
        self.grid_rowconfigure(3, weight=0)

        # 1. Header & Setup
        self.init_header_ui()
        


        # 3. Participant Section (Card)
        self.init_participant_ui()

        # 4. Recording Dashboard (Dynamic, Expands)
        self.frame_recording_container = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_recording_container.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")
        
        # Inner scrollable frame
        self.frame_recording = ctk.CTkScrollableFrame(self.frame_recording_container, label_text="Recording Console")
        self.frame_recording.pack(fill="both", expand=True)

        # 5. Logger Console
        self.textbox_log = ctk.CTkTextbox(self, height=80, font=("Consolas", 12))
        self.textbox_log.grid(row=3, column=0, columnspan=2, padx=20, pady=(0, 20), sticky="ew")
        
        # 6. Graphs Section
        self.init_graph_ui()

        # --- Trigger Startup Dialog ---
        self.after(100, self.ask_startup_details)

        # --- Connection Manager ---
        self.data_queue = queue.Queue()
        self.connection_manager = None
        self.connected_state = False
        
        # --- Data Buffers ---
        self.incoming_buffer = "" # String buffer for packet reconstruction
        self.recording_buffer = [] # List of [val, val...]
        self.recording_active_flag = False
        self.recording_movement = ""
        self.recording_start_ts = 0
        self.recording_duration = 0

    # ================= UI BUILDERS =================

    def init_header_ui(self):
        self.frame_header = ctk.CTkFrame(self, corner_radius=10, fg_color="#1a1a1a")
        self.frame_header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        
        # Title / Brand
        lbl_title = ctk.CTkLabel(self.frame_header, text="SoterCare Studio", font=("Arial", 16, "bold"))
        lbl_title.pack(side="left", padx=15, pady=10)
        
        # Status Label
        self.lbl_status = ctk.CTkLabel(self.frame_header, text="Not Connected | No Folder", text_color="gray")
        self.lbl_status.pack(side="left", padx=10)
        
        # Right Side Actions
        self.btn_reconnect = ctk.CTkButton(self.frame_header, text="Reconnect", command=self.manual_reconnect, width=100, fg_color="orange")
        # Initially hidden/managed:
        self.btn_reconnect.pack_forget() 
        
        ctk.CTkButton(self.frame_header, text="Config", command=self.open_config_window, width=100, fg_color="#333").pack(side="right", padx=10, pady=10)


    def update_connection_status(self, is_connected):
        # Callback from Connection Thread
        self.connected_state = is_connected
        self.after(0, lambda: self.update_status_ui(is_connected))

    def start_persistent_connection(self):
        if self.connection_manager:
            self.connection_manager.stop()
            
        info = {}
        if self.connection_mode == "WiFi":
            if not self.esp_ip: return # Don't start if no IP
            info = {"ip": self.esp_ip, "port": self.esp_port}
        else:
            if not self.bt_port: return # Don't start if no BT Port
            info = {"port": self.bt_port}

        self.connection_manager = ConnectionAdapter(
            self.connection_mode,
            info,
            self.data_queue,
            self.update_connection_status
        )
        self.connection_manager.start()
        
        # UX: Show "Connecting..." immediately and hide Reconnect button
        self.lbl_status.configure(text="Connecting...", text_color="orange")
        self.btn_reconnect.pack_forget()
        
        # Start Data Processing Loop
        self.process_incoming_data()
        
    def process_incoming_data(self):
        # Run frequently (e.g. 50Hz = 20ms) to drain queue
        try:

            while not self.data_queue.empty():
                text = self.data_queue.get_nowait()

                self.incoming_buffer += text
                
                # Split lines
                while '\n' in self.incoming_buffer:
                    line, self.incoming_buffer = self.incoming_buffer.split('\n', 1)
                    line = line.strip()
                    if not line: continue
                    
                    # Parse CSV
                    try:
                        parts = line.split(',')
                        if len(parts) == 6:
                            vals = [float(x) for x in parts]
                            
                            # 1. Update Live Buffer (for graphs)
                            self.live_data_buffer.append(vals)
                            
                            # 2. Update Recording Buffer (if active)
                            if self.recording_active_flag:
                                current_ts = time.time() * 1000
                                # Check Duration
                                if (current_ts - self.recording_start_ts) < self.recording_duration:
                                    self.recording_buffer.append(vals)
                                else:
                                    self.finish_recording()
                    except: pass
                    
        except Exception as e:
             print(f"DEBUGGING ERROR in process_incoming_data: {e}")
        
        # Update Graphs if Live View is ON
        if self.view_mode == "live" and len(self.live_data_buffer) > 0:
            # We update graphs separately in a slower timer to save UI render
            pass
            
        self.after(20, self.process_incoming_data)

    def manual_reconnect(self):
        # Auto-reconnect handles this, but we can force restart
        if self.connection_manager:
            self.connection_manager.stop()
        self.start_persistent_connection()

    def perform_connection_check(self):
        pass


    def update_status_ui(self, is_connected):
        folder_text = f".../{os.path.basename(self.root_folder)}" if self.root_folder else "No Folder"
        color = "green" if is_connected else "red"
        status_text = "CONNECTED" if is_connected else "DISCONNECTED"
        
        conn_str = ""
        if self.connection_mode == "WiFi":
            conn_str = f"WiFi: {self.esp_ip}:{self.esp_port}"
        else:
            conn_str = f"BT: {self.bt_port}"

        full_text = f"Session: {self.session_name} | {conn_str} ({status_text}) | Save: {folder_text}"
        self.lbl_status.configure(text=full_text, text_color=color)

        if is_connected:
            self.btn_reconnect.pack_forget()
        else:
            self.btn_reconnect.configure(text="Reconnect", state="normal")
            self.btn_reconnect.pack(side="right", padx=10, pady=5)


    def open_config_window(self):
        if getattr(self, "config_window", None) and self.config_window.winfo_exists():
            self.config_window.lift()
            return

        self.config_window = ctk.CTkToplevel(self)
        self.config_window.title("Session Configuration")
        self.config_window.geometry("750x400")
        
        # Center the window
        self.config_window.geometry(f"+{self.winfo_x()+50}+{self.winfo_y()+50}")
        
        # Make modal-like (optional, but good for settings)
        self.config_window.transient(self)
        self.config_window.grab_set()

        # Main Content Grid inside Window
        container = ctk.CTkFrame(self.config_window, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=20, pady=20)
        
        # --- Left Side: Headers & List ---
        left_frame = ctk.CTkFrame(container, fg_color="transparent")
        left_frame.pack(side="left", fill="both", expand=True)
        
        # Headers
        header_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 3))
        ctk.CTkLabel(header_frame, text="Label", width=180, anchor="w", text_color="gray", font=("Arial", 10)).pack(side="left", padx=5)
        ctk.CTkLabel(header_frame, text="Duration (s)", width=80, text_color="gray", font=("Arial", 10)).pack(side="left", padx=5)
        
        # Freq Input
        ctk.CTkLabel(header_frame, text="Freq (Hz):", text_color="orange", font=("Arial", 10)).pack(side="left", padx=(15, 5))
        self.entry_freq = ctk.CTkEntry(header_frame, width=60, height=28)
        self.entry_freq.insert(0, str(self.session_config.get("frequency", 50)))
        self.entry_freq.pack(side="left")

        # Sensor Mode Selection
        ctk.CTkLabel(header_frame, text="Sensors:", text_color="orange", font=("Arial", 10)).pack(side="left", padx=(15, 5))
        self.combo_sensor_mode = ctk.CTkComboBox(header_frame, values=["Both", "Accelerometer", "Gyroscope"], width=110, height=28)
        self.combo_sensor_mode.set(self.session_config.get("sensor_mode", "Both"))
        self.combo_sensor_mode.pack(side="left")

        # Scrollable List
        self.frame_movements_list = ctk.CTkScrollableFrame(left_frame, fg_color="#2b2b2b")
        self.frame_movements_list.pack(fill="both", expand=True)
        
        self.movement_rows = []

        # --- Right Side: Buttons ---
        right_frame = ctk.CTkFrame(container, fg_color="transparent")
        right_frame.pack(side="right", fill="y", padx=(10, 0))

        ctk.CTkButton(right_frame, text="+ Add Label", command=lambda: self.add_movement_row(), width=120, height=30, fg_color="#444").pack(pady=2)
        ctk.CTkButton(right_frame, text="Load JSON", command=self.load_config_json, width=120, height=30).pack(pady=2)
        ctk.CTkButton(right_frame, text="Save JSON", command=self.save_config_json, width=120, height=30).pack(pady=2)
        
        def open_device_config():
            # Force Disconnect to allow re-scanning/re-pairing
            if self.connection_manager:
                self.connection_manager.stop()
            
            # Update Status
            self.update_connection_status(False)
            
            self.config_window.destroy()
            self.ask_startup_details()

        ctk.CTkButton(right_frame, text="Device Config", command=open_device_config, width=120, height=30, fg_color="#1F6AA5").pack(pady=5)

        self.btn_apply = ctk.CTkButton(right_frame, text="Apply & Close", fg_color="green", command=self.apply_config_and_close, width=120, height=30)
        self.btn_apply.pack(pady=5)


        ctk.CTkFrame(right_frame, height=2, fg_color="#555").pack(fill="x", pady=10)
        ctk.CTkButton(right_frame, text="Reset Data", fg_color="#D32F2F", hover_color="#B71C1C", width=120, command=self.reset_all_data).pack(pady=5)
        
        # Initial Population
        self.populate_config_ui()

    def populate_config_ui(self):
        # Refresh rows from config
        self.refresh_config_rows()
        # Ensure buttons are unlocked
        self.toggle_config_buttons(unlocked=True)

    def apply_config_and_close(self):
        if self.apply_config():
            self.config_window.destroy()

    def toggle_config_buttons(self, unlocked):
        if hasattr(self, "btn_apply") and self.btn_apply.winfo_exists():
            if unlocked:
                self.btn_apply.configure(state="normal", fg_color="green")
            else:
                self.btn_apply.configure(state="disabled", fg_color="gray")
        


    def add_movement_row(self, label="", duration=""):
        row_frame = ctk.CTkFrame(self.frame_movements_list, fg_color="transparent")
        row_frame.pack(fill="x", pady=2)
        
        entry_lbl = ctk.CTkEntry(row_frame, width=200)
        entry_lbl.insert(0, label)
        entry_lbl.pack(side="left", padx=5)
        
        entry_dur = ctk.CTkEntry(row_frame, width=80)
        if duration:
            entry_dur.insert(0, str(duration))
        entry_dur.pack(side="left", padx=5)
        
        # Remove button
        def remove_me():
            # Allow removing even if it's the last one, to clear it
            row_frame.destroy()
            if widgets in self.movement_rows:
                self.movement_rows.remove(widgets)
        
        btn_rem = ctk.CTkButton(row_frame, text="X", width=30, fg_color="red", command=remove_me)
        btn_rem.pack(side="left", padx=5)

        widgets = {"frame": row_frame, "label": entry_lbl, "duration": entry_dur, "btn_rem": btn_rem}
        self.movement_rows.append(widgets)

    def refresh_config_rows(self):
        # Clear existing
        for widget in self.movement_rows:
            widget["frame"].destroy()
        self.movement_rows = []
        
        # Populate from session_config
        if not self.session_config["movements"]:
             self.add_movement_row("", "")
        else:
            for item in self.session_config["movements"]:
                self.add_movement_row(item.get("label", ""), item.get("duration_sec", ""))

    def init_participant_ui(self):
        # Card Container
        self.frame_part = ctk.CTkFrame(self, corner_radius=10, border_width=1, border_color="#333")
        self.frame_part.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        # Header
        frame_head = ctk.CTkFrame(self.frame_part, fg_color="transparent", height=30)
        frame_head.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frame_head, text="CONTRIBUTOR DETAILS", font=("Arial", 12, "bold"), text_color="#aaa").pack(side="left")
        
        self.lbl_session_id = ctk.CTkLabel(frame_head, text="", font=("Arial", 12, "bold"), text_color="#1F6AA5")
        self.lbl_session_id.pack(side="right")

        # Inputs Container
        container = ctk.CTkFrame(self.frame_part, fg_color="transparent")
        container.pack(fill="x", padx=10, pady=10)

        # Row 1: Inputs
        ctk.CTkLabel(container, text="Name:").grid(row=0, column=0, padx=10, sticky="e")
        self.entry_name = ctk.CTkEntry(container, width=200)
        self.entry_name.grid(row=0, column=1, padx=5, sticky="w")

        ctk.CTkLabel(container, text="Age:").grid(row=0, column=2, padx=10, sticky="e")
        self.entry_age = ctk.CTkEntry(container, width=60)
        self.entry_age.grid(row=0, column=3, padx=5, sticky="w")

        ctk.CTkLabel(container, text="Sex:").grid(row=0, column=4, padx=10, sticky="e")
        self.combo_sex = ctk.CTkComboBox(container, values=["Male", "Female", "Other"], width=100)
        self.combo_sex.grid(row=0, column=5, padx=5, sticky="w")

        ctk.CTkLabel(container, text="Reps:").grid(row=0, column=6, padx=10, sticky="e")
        self.entry_rec_per_label_main = ctk.CTkEntry(container, width=50)
        self.entry_rec_per_label_main.insert(0, str(self.session_config.get("recordings_per_label", 1)))
        self.entry_rec_per_label_main.grid(row=0, column=7, padx=5, sticky="w")

        # Row 2: Buttons
        btn_frame = ctk.CTkFrame(self.frame_part, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 10))
        
        self.btn_start_session = ctk.CTkButton(btn_frame, text="Start Session", command=self.start_session, fg_color="#1F6AA5")
        self.btn_start_session.pack(side="left", fill="x", expand=True, padx=(0, 5))



        ctk.CTkButton(btn_frame, text="Clear", fg_color="gray", width=80, hover_color="#555555", command=self.clear_participant_data).pack(side="right")


    def reset_all_data(self):
        """Reset ALL data for the current session: Delete JSONs, Clear Log Content (Keep Headers)"""
        if not self.root_folder:
             return

        confirm = messagebox.askyesno("Confirm Reset", 
                                      f"Are you sure you want to CLEAR ALL DATA in '{os.path.basename(self.root_folder)}'?\n\n"
                                      "This will:\n"
                                      "1. Delete ALL .json recordings\n"
                                      "2. Clear ALL entries in the log file (Headers kept)\n\n"
                                      "This action cannot be undone.")
        if not confirm:
            return

        self.log("--- Resetting ALL Session Data ---")

        # 1. Delete JSON Files
        deleted_count = 0
        try:
            for root, dirs, files in os.walk(self.root_folder):
                for file in files:
                    if file.endswith(".json"):
                        file_path = os.path.join(root, file)
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                        except: pass
            self.log(f"Deleted {deleted_count} JSON files.")
        except Exception as e:
            self.log(f"Error scanning files: {e}")

        # 2. Clear Log File (Keep Headers)
        if hasattr(self, 'log_filename') and self.log_filename:
            log_path = os.path.join(self.root_folder, self.log_filename)
            if os.path.exists(log_path):
                try:
                    # Read headers first
                    headers = []
                    with open(log_path, 'r', newline='') as f:
                        reader = csv.reader(f)
                        try:
                            headers = next(reader)
                        except: pass
                    
                    # Rewrite file with ONLY headers
                    if headers:
                        with open(log_path, 'w', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow(headers)
                        self.log("Log file cleared (headers preserved).")
                    else:
                        self.log("Log file was empty or invalid.")
                        
                except Exception as e:
                    self.log(f"Error clearing log: {e}")
        else:
            self.log("Log file not initialized yet.")

        # 3. Reset UI (If a session was active)
        self.clear_participant_data()
        self.log("All data reset complete.")


    def clear_participant_data(self):
        self.entry_name.delete(0, "end")
        self.entry_age.delete(0, "end")
        self.combo_sex.set("Male") # Reset to default
        
        # Clear recording buttons
        for widget in self.frame_recording.winfo_children():
            widget.destroy()
        
        self.current_participant = {}
        self.lbl_session_id.configure(text="")
        self.log("Session cleared. Ready for new participant.")

    def init_graph_ui(self):
        # Frame for Graphs
        self.frame_graphs = ctk.CTkFrame(self, corner_radius=10, fg_color="#1a1a1a")
        self.frame_graphs.grid(row=0, column=1, rowspan=4, padx=(0, 20), pady=20, sticky="nsew")
        
        # Header for Graphs (Title + Mode Switch)
        graph_header = ctk.CTkFrame(self.frame_graphs, fg_color="transparent")
        graph_header.pack(fill="x", padx=10, pady=10)

        # Title
        ctk.CTkLabel(graph_header, text="Live Data Visualization", font=("Arial", 16, "bold")).pack(side="left")
        
        # Live Mode Button
        self.btn_live_mode = ctk.CTkButton(
            graph_header, 
            text="Live Mode", 
            fg_color="#00C853", 
            width=100, 
            command=self.toggle_view_mode
        )
        self.btn_live_mode.pack(side="right")

        # Cropping Mode Button (Right of Live Mode)
        self.btn_crop_mode = ctk.CTkButton(
            graph_header,
            text="Cropping Mode",
            fg_color="#333",
            width=100,
            command=self.toggle_crop_mode,
            state="disabled" # Enabled only when data is loaded
        )
        self.btn_crop_mode.pack(side="right", padx=10)
        
        # Preview Mode Button
        self.btn_preview_mode = ctk.CTkButton(
            graph_header,
            text="Preview Mode",
            fg_color="#1F6AA5",
            width=100,
            command=self.open_preview_window
        )
        self.btn_preview_mode.pack(side="right", padx=10)
        
        # Matplotlib Figure
        # Dark theme colors: Face #1a1a1a, Text white
        # Reduced height from 8 to 5 to allow room for controls
        self.fig = Figure(figsize=(5, 5), dpi=100, facecolor="#1a1a1a")
        
        # 1. Accelerometer
        self.ax1 = self.fig.add_subplot(211)
        self.ax1.set_facecolor("#2b2b2b")
        self.ax1.set_title("Accelerometer (g)", color="white", fontsize=10)
        self.ax1.tick_params(axis='x', colors='white')
        self.ax1.tick_params(axis='y', colors='white')
        
        # 2. Gyroscope
        self.ax2 = self.fig.add_subplot(212)
        self.ax2.set_facecolor("#2b2b2b")
        self.ax2.set_title("Gyroscope (deg/s)", color="white", fontsize=10)
        self.ax2.tick_params(axis='x', colors='white')
        self.ax2.tick_params(axis='y', colors='white')
        
        self.fig.tight_layout()

        # Canvas
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_graphs)
        self.canvas.draw()

        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)
        
        # Crop Controls (Initially Hidden)
        self.frame_crop_controls = ctk.CTkFrame(self.frame_graphs, fg_color="#2b2b2b", height=0)
        
        # Grid layout for controls
        self.frame_crop_controls.columnconfigure(0, weight=1)
        self.frame_crop_controls.columnconfigure(1, weight=1)
        
        # Left Side (Front Crop)
        self.lbl_crop_start = ctk.CTkLabel(self.frame_crop_controls, text="Crop Front: 0ms", font=("Arial", 10))
        self.lbl_crop_start.grid(row=0, column=0, padx=10, pady=(5,0), sticky="w")
        
        self.slider_crop_start = ctk.CTkSlider(
            self.frame_crop_controls, 
            from_=0, to=100, 
            command=self.on_crop_change,
            number_of_steps=100
        )
        self.slider_crop_start.set(0)
        self.slider_crop_start.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        
        # Right Side (Back Crop)
        self.lbl_crop_end = ctk.CTkLabel(self.frame_crop_controls, text="Crop Back: MAX", font=("Arial", 10))
        self.lbl_crop_end.grid(row=0, column=1, padx=10, pady=(5,0), sticky="e")
        
        self.slider_crop_end = ctk.CTkSlider(
            self.frame_crop_controls, 
            from_=0, to=100, 
            command=self.on_crop_change,
            number_of_steps=100
        )
        self.slider_crop_end.set(100) # Default to MAX (Full End)
        self.slider_crop_end.grid(row=1, column=1, padx=10, pady=5, sticky="ew")

        # Save Button (Spans both)
        self.btn_save_crop = ctk.CTkButton(
            self.frame_crop_controls,
            text="Save Cropped Data",
            fg_color="#D32F2F", 
            hover_color="#B71C1C",
            command=self.save_cropped_data,
            width=200
        )
        self.btn_save_crop.grid(row=2, column=0, columnspan=2, pady=10)
        
        # Real-time duration label (below save button)
        self.lbl_crop_duration = ctk.CTkLabel(
            self.frame_crop_controls, 
            text="Final Length: 0ms", 
            font=("Arial", 12, "bold"), 
            text_color="#00C853"
        )
        self.lbl_crop_duration.grid(row=3, column=0, columnspan=2, pady=(0, 5))
        
        # Initial visibility update
        self.update_graph_visibility()

    def update_graph_visibility(self):
        """Updates graph titles/state based on current config mode (without data)"""
        mode = self.session_config.get("sensor_mode", "Both")
        
        # Accel
        self.ax1.clear()
        self.ax1.set_facecolor("#2b2b2b")
        self.ax1.set_xticks([])
        self.ax1.tick_params(axis='y', colors='#aaa')
        self.ax1.set_ylim(-2.0, 2.0)
        
        if mode == "Gyroscope":
             self.ax1.set_title("Accelerometer (Disabled)", color="#777", fontsize=10)
             self.ax1.text(0.5, 0.5, "N/A", color="#555", ha='center', va='center', transform=self.ax1.transAxes)
        else:
             self.ax1.set_title("Accelerometer (g)", color="white", fontsize=10)
             self.ax1.grid(True, color="#444", linestyle='--', linewidth=0.5)

        # Gyro
        self.ax2.clear()
        self.ax2.set_facecolor("#2b2b2b")
        self.ax2.set_xticks([])
        self.ax2.tick_params(axis='y', colors='#aaa')
        self.ax2.set_ylim(-400, 400)
        self.ax2.set_xlabel("Time (ms)", color="#aaa") # Only bottom graph needs label if enabled

        if mode == "Accelerometer":
             self.ax2.set_title("Gyroscope (Disabled)", color="#777", fontsize=10)
             self.ax2.text(0.5, 0.5, "N/A", color="#555", ha='center', va='center', transform=self.ax2.transAxes)
        else:
             self.ax2.set_title("Gyroscope (deg/s)", color="white", fontsize=10)
             self.ax2.grid(True, color="#444", linestyle='--', linewidth=0.5)
             
        self.canvas.draw()

    def update_graphs(self, movement_name, data):
        """Update graphs with recorded data - Delegates to render_graph_data for consistency"""
        self.render_graph_data(data, title_prefix=f"{movement_name}")

    def toggle_view_mode(self):
        if self.view_mode == "recording":
            # Switch to LIVE
            self.view_mode = "live"
            self.btn_live_mode.configure(text="Recording Mode", fg_color="#D32F2F") # Red to go back
            self.set_recording_console_state("disabled")
            
            self.live_data_buffer.clear()
            # No thread needed. process_incoming_data populates buffer, update_live_graph_ui reads it.
            
            # Start GUI Update Loop
            self.update_live_graph_ui()
            
        else:
            # Switch to RECORDING
            self.view_mode = "recording"
            self.btn_live_mode.configure(text="Live Mode", fg_color="#00C853") # Green to go live
            self.set_recording_console_state("normal")
            
            # (GUI loop will stop self-scheduling when mode changes)

    def set_recording_console_state(self, state):
        if state == "disabled":
            # Create overlay
            self.overlay = ctk.CTkFrame(self.frame_recording_container, fg_color="#333333")
            self.overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            
            lbl = ctk.CTkLabel(self.overlay, text="Live Mode Active", font=("Arial", 20, "bold"), text_color="orange")
            lbl.place(relx=0.5, rely=0.4, anchor="center")
            
            sub = ctk.CTkLabel(self.overlay, text="Go to Recording mode to use", font=("Arial", 12))
            sub.place(relx=0.5, rely=0.5, anchor="center")
            
            # Raise overlay to block clicks
            self.overlay.lift()
            
        else:
            if hasattr(self, 'overlay') and self.overlay.winfo_exists():
                self.overlay.destroy()

    def run_live_thread(self):
        pass

    def render_graph_data(self, data, title_prefix="Live"):
        """Helper to render data on the canvas"""
        if not data: return

        # Determine mode based on column count
        # 3 columns = Accel ONLY OR Gyro ONLY (Need to check config or context, but usually viewing matches config)
        # 6 columns = Both
        
        cols = len(data[0])
        
        ax_data, ay_data, az_data = [], [], []
        gx_data, gy_data, gz_data = [], [], []
        
        has_accel = False
        has_gyro = False
        
        if cols == 6:
            # Data from device always has 6 columns. 
            # We MUST check config to decide what to actually SHOW.
            mode = self.session_config.get("sensor_mode", "Both")
            
            # Extract basic data
            # (We extract everything first, then decide whether to show)
            ax_data = [d[0] for d in data]
            ay_data = [d[1] for d in data]
            az_data = [d[2] for d in data]
            gx_data = [d[3] for d in data]
            gy_data = [d[4] for d in data]
            gz_data = [d[5] for d in data]
            
            # Apply Config Logic
            if mode == "Accelerometer":
                has_accel = True
                has_gyro = False
            elif mode == "Gyroscope":
                has_accel = False
                has_gyro = True
            else: # Both
                has_accel = True
                has_gyro = True
            
        elif cols == 3:
            # Viewing a saved file that was already filtered
            # Use current session config to match mode, or rely on file context if we had it.
            # Assuming current config matches file intent for simple viewer logic.
            mode = self.session_config.get("sensor_mode", "Both")
            
            if mode == "Gyroscope":
                has_gyro = True
                gx_data = [d[0] for d in data]
                gy_data = [d[1] for d in data]
                gz_data = [d[2] for d in data]
            else:
                # Default to Accel if "Accelerometer" or Ambiguous
                # (Note: Saving "Both" produces 6 cols, so 3 cols means specific mode)
                has_accel = True
                ax_data = [d[0] for d in data]
                ay_data = [d[1] for d in data]
                az_data = [d[2] for d in data]

        # Times (Fake times relative to buffer len)
        times = range(len(data))
        
        # --- Plot Accel ---
        self.ax1.clear()
        self.ax1.set_facecolor("#2b2b2b")
        if has_accel:
            self.ax1.set_title(f"{title_prefix} Accelerometer (g)", color="white", fontsize=10)
            self.ax1.plot(times, ax_data, label='X', color='#FF5252', linewidth=1)
            self.ax1.plot(times, ay_data, label='Y', color='#448AFF', linewidth=1)
            self.ax1.plot(times, az_data, label='Z', color='#69F0AE', linewidth=1)
            self.ax1.grid(True, color="#444", linestyle='--', linewidth=0.5)
            self.ax1.legend(loc='upper right', facecolor="#333", edgecolor="white", labelcolor="white", fontsize=8)
        else:
            self.ax1.set_title(f"{title_prefix} Accelerometer (Disabled)", color="#777", fontsize=10)
            self.ax1.text(0.5, 0.5, "N/A", color="#555", ha='center', va='center', transform=self.ax1.transAxes)
            
        self.ax1.set_xticks([]) # Hide X for cleanliness
        self.ax1.tick_params(axis='y', colors='#aaa')
        self.ax1.set_ylim(-2.0, 2.0)

        # --- Plot Gyro ---
        self.ax2.clear()
        self.ax2.set_facecolor("#2b2b2b")
        if has_gyro:
            self.ax2.set_title(f"{title_prefix} Gyroscope", color="white", fontsize=10)
            self.ax2.plot(times, gx_data, label='X', color='#FF5252', linewidth=1)
            self.ax2.plot(times, gy_data, label='Y', color='#448AFF', linewidth=1)
            self.ax2.plot(times, gz_data, label='Z', color='#69F0AE', linewidth=1)
            self.ax2.grid(True, color="#444", linestyle='--', linewidth=0.5)
            self.ax2.legend(loc='upper right', facecolor="#333", edgecolor="white", labelcolor="white", fontsize=8)
        else:
            self.ax2.set_title(f"{title_prefix} Gyroscope (Disabled)", color="#777", fontsize=10)
            self.ax2.text(0.5, 0.5, "N/A", color="#555", ha='center', va='center', transform=self.ax2.transAxes)

        self.ax2.set_xticks([])
        self.ax2.tick_params(axis='y', colors='#aaa')
        self.ax2.set_ylim(-400, 400)
        
        self.canvas.draw()

    def update_live_graph_ui(self):
        if self.view_mode != "live":
            return

        try:
            if len(self.live_data_buffer) > 0:
                self.render_graph_data(list(self.live_data_buffer), "Live")
        except Exception as e:
            print(f"Graph Render Error: {e}")
        
        # Schedule next update
        if self.view_mode == "live":
            self.after(50, self.update_live_graph_ui)


    # ================= LOGIC FUNCTIONS =================

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.textbox_log.insert("end", f"[{timestamp}] {msg}\n")
        self.textbox_log.see("end")

    def ask_startup_details(self):
        if hasattr(self, 'startup_dialog') and self.startup_dialog is not None and self.startup_dialog.winfo_exists():
            self.startup_dialog.lift()
            return

        # Dialog to ask Session Name, Folder, and Select Device
        dialog = ctk.CTkToplevel(self)
        self.startup_dialog = dialog 
        dialog.title("Device Configuration")
        dialog.geometry("400x500")
        
        try:
            dialog.iconbitmap(os.path.join(os.path.dirname(__file__), "SoterCare-icon.ico"))
        except: pass

        dialog.attributes("-topmost", True)

        # --- Session Name ---
        ctk.CTkLabel(dialog, text="Session Name:").pack(pady=(10,5))
        session_entry = ctk.CTkEntry(dialog)
        session_entry.pack(pady=5)

        # --- Folder Selection ---
        lbl_folder = ctk.CTkLabel(dialog, text="No folder selected", text_color="orange")
        lbl_folder.pack(pady=5)

        def sel_folder():
            path = filedialog.askdirectory()
            if path:
                self.root_folder = path
                self.backup_folder = os.path.join(self.root_folder, "BACKUP")
                os.makedirs(self.backup_folder, exist_ok=True)
                lbl_folder.configure(text=f".../{os.path.basename(path)}", text_color="green")

        ctk.CTkButton(dialog, text="Select Data Save Folder", command=sel_folder).pack(pady=5)

        # --- Device Selection (BLE Only) ---
        ctk.CTkLabel(dialog, text="Select Device:").pack(pady=(15, 5))
        
        # Scanned Ports Combo
        combo_ports = ctk.CTkComboBox(dialog, values=["Scanning..."], width=250)
        combo_ports.pack(pady=5)

        # Scanning Logic (Nested to access combo_ports easily)
        def refresh_list(auto_select_addr=None, saved_name=None):
            combo_ports.set("Scanning...")
            # Run scan in thread
            def scan_thread():
                port_list = []
                # Add saved immediately if exists
                if auto_select_addr:
                    item = f"{saved_name} (Saved) - {auto_select_addr}"
                    port_list.append(item)

                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    devices = loop.run_until_complete(BleakScanner.discover(timeout=3.0))
                    loop.close()
                    
                    for d in devices:
                        name = d.name or "Unknown"
                        if "SoterCare" in name or (auto_select_addr and d.address == auto_select_addr):
                             item = f"{name} - {d.address}"
                             if not any(d.address in p for p in port_list):
                                 port_list.append(item)
                except Exception as e:
                    print(f"Scan Error: {e}")

                def update_ui():
                    if not combo_ports.winfo_exists():
                        return
                    if port_list:
                        combo_ports.configure(values=port_list)
                        if auto_select_addr:
                             # Try select match
                             for p in port_list:
                                 if auto_select_addr in p:
                                     combo_ports.set(p)
                                     break
                        else:
                             combo_ports.set(port_list[0])
                    else:
                        combo_ports.configure(values=["No devices found"])
                        combo_ports.set("No devices found")
                
                self.after(0, update_ui)
            
            threading.Thread(target=scan_thread, daemon=True).start()

        ctk.CTkButton(dialog, text="Refresh Devices", command=refresh_list, width=120, fg_color="#444").pack(pady=5)

        # --- Confirm Logic ---
        def confirm():
            s_name = session_entry.get().strip()
            if not s_name:
                messagebox.showerror("Error", "Please enter a Session Name.")
                return
            if not self.root_folder:
                messagebox.showerror("Error", "Please select a Save Folder.")
                return

            val = combo_ports.get()
            if not val or val == "Scanning..." or val == "No devices found":
                messagebox.showerror("Error", "Please select a valid device.")
                return
            
            self.session_name = s_name
            self.lbl_session_id.configure(text=f"ID: {self.session_name}")
            
            # Extract Address
            if " - " in val:
                self.bt_port = val.rsplit(" - ", 1)[-1].strip()
            else:
                self.bt_port = val.strip()

            # Save Config
            try:
                device_data = {
                    "last_address": self.bt_port,
                    "last_name": val.rsplit(" - ", 1)[0].strip() if " - " in val else "Unknown",
                    "last_session": self.session_name,
                    "last_folder": self.root_folder
                }
                with open(self.last_device_file, "w") as f:
                    json.dump(device_data, f)
            except: pass

            # Generate Log Filename: [DDMMYYYY][SessionName]_participant_log.csv
            date_str = datetime.now().strftime("%d%m%Y")
            self.log_filename = f"{date_str}_{self.session_name}_participant_log.csv"

            # Init Log File
            try:
                self.init_log_file()
            except Exception as e:
                print(f"Log Init Error: {e}")

            dialog.destroy()
            
            # Connect
            self.after(100, self.start_persistent_connection)

        ctk.CTkButton(dialog, text="Confirm & Connect", command=confirm, fg_color="green").pack(pady=20)

        # --- Auto-Load Last Config ---
        if os.path.exists(self.last_device_file):
            try:
                with open(self.last_device_file, "r") as f:
                    data = json.load(f)
                    if "last_session" in data: session_entry.insert(0, data["last_session"])
                    if "last_folder" in data and os.path.exists(data["last_folder"]):
                        self.root_folder = data["last_folder"]
                        self.backup_folder = os.path.join(self.root_folder, "BACKUP")
                        lbl_folder.configure(text=f".../{os.path.basename(self.root_folder)}", text_color="green")
                    
                    last_addr = data.get("last_address", "")
                    last_name = data.get("last_name", "Saved Device")
                    refresh_list(last_addr, last_name)
                    return
            except: pass
        
        # Default if no config
        refresh_list()

    def init_log_file(self):
        # Creates the master csv log if it doesn't exist
        log_path = os.path.join(self.root_folder, self.log_filename)
        if not os.path.exists(log_path):
            with open(log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Record_ID", "Name", "Age", "Sex", "Movement", "Duration_ms", "Freq_Hz", "File_Path", "Redo_Count", "Cropped_Duration_ms"])
            self.log(f"Created master log at {log_path}")

    # --- Config Import/Export ---
    def apply_config(self):
        new_movements = []
        for row in self.movement_rows:
            lbl = row["label"].get().strip()
            dur_str = row["duration"].get().strip()
            
            if lbl:
                try:
                    dur_val = float(dur_str) if dur_str else 5.0
                except:
                    dur_val = 5.0
                new_movements.append({"label": lbl, "duration_sec": dur_val})
        
        # Get frequency
        try:
            freq = int(self.entry_freq.get())
            if freq <= 0:
                raise ValueError()
        except:
            messagebox.showerror("Error", "Please enter a valid frequency (Hz)")
            return False
        
        self.session_config["movements"] = new_movements
        self.session_config["frequency"] = freq
        self.session_config["sensor_mode"] = self.combo_sensor_mode.get()
        # self.session_config["recordings_per_label"] -> Managed in Main UI on Start
        
        self.log(f"Config Applied: {len(new_movements)} movements, {freq}Hz")
        
        self.update_graph_visibility() # Update graphs immediately
        
        # Lock UI
        for row in self.movement_rows:
            row["label"].configure(state="disabled")
            row["duration"].configure(state="disabled")
            row["btn_rem"].configure(state="disabled")
        
        self.entry_freq.configure(state="disabled")
        self.combo_sensor_mode.configure(state="disabled")
            
        self.toggle_config_buttons(unlocked=False)
        return True


    def save_config_json(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            # Refresh config from UI first (optional, or rely on 'Apply' being pressed)
            # We'll just save what's currently in session_config which assumes 'Apply' was pressed,
            # OR we can grab from UI to be safe. Let's grab from UI.
            
            temp_movements = []
            for row in self.movement_rows:
                lbl = row["label"].get().strip()
                dur_str = row["duration"].get().strip()
                if lbl:
                    try:
                        dur_val = float(dur_str)
                    except: dur_val = 5.0
                    temp_movements.append({"label": lbl, "duration_sec": dur_val})
            
            out_config = {
                "movements": temp_movements,
                "frequency": self.session_config.get("frequency", 100),
                "recordings_per_label": self.session_config.get("recordings_per_label", 1),
                "sensor_mode": self.session_config.get("sensor_mode", "Both")
            }

            with open(path, 'w') as f:
                json.dump(out_config, f, indent=4)
            self.log(f"Config saved to {os.path.basename(path)}")

    def load_config_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            with open(path, 'r') as f:
                data = json.load(f)
                
                # Support legacy format migration if needed (list of strings)
                if data.get("movements") and isinstance(data["movements"][0], str):
                    converted = [{"label": m, "duration_sec": 5} for m in data["movements"]]
                    data["movements"] = converted

                self.session_config = data
                
                # Update UI fields
                if "frequency" in data:
                    self.entry_freq.configure(state="normal")
                    self.entry_freq.delete(0, "end")
                    self.entry_freq.insert(0, str(data["frequency"]))
                
                if "recordings_per_label" in data:
                    if hasattr(self, 'entry_rec_per_label_main'):
                        self.entry_rec_per_label_main.delete(0, "end")
                        self.entry_rec_per_label_main.insert(0, str(data.get("recordings_per_label", 1)))
                
                if "sensor_mode" in data and hasattr(self, 'combo_sensor_mode'):
                    self.combo_sensor_mode.set(data["sensor_mode"])
                
                self.refresh_config_rows()
                self.update_graph_visibility() # Update graphs on load
                self.log("Configuration Loaded.")

    # --- Session Management ---
    def start_session(self):
        name = self.entry_name.get()
        if not name or not self.session_config["movements"]:
            messagebox.showerror("Error", "Enter Name and Ensure Config is Applied")
            return

        # Update Recordings Per Label from main input
        try:
            rpl = int(self.entry_rec_per_label_main.get())
            if rpl <= 0: raise ValueError()
            self.session_config["recordings_per_label"] = rpl
        except:
            messagebox.showerror("Error", "Invalid Recordings/Label Value")
            return

        # Generate Session ID
        name_part = name[:2].replace(" ", "")
        age_part = self.entry_age.get()
        sex_val = self.combo_sex.get()
        sex_part = sex_val[0].lower() if sex_val else "x"
        rand_part = random.randint(100, 999)
        session_id = f"{name_part}{age_part}{sex_part}{rand_part}"
        
        self.lbl_session_id.configure(text=f"ID: {session_id}")

        self.current_participant = {
            "name": name,
            "age": age_part,
            "sex": sex_val,
            "record_id": session_id
        }
        
        # Initialize recording tracking
        self.current_recording_num = {}
        self.recording_buttons = {}
        self.redo_button_frames = {}
        self.completed_recordings = {}
        self.individual_redo_buttons = {}
        self.individual_view_buttons = {}
        
        # Clear previous buttons
        for widget in self.frame_recording.winfo_children():
            widget.destroy()

        # Generate Buttons for each Movement
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        
        for mov_data in self.session_config["movements"]:
            label = mov_data["label"]
            dur = mov_data.get("duration_sec", 5)
            
            # Initialize tracking for this label
            self.current_recording_num[label] = 1
            self.completed_recordings[label] = [False] * recordings_per_label
            self.individual_redo_buttons[label] = []
            self.individual_view_buttons[label] = []
            
            # Create container frame for this label
            container = ctk.CTkFrame(self.frame_recording, fg_color="#1a1a1a", corner_radius=8)
            container.pack(pady=8, fill="x", padx=5)
            
            # Header section
            header_frame = ctk.CTkFrame(container, fg_color="transparent")
            header_frame.pack(fill="x", padx=12, pady=(10, 8))
            
            ctk.CTkLabel(
                header_frame,
                text=label,
                font=("Arial", 14, "bold"),
                text_color="#FFFFFF"
            ).pack(side="left")
            
            ctk.CTkLabel(
                header_frame,
                text=f"{dur}s",
                font=("Arial", 10),
                text_color="#888888"
            ).pack(side="right")
            
            # Main record button
            btn = ctk.CTkButton(
                container,
                text=f"RECORD: {label} (1/{recordings_per_label})",
                height=42,
                font=("Arial", 13, "bold"),
                fg_color="#1F6AA5",
                hover_color="#1976D2",
                border_color="#2196F3",
                border_width=2,
                corner_radius=6,
                command=lambda m=label: self.trigger_recording(m)
            )
            btn.pack(fill="x", padx=12, pady=(0, 10))
            
            # Store button reference
            self.recording_buttons[label] = btn
            
            # Separator
            ctk.CTkFrame(container, height=1, fg_color="#333333").pack(fill="x", padx=12, pady=(0, 8))
            
            # Recording list
            list_frame = ctk.CTkFrame(container, fg_color="transparent")
            list_frame.pack(fill="x", padx=12, pady=(0, 10))
            self.redo_button_frames[label] = list_frame
            
            # Create all recording slots
            for i in range(recordings_per_label):
                row_frame = ctk.CTkFrame(list_frame, fg_color="#252525", corner_radius=4)
                row_frame.pack(fill="x", pady=2)
                
                inner_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
                inner_frame.pack(fill="x", padx=8, pady=6)
                
                # Number badge
                badge = ctk.CTkLabel(
                    inner_frame,
                    text=str(i+1),
                    width=24,
                    height=24,
                    font=("Arial", 11, "bold"),
                    fg_color="#333333",
                    corner_radius=12,
                    text_color="#888888"
                )
                badge.pack(side="left", padx=(0, 10))
                
                # Recording label
                rec_label = ctk.CTkLabel(
                    inner_frame,
                    text=f"Recording {i+1}/{recordings_per_label}",
                    text_color="#999999",
                    font=("Arial", 11),
                    anchor="w"
                )
                rec_label.pack(side="left", fill="x", expand=True)
                
                # View button (initially disabled)
                view_btn = ctk.CTkButton(
                    inner_frame,
                    text="View",
                    width=50,
                    height=26,
                    font=("Arial", 10, "bold"),
                    fg_color="#2196F3",
                    hover_color="#1976D2",
                    corner_radius=4,
                    state="disabled",
                    command=lambda m=label, num=i+1: self.view_recorded_data(m, num)
                )
                view_btn.pack(side="right", padx=(5, 0))
                
                # Redo button
                redo_btn = ctk.CTkButton(
                    inner_frame,
                    text="Redo",
                    width=60,
                    height=26,
                    font=("Arial", 10, "bold"),
                    fg_color="#FF6B6B",
                    hover_color="#FF5252",
                    corner_radius=4,
                    state="disabled",
                    command=lambda m=label, num=i+1: self.trigger_redo_specific(m, num)
                )
                redo_btn.pack(side="right")
                self.individual_redo_buttons[label].append(redo_btn)
                
                # Delete button (Left of Redo)
                del_btn = ctk.CTkButton(
                    inner_frame,
                    text="Del",
                    width=40,
                    height=26,
                    font=("Arial", 10, "bold"),
                    fg_color="#333333",
                    hover_color="#555555",
                    corner_radius=4,
                    state="disabled",
                    command=lambda m=label, num=i+1: self.delete_recording(m, num)
                )
                del_btn.pack(side="right", padx=(0, 5))
                # Store references if needed, or just keep in closure

                
                # Store view button reference
                self.individual_view_buttons[label].append(view_btn)
            
            # Ensure folder exists
            mov_path = os.path.join(self.root_folder, label)
            if not os.path.exists(mov_path):
                os.makedirs(mov_path)

        self.log(f"Session started for {name}. Ready to record.")

    # --- Recording Logic ---

    def trigger_recording(self, movement, force_count=None):
        if self.recording_active_flag: return
        if not self.connected_state:
            messagebox.showerror("Error", "Device not connected!")
            return
        
        # Get recording number
        if force_count is not None:
             self.current_recording_count = force_count
        else:
             self.current_recording_count = self.current_recording_num.get(movement, 1)

        # Get Duration
        duration_sec = 5
        for m in self.session_config["movements"]:
            if m["label"] == movement:
                duration_sec = m.get("duration_sec", 5)
                break
        
        self.recording_direction_movement = movement
        self.recording_duration = duration_sec * 1000 # ms
        self.recording_start_ts = time.time() * 1000
        self.recording_buffer = [] # Clear buffer
        self.recording_active_flag = True # START RECORDING
        
        # Show Popup
        self.show_recording_popup(movement, self.current_recording_count)
        self.log(f"--- Started Recording: {movement} ---")

    def finish_recording(self):
        self.recording_active_flag = False # STOP RECORDING
        movement = self.recording_direction_movement
        
        collected_data = self.recording_buffer
        duration_sec = self.recording_duration / 1000.0
        
        self.log(f"Recorded {len(collected_data)} samples.")
        
        if len(collected_data) > 0:
            try:
                # 1. Save
                self.save_data_files(movement, collected_data)
            except Exception as e:
                self.log(f"CRITICAL ERROR saving file: {e}")
                import traceback
                traceback.print_exc()
                return

            try:
                # 2. Update UI
                self.update_recording_row_label(movement, self.current_recording_count, duration_sec=duration_sec)
                self.update_recording_row_conn_buttons(movement, self.current_recording_count, state="normal")
            except Exception as e:
                self.log(f"CRITICAL ERROR updating UI: {e}")
                import traceback
                traceback.print_exc()

            try:
                # 3. Show Graph
                self.view_recorded_data(movement, self.current_recording_count)
            except Exception as e:
                self.log(f"CRITICAL ERROR viewing data: {e}")
            
            # 4. Auto-Advance to next recording if this was the current one
            try:
                # Check if we just recorded the "head" (latest) recording
                current_head = self.current_recording_num.get(movement, 1)
                recordings_per_label = self.session_config.get("recordings_per_label", 1)
                
                if self.current_recording_count == current_head:
                    if current_head < recordings_per_label:
                        # Advance
                        self.advance_recording_state(movement)
                    else:
                        # Done with this label
                        self.mark_label_complete(movement)
            except Exception as e:
                print(f"Auto-advance error: {e}")

        else:
            self.log("Error: No data received.")

        # Update Graphs (Optional, since we likely viewed it live)
 
        


    def save_data_files(self, movement, values):
        # 1. Use Session ID with recording count (no underscore)
        record_id = self.current_participant.get('record_id', 'UNKNOWN_ID')
        recording_count = self.current_recording_count

        # 2. Generate Filename: [label].[id][count].json (no underscore before count)
        filename = f"{movement}.{record_id}{recording_count}.json"
        
        # 3. Define Paths
        folder_path = os.path.join(self.root_folder, movement)
        full_path = os.path.join(folder_path, filename)

        # 4. Build JSON
        sanitized_name = self.current_participant['name'].replace(" ", "_")
        
        sensor_mode = self.session_config.get("sensor_mode", "Both")
        
        # Filter Data based on mode
        final_values = []
        final_sensors = []
        
        if sensor_mode == "Accelerometer":
            # Keep indices 0,1,2
            final_sensors = [
                { "name": "ax", "units": "m/s2" }, { "name": "ay", "units": "m/s2" }, { "name": "az", "units": "m/s2" }
            ]
            final_values = [[row[0], row[1], row[2]] for row in values]
            
        elif sensor_mode == "Gyroscope":
            # Keep indices 3,4,5
            final_sensors = [
                 { "name": "gx", "units": "deg/s" }, { "name": "gy", "units": "deg/s" }, { "name": "gz", "units": "deg/s" }
            ]
            final_values = [[row[3], row[4], row[5]] for row in values]
            
        else: # Both
            final_sensors = [
                { "name": "ax", "units": "m/s2" }, { "name": "ay", "units": "m/s2" }, { "name": "az", "units": "m/s2" },
                { "name": "gx", "units": "deg/s" }, { "name": "gy", "units": "deg/s" }, { "name": "gz", "units": "deg/s" }
            ]
            final_values = values

        duration_sec = 5
        for m in self.session_config["movements"]:
            if m["label"] == movement:
                duration_sec = m.get("duration_sec", 5)
                break
                
        actual_interval_ms = (duration_sec * 1000.0) / len(final_values) if len(final_values) > 0 else 1000.0 / self.session_config.get("frequency", 50)

        payload = {
            "protected": {"ver": "v1", "alg": "none", "iat": int(time.time())},
            "signature": "0",
            "payload": {
                "record_id": record_id,
                "device_name": sanitized_name,
                "device_type": "ESP32-S3",
                "interval_ms": actual_interval_ms,
                "sensors": final_sensors,
                "values": final_values
            }
        }

        # 4. Write JSON
        with open(full_path, 'w') as f:
            json.dump(payload, f)
        
        self.log(f"Saved: {filename}")
        
        # --- BACKUP SAVE ---
        if hasattr(self, 'backup_folder') and self.backup_folder:
            try:
                # Mirror folder structure in backup (e.g., BACKUP/Walking/file.json)
                backup_mov_dir = os.path.join(self.backup_folder, movement)
                if not os.path.exists(backup_mov_dir):
                    os.makedirs(backup_mov_dir)
                    
                backup_path = os.path.join(backup_mov_dir, filename)
                
                with open(backup_path, 'w') as f:
                    # Save exact copy of ORIGINAL data (before any cropping can happen)
                    json.dump(payload, f)
                self.log(f"Backup saved: BACKUP/{movement}/{filename}")
            except Exception as e:
                self.log(f"Backup failed: {e}")

        # 5. Update Master CSV Log (Handle Redos)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self.root_folder, self.log_filename)
        
        # Calculate relative path
        rel_path = os.path.relpath(full_path, self.root_folder)

        # Look up duration for this specific movement
        duration_sec = 5
        for m in self.session_config["movements"]:
            if m["label"] == movement:
                duration_sec = m.get("duration_sec", 5)
                break
        
        # Check if we need to update an existing row (Redo) or append new
        updated = False
        rows = []
        
        if os.path.exists(log_path):
            with open(log_path, 'r', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
        
        # Find index of File_Path column (should be 8)
        file_path_idx = 8
        redo_count_idx = 9
        
        for i in range(1, len(rows)): # Skip header
            if len(rows[i]) > file_path_idx and rows[i][file_path_idx] == rel_path:
                # Update existing row
                rows[i][0] = timestamp # Update timestamp
                
                # Increment Redo Count
                current_redos = 0
                if len(rows[i]) > redo_count_idx:
                    try:
                        current_redos = int(rows[i][redo_count_idx])
                    except: pass
                else:
                    # If column didn't exist, append it
                    rows[i].append("0")
                
                # Update/Set Redo Count
                if len(rows[i]) > redo_count_idx:
                    rows[i][redo_count_idx] = str(current_redos + 1)
                else:
                    rows[i].append(str(current_redos + 1))
                    
                # Reset Cropped Duration because this is a new UN-cropped file now
                cropped_duration_idx = 10
                if len(rows[i]) > cropped_duration_idx:
                    rows[i][cropped_duration_idx] = "N/A"
                else:
                    while len(rows[i]) <= cropped_duration_idx:
                        rows[i].append("N/A")
                    
                updated = True
                self.log(f"Updated log for Redo: {filename} (Cnt: {current_redos + 1})")
                break
        
        if updated:
            # Write back all rows
            try:
                with open(log_path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
            except PermissionError:
                self.log(f"WARNING: Could not update log file (Row Update). Is it open in Excel?")
            except Exception as e:
                self.log(f"Error updating log: {e}")
        else:
            # Append new row
            try:
                with open(log_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp,
                        record_id,
                        self.current_participant['name'],
                        self.current_participant['age'],
                        self.current_participant['sex'],
                        movement,
                        int(duration_sec * 1000), # Duration
                        self.session_config["frequency"],
                        rel_path,
                        "0", # Initial Redo Count
                        "N/A" # Initial Cropped Duration
                    ])
                self.log(f"Master log updated. ID: {record_id}")
            except PermissionError:
                self.log(f"WARNING: Could not write new row to log file. Is it open in Excel?")
            except Exception as e:
                self.log(f"Error writing to log: {e}")
    
    def update_recording_button_state(self, movement, duration_sec=None):
        """After recording completes, enable redo button and update main button to 'Record Next'"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        
        # Identify which recording just finished (from the thread state)
        # Fallback to current_recording_num if not set (shouldn't happen in normal flow)
        finished_num = getattr(self, "current_recording_count", 1)
        
        # Mark THIS recording as complete
        if finished_num <= recordings_per_label:
            self.completed_recordings[movement][finished_num - 1] = True
        
        # Enable buttons
        redo_buttons = self.individual_redo_buttons.get(movement, [])
        view_buttons = self.individual_view_buttons.get(movement, [])
        if finished_num - 1 < len(redo_buttons):
            redo_buttons[finished_num - 1].configure(state="normal")
        # Also enable Del button? It's dynamically packed but we can find it via children
        # or simplified: Iterate children of inner_frame and enable "Del"
        
        self.update_recording_row_conn_buttons(movement, finished_num, state="normal")

        if finished_num - 1 < len(view_buttons):
            view_buttons[finished_num - 1].configure(state="normal")
        
        # Update the recording row to show completion for THIS recording
        if duration_sec is not None:
             self.update_recording_row_label(movement, finished_num, duration_sec=duration_sec)


        # Check if we should advance the MAIN sequence
        # Only advance if we just finished the "next" scheduled recording
        current_seq_num = self.current_recording_num.get(movement, 1)
        
        if finished_num == current_seq_num:
            # We finished the expected next recording, so advance sequence
            next_num = current_seq_num + 1
            self.current_recording_num[movement] = next_num
            
            # Update main button
            btn = self.recording_buttons.get(movement)
            if btn:
                if next_num <= recordings_per_label:
                    # Update to "Record Next (X/Y)"
                    btn.configure(
                        text=f"Record Next ({next_num}/{recordings_per_label})",
                        fg_color="#FF9800",
                        hover_color="#FB8C00",
                        border_color="#FFA726"
                    )
                else:
                    # All recordings complete - show green "All Done"
                    btn.configure(
                        text="All Done",
                        fg_color="#4CAF50",
                        hover_color="#4CAF50",
                        border_color="#66BB6A",
                        state="normal",
                        command=lambda: None
                    )
    def update_recording_row_conn_buttons(self, movement, recording_num, state="normal"):
        list_frame = self.redo_button_frames.get(movement)
        if list_frame:
            row_frames = [w for w in list_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
            if recording_num - 1 < len(row_frames):
                row_frame = row_frames[recording_num - 1]
                inner_frames = [w for w in row_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
                if inner_frames:
                    inner_frame = inner_frames[0]
                    for widget in inner_frame.winfo_children():
                        if isinstance(widget, ctk.CTkButton):
                            if widget.cget("text") == "Del":
                                widget.configure(state=state)

    def trigger_redo_specific(self, movement, recording_num):
        """Trigger re-recording of a specific instance"""
        if self.is_recording:
            return
        
        # Trigger recording with EXPLICIT count (preserve global sequence)
        self.trigger_recording(movement, force_count=recording_num)

    def delete_recording(self, movement, recording_num):
        confirm = messagebox.askyesno("Delete", "Are you sure you want to delete this recording?\nThis creates an empty file and marks it deleted.")
        if not confirm: return
        
        # File Operations
        record_id = self.current_participant.get('record_id', 'UNKNOWN_ID')
        filename = f"{movement}.{record_id}{recording_num}.json"
        
        # 1. Main File -> Empty
        filepath = os.path.join(self.root_folder, movement, filename)
        if os.path.exists(filepath):
            with open(filepath, 'w') as f:
                f.write("{}") # Empty JSON
        
        # 2. Update CSV Log -> "deleted" in Path
        self.update_csv_log_path(filename, "deleted")
        
        # 3. Update UI
        self.update_recording_row_label(movement, recording_num, text_override="Deleted", color="#D32F2F")
        self.update_recording_row_conn_buttons(movement, recording_num, state="disabled")
        
        # Also disable Redo/View for safety? Or allow Redo to recover?
        # Requirement says "clear... update path". "Deleted" implies gone. 
        # I'll disable View but keep Redo active? No, usually delete means done with it.
        # But if they want to re-record, Redo needs to be active.
        # I'll keep Redo active if user wants to fix it later, but disabling View is key.
        # Actually I just disabled "Del" above. Let's disable View too.
        # View is handled by self.individual_view_buttons list logic.
        view_buttons = self.individual_view_buttons.get(movement, [])
        if recording_num - 1 < len(view_buttons):
            view_buttons[recording_num - 1].configure(state="disabled")

    def update_csv_log_path(self, filename_search, new_path_val, cropped_duration_ms=None):
        log_path = os.path.join(self.root_folder, self.log_filename)
        if not os.path.exists(log_path): return
        
        rows = []
        with open(log_path, 'r', newline='') as f:
            reader = csv.reader(f)
            rows = list(reader)
        
        file_path_idx = 8
        cropped_duration_idx = 10
        found = False
        
        for i in range(1, len(rows)):
            # Handle potential missing columns in old logs
            while len(rows[i]) <= cropped_duration_idx:
                rows[i].append("N/A")
                
            if len(rows[i]) > file_path_idx and rows[i][file_path_idx].endswith(filename_search):
                if new_path_val is not None:
                    rows[i][file_path_idx] = new_path_val
                if cropped_duration_ms is not None:
                    rows[i][cropped_duration_idx] = str(cropped_duration_ms)
                found = True
                break
        
        if found:
            try:
                with open(log_path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
            except PermissionError:
                self.log(f"WARNING: Could not update cropped time in log file. Is it open in Excel?")
            except Exception as e:
                self.log(f"Error updating log: {e}")

    def update_recording_row_label(self, movement, recording_num, duration_sec=None, text_override=None, color="#4CAF50", status_text="Complete"):
        """Helper to find and update the recording label with duration"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        list_frame = self.redo_button_frames.get(movement)
        if list_frame:
            row_frames = [w for w in list_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
            if recording_num - 1 < len(row_frames):
                row_frame = row_frames[recording_num - 1]
                # Get inner frame
                inner_frames = [w for w in row_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
                if inner_frames:
                    inner_frame = inner_frames[0]
                    # Update badge and label
                    for widget in inner_frame.winfo_children():
                        if isinstance(widget, ctk.CTkLabel):
                            # Check if it's the badge (has width property)
                            if hasattr(widget, 'cget') and widget.cget('width') == 24:
                                # Update badge to green (or color) with checkmark/cross
                                txt = "✓" if not text_override == "Deleted" else "X"
                                widget.configure(
                                    text=txt,
                                    fg_color=color,
                                    text_color="#FFFFFF"
                                )
                            else:
                                # Update recording label
                                if text_override:
                                    final_text = f"Recording {recording_num}/{recordings_per_label} - {text_override}"
                                else:
                                    rounded_up = math.ceil(duration_sec) if duration_sec else 0
                                    final_text = f"Recording {recording_num}/{recordings_per_label} - {status_text} ({rounded_up}s)"
                                
                                widget.configure(
                                    text=final_text,
                                    text_color=color,
                                    font=("Arial", 11, "bold")
                                )

    def update_recording_row_conn_buttons(self, movement, recording_num, state="normal"):
        """Enable or Disable the View/Redo buttons for a specific row"""
        # View Buttons
        if movement in self.individual_view_buttons:
            btns = self.individual_view_buttons[movement]
            if recording_num - 1 < len(btns):
                btns[recording_num - 1].configure(state=state)
        
        # Redo Buttons
        if movement in self.individual_redo_buttons:
            btns = self.individual_redo_buttons[movement]
            if recording_num - 1 < len(btns):
                 btns[recording_num - 1].configure(state=state)

    def trigger_redo_specific(self, movement, recording_num):
        """Trigger re-recording of a specific instance"""
        if self.is_recording:
            return
        
        # Trigger recording with EXPLICIT count (preserve global sequence)
        self.trigger_recording(movement, force_count=recording_num)
    
    def view_recorded_data(self, movement, recording_num):
        """Load and display recorded data in live visualization"""
        try:
            # Build filename using correct record_id from current_participant
            if not self.current_participant:
                self.log("Error: No active participant session")
                return

            record_id = self.current_participant.get('record_id', 'UNKNOWN_ID')
            filename = f"{movement}.{record_id}{recording_num}.json"
            filepath = os.path.join(self.root_folder, movement, filename)
            
            # Check if file exists
            if not os.path.exists(filepath):
                self.log(f"File not found: {filename}")
                return
            
            # Load JSON data
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            # Extract sensor values
            sensor_data = data.get("payload", {}).get("values", [])
            if not sensor_data:
                self.log(f"No sensor data found in {filename}")
                return
                
            self.current_viewing_interval_ms = data.get("payload", {}).get("interval_ms", 1000.0 / self.session_config.get("frequency", 50))
            
            # --- View Data (Stay in Recording Mode) ---
            
            # 1. Stop any live thread if it happened to be running
            self.live_event.clear()
            
            # 2. Render data directly to graph
            self.log(f"Viewing Recording: {filename} ({len(sensor_data)} samples)")
            
            # STORE DATA for cropping
            self.full_recording_data = sensor_data
            self.current_viewing_filepath = filepath
            # Track meta for UI updates on save
            self.current_viewing_meta = {"movement": movement, "num": recording_num}
            
            self.btn_crop_mode.configure(state="normal") # Enable crop button
            
            # Reset crop state if we are already in crop mode, or just render default
            if self.cropping_mode:
                self.setup_crop_sliders_for_data()
                # self.on_crop_change() -> triggered by render if we want, but let's just render full first
            
            self.render_graph_data(sensor_data, title_prefix="Recorded")
            
        except Exception as e:
            self.log(f"Error viewing data: {e}")
    
    def handle_redo_action(self, movement):
        """Handle Redo button click - redo current recording"""
        # Current number stays the same (we're redoing it)
        current_num = self.current_recording_num.get(movement, 1)
        
        # Trigger recording for same number
        self.trigger_recording(movement)
    
    def advance_recording_state(self, movement):
        """Advance the recording state for a label to the next number"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        current_num = self.current_recording_num.get(movement, 1)
        
        next_num = current_num + 1
        self.current_recording_num[movement] = next_num
        
        # Update Main Button
        btn = self.recording_buttons.get(movement)
        if btn:
            btn.configure(
                text=f"RECORD: {movement} ({next_num}/{recordings_per_label})",
                fg_color="#1F6AA5", # Reset color if it was changed
                state="normal"
            )
            
    def mark_label_complete(self, movement):
        """Mark a label as fully complete (all recordings done)"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        
        # Update Main Button to show complete
        btn = self.recording_buttons.get(movement)
        if btn:
            btn.configure(
                text=f"{movement} - COMPLETED ({recordings_per_label}/{recordings_per_label})",
                fg_color="#388E3C", # Green
                state="disabled"
            )

    def handle_next_action(self, movement):
        """Handle Next button click - advance to next recording"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        current_num = self.current_recording_num.get(movement, 1)
        
        # Increment to next recording
        next_num = current_num + 1
        self.current_recording_num[movement] = next_num
        
        # Clear redo frame
        redo_frame = self.redo_button_frames.get(movement)
        if redo_frame:
            for widget in redo_frame.winfo_children():
                widget.destroy()
        
        # Update and show main button
        btn = self.recording_buttons.get(movement)
        if btn:
            btn.configure(text=f"RECORD: {movement} ({next_num}/{recordings_per_label})")
            btn.pack(fill="x")
    
    def handle_complete_action(self, movement):
        """Handle completion - reset to first recording"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        
        # Reset to first recording
        self.current_recording_num[movement] = 1
        
        # Clear redo frame
        redo_frame = self.redo_button_frames.get(movement)
        if redo_frame:
            for widget in redo_frame.winfo_children():
                widget.destroy()
        
        # Update and show main button with checkmark
        btn = self.recording_buttons.get(movement)
        if btn:
            btn.configure(
                text=f"RECORD: {movement} (1/{recordings_per_label}) ✓",
                border_color="#00C853"
            )
            btn.pack(fill="x")
    
    def add_redo_button(self, movement, recording_num):
        """Add a redo button for a specific recording instance"""
        redo_frame = self.redo_button_frames.get(movement)
        if not redo_frame:
            return
        
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        
        # Clear existing buttons and recreate all
        for widget in redo_frame.winfo_children():
            widget.destroy()
        
        # Create redo buttons for all completed recordings
        for i in range(recordings_per_label):
            if self.completed_recordings[movement][i]:
                btn = ctk.CTkButton(
                    redo_frame,
                    text=f"Redo {i+1}",
                    width=80,
                    height=30,
                    fg_color="#444",
                    hover_color="#666",
                    command=lambda m=movement, num=i+1: self.trigger_redo_recording(m, num)
                )
                btn.pack(side="left", padx=2)
    
    def trigger_redo_recording(self, movement, recording_num):
        """Trigger re-recording of a specific instance"""
        if self.is_recording:
            return
        
        self.is_recording = True
        self.recording_start_time = None
        self.current_recording_count = recording_num  # Set to specific instance to redo
        
        # Start recording thread
        threading.Thread(target=self.run_record_thread, args=(movement,), daemon=True).start()
        
        # Show progress popup (same as normal recording)
        self.show_recording_popup(movement, recording_num)
    
    def show_recording_popup(self, movement, recording_num):
        """Show recording progress popup"""
        recordings_per_label = self.session_config.get("recordings_per_label", 1)
        
        top = ctk.CTkToplevel(self)
        top.geometry("300x150")
        top.title(f"Recording: {movement} ({recording_num}/{recordings_per_label})")
        top.attributes("-topmost", True)
        
        try:
            top.iconbitmap(os.path.join(os.path.dirname(__file__), "SoterCare-icon.ico"))
        except: pass

        lbl_time = ctk.CTkLabel(top, text="Connecting...", font=("Arial", 30, "bold"))
        lbl_time.pack(expand=True)
        
        # Look up duration
        duration_sec = 5
        for m in self.session_config["movements"]:
            if m["label"] == movement:
                duration_sec = m.get("duration_sec", 5)
                break
        
        def update_timer():
            if not self.recording_active_flag:
                top.destroy()
                return
            
            if self.recording_start_ts is None:
                lbl_time.configure(text="Connecting...")
                top.after(50, update_timer)
                return
                
            elapsed = time.time() - (self.recording_start_ts / 1000.0)
            remaining = max(0, duration_sec - elapsed)
            lbl_time.configure(text=f"{remaining:.1f}s")
            
            if remaining >= 0:
                top.after(100, update_timer)
            else:
                top.destroy()

        top.after(50, update_timer)


    # --- Cropping Logic ---
    def toggle_crop_mode(self):
        if not self.full_recording_data:
            return

        self.cropping_mode = not self.cropping_mode
        
        if self.cropping_mode:
            self.btn_crop_mode.configure(fg_color="#00C853", text="Cropping Active")
            
            # Repack to ensure controls get space at bottom
            self.canvas.get_tk_widget().pack_forget()
            self.frame_crop_controls.pack(fill="x", padx=10, pady=10, side="bottom")
            self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)
            
            self.setup_crop_sliders_for_data()
        else:
            self.btn_crop_mode.configure(fg_color="#333", text="Cropping Mode")
            self.frame_crop_controls.pack_forget()
            # Restore full view
            self.render_graph_data(self.full_recording_data, title_prefix="Recorded")

    def setup_crop_sliders_for_data(self):
        if not self.full_recording_data: return
        
        total_samples = len(self.full_recording_data)
        interval_ms = getattr(self, "current_viewing_interval_ms", 1000.0 / self.session_config.get("frequency", 50))
        
        # Configure sliders to use percentages to ensure accurate cropping regardless of frequency
        max_steps = 100
        
        self.slider_crop_start.configure(from_=0, to=max_steps, number_of_steps=max_steps)
        self.slider_crop_start.set(0) # Start at 0%
        
        self.slider_crop_end.configure(from_=0, to=max_steps, number_of_steps=max_steps)
        self.slider_crop_end.set(max_steps) # Start at 100%
        
        self.update_crop_labels(0, total_samples * interval_ms)
    
    def on_crop_change(self, val):
        if not self.full_recording_data or not self.cropping_mode: return
        
        start_pct = int(self.slider_crop_start.get())
        end_pct = int(self.slider_crop_end.get())
        
        total = len(self.full_recording_data)
        interval_ms = getattr(self, "current_viewing_interval_ms", 1000.0 / self.session_config.get("frequency", 50))
        
        # Convert percentages to sample indices
        start_idx = int((start_pct / 100.0) * total)
        end_idx = int((end_pct / 100.0) * total)
        
        if end_idx == 0: end_idx = 1
        if end_idx > total: end_idx = total
        
        if start_idx >= end_idx:
            start_idx = max(0, end_idx - max(1, int(0.01 * total)))
            self.slider_crop_start.set(int((start_idx / total) * 100))

        start_ms = start_idx * interval_ms
        end_ms = end_idx * interval_ms
        self.update_crop_labels(start_ms, end_ms)
        
        if start_idx < end_idx:
            subset = self.full_recording_data[start_idx:end_idx]
            self.render_graph_data(subset, title_prefix="Cropped")
            
    def update_crop_labels(self, start_ms, end_ms):
        self.lbl_crop_start.configure(text=f"Start: {int(start_ms)}ms")
        self.lbl_crop_end.configure(text=f"End: {int(end_ms)}ms")
        
        duration_ms = end_ms - start_ms
        if hasattr(self, 'lbl_crop_duration'):
            self.lbl_crop_duration.configure(text=f"Final Length: {int(duration_ms)}ms")

    def save_cropped_data(self):
        if not self.full_recording_data or not self.current_viewing_filepath:
            return
            
        try:
            start_pct = int(self.slider_crop_start.get())
            end_pct = int(self.slider_crop_end.get())
            
            total = len(self.full_recording_data)
            start_idx = int((start_pct / 100.0) * total)
            end_idx = int((end_pct / 100.0) * total)
            
            if end_idx == 0: end_idx = 1
            if end_idx > total: end_idx = total
            
            if start_idx >= end_idx:
                messagebox.showerror("Error", "Invalid crop selection.")
                return

            new_data = self.full_recording_data[start_idx:end_idx]
            
            with open(self.current_viewing_filepath, 'r') as f:
                full_json = json.load(f)
            
            full_json["payload"]["values"] = new_data
            
            with open(self.current_viewing_filepath, 'w') as f:
                json.dump(full_json, f)
            
            self.full_recording_data = new_data
            
            interval_ms = getattr(self, "current_viewing_interval_ms", 1000.0 / self.session_config.get("frequency", 50))
            new_duration_sec = (len(new_data) * interval_ms) / 1000.0
            
            # Update CSV Log
            filename = os.path.basename(self.current_viewing_filepath)
            self.update_csv_log_path(filename, None, int(new_duration_sec * 1000))
            
            self.log(f"Saved cropped data. New length: {len(new_data)} samples ({new_duration_sec:.2f}s).")
            
            self.setup_crop_sliders_for_data()
            self.render_graph_data(self.full_recording_data, title_prefix="Recorded")
            
            if self.current_viewing_meta:
                m = self.current_viewing_meta.get("movement")
                n = self.current_viewing_meta.get("num")
                if m and n:
                    self.update_recording_row_label(m, n, new_duration_sec, status_text="Cropped")
            
        except Exception as e:
            self.log(f"Error saving cropped data: {e}")
            messagebox.showerror("Error", f"Failed to save: {e}")

    # --- Preview Mode Logic ---
    def open_preview_window(self):
        # Prevent opening multiple windows
        if hasattr(self, "preview_window") and self.preview_window.winfo_exists():
            self.preview_window.lift()
            return
            
        self.preview_window = ctk.CTkToplevel(self)
        self.preview_window.title("Preview Recorded Data")
        self.preview_window.geometry("1100x700")
        
        # Delay icon and dark mode application slightly to prevent Windows DWM from glitching 
        # and drawing a white titlebar on the newly spawned transparent window.
        def apply_window_fixes():
            try:
                self.preview_window.iconbitmap(os.path.join(os.path.dirname(__file__), "SoterCare-icon.ico"))
            except Exception as e:
                print(f"Preview icon load warning: {e}")
            ctk.set_appearance_mode("Dark")
            
        self.preview_window.after(200, apply_window_fixes)
        
        # Bring the newly created window to the front without locking it 
        # as a strict un-minimizable dialog.
        self.preview_window.lift()
        self.preview_window.focus()

        self.preview_window.grid_columnconfigure(0, weight=1, minsize=300) # List column
        self.preview_window.grid_columnconfigure(1, weight=3) # Graph column
        self.preview_window.grid_rowconfigure(0, weight=0) # Header
        self.preview_window.grid_rowconfigure(1, weight=1) # Main content
        
        # State variables for preview
        self.preview_folder = ""
        self.preview_files = []
        self.preview_current_file = ""
        self.preview_data = []
        self.preview_interval_ms = 20.0 # Default 50Hz
        
        self.preview_file_buttons = []
        self.preview_selected_index = -1
        
        self.preview_window.bind("<Up>", self.preview_select_prev)
        self.preview_window.bind("<Down>", self.preview_select_next)
        
        # --- Header ---
        header_frame = ctk.CTkFrame(self.preview_window, corner_radius=10, fg_color="#1a1a1a")
        header_frame.grid(row=0, column=0, columnspan=2, padx=20, pady=10, sticky="ew")
        
        ctk.CTkButton(header_frame, text="Select Folder", command=self.preview_select_folder, width=150).pack(side="left", padx=10, pady=10)
        self.lbl_preview_folder = ctk.CTkLabel(header_frame, text="No folder selected", text_color="orange")
        self.lbl_preview_folder.pack(side="left", padx=10)
        
        # --- Left Panel: File List ---
        left_frame = ctk.CTkFrame(self.preview_window, corner_radius=10, fg_color="#1a1a1a")
        left_frame.grid(row=1, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        
        ctk.CTkLabel(left_frame, text="JSON Files", font=("Arial", 14, "bold")).pack(pady=10)
        
        self.preview_list_frame = ctk.CTkScrollableFrame(left_frame, fg_color="#2b2b2b")
        self.preview_list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        # --- Right Panel: Graph & Controls ---
        right_frame = ctk.CTkFrame(self.preview_window, corner_radius=10, fg_color="#1a1a1a")
        right_frame.grid(row=1, column=1, padx=(10, 20), pady=(0, 20), sticky="nsew")
        
        # Graphs
        self.preview_fig = Figure(figsize=(5, 5), dpi=100, facecolor="#1a1a1a")
        
        self.preview_ax1 = self.preview_fig.add_subplot(211)
        self.preview_ax1.set_facecolor("#2b2b2b")
        self.preview_ax1.set_title("Accelerometer (g)", color="white", fontsize=10)
        self.preview_ax1.tick_params(axis='x', colors='white')
        self.preview_ax1.tick_params(axis='y', colors='white')
        
        self.preview_ax2 = self.preview_fig.add_subplot(212)
        self.preview_ax2.set_facecolor("#2b2b2b")
        self.preview_ax2.set_title("Gyroscope (deg/s)", color="white", fontsize=10)
        self.preview_ax2.tick_params(axis='x', colors='white')
        self.preview_ax2.tick_params(axis='y', colors='white')
        
        self.preview_fig.tight_layout()
        
        self.preview_canvas = FigureCanvasTkAgg(self.preview_fig, master=right_frame)
        self.preview_canvas.draw()
        self.preview_canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)
        
        # File Action Controls (Delete)
        self.preview_file_actions = ctk.CTkFrame(right_frame, fg_color="transparent")
        self.preview_file_actions.pack(fill="x", padx=10, pady=(0, 5))
        
        self.btn_preview_delete = ctk.CTkButton(
            self.preview_file_actions,
            text="Delete This Recording",
            fg_color="#D32F2F", hover_color="#B71C1C",
            command=self.preview_delete_file,
            state="disabled"
        )
        self.btn_preview_delete.pack(side="right", pady=5)
        
        # Crop Controls
        self.preview_crop_controls = ctk.CTkFrame(right_frame, fg_color="#2b2b2b", corner_radius=10)
        self.preview_crop_controls.pack(fill="x", padx=10, pady=(5, 10))
        
        self.preview_crop_controls.columnconfigure(0, weight=1)
        self.preview_crop_controls.columnconfigure(1, weight=1)
        
        self.lbl_preview_crop_start = ctk.CTkLabel(self.preview_crop_controls, text="Crop Front: 0ms", font=("Arial", 10))
        self.lbl_preview_crop_start.grid(row=0, column=0, padx=10, pady=(5,0), sticky="w")
        
        self.slider_preview_crop_start = ctk.CTkSlider(
            self.preview_crop_controls, from_=0, to=100, command=self.on_preview_crop_change, number_of_steps=100
        )
        self.slider_preview_crop_start.set(0)
        self.slider_preview_crop_start.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        
        self.lbl_preview_crop_end = ctk.CTkLabel(self.preview_crop_controls, text="Crop Back: MAX", font=("Arial", 10))
        self.lbl_preview_crop_end.grid(row=0, column=1, padx=10, pady=(5,0), sticky="e")
        
        self.slider_preview_crop_end = ctk.CTkSlider(
            self.preview_crop_controls, from_=0, to=100, command=self.on_preview_crop_change, number_of_steps=100
        )
        self.slider_preview_crop_end.set(100)
        self.slider_preview_crop_end.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        
        self.btn_preview_save_crop = ctk.CTkButton(
            self.preview_crop_controls,
            text="Save Cropped Data",
            fg_color="#D32F2F", hover_color="#B71C1C",
            command=self.preview_save_crop,
            state="disabled"
        )
        self.btn_preview_save_crop.grid(row=2, column=0, columnspan=2, pady=10)
        
        self.lbl_preview_crop_duration = ctk.CTkLabel(
            self.preview_crop_controls, text="Final Length: 0ms", font=("Arial", 12, "bold"), text_color="#00C853"
        )
        self.lbl_preview_crop_duration.grid(row=3, column=0, columnspan=2, pady=(0, 5))
        
        # Initialize empty graphs
        self.preview_update_graph_visibility()

    def preview_select_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.preview_folder = path
            self.lbl_preview_folder.configure(text=f".../{os.path.basename(path)}", text_color="green")
            self.preview_refresh_file_list()

    def preview_refresh_file_list(self):
        # Clear existing
        for widget in self.preview_list_frame.winfo_children():
            widget.destroy()
            
        self.preview_files = []
        if not self.preview_folder or not os.path.exists(self.preview_folder): return
        
        # Traverse recursively to find all JSONs
        for root, dirs, files in os.walk(self.preview_folder):
            if "BACKUP" in root: continue # Skip backups
            for file in files:
                if file.endswith(".json"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.preview_folder)
                    self.preview_files.append((rel_path, full_path))
        
        if not self.preview_files:
            ctk.CTkLabel(self.preview_list_frame, text="No .json files found.", text_color="gray").pack(pady=20)
            return
            
        self.preview_file_buttons = []
        # Add buttons for each file
        for i, (rel_path, full_path) in enumerate(self.preview_files):
            btn = ctk.CTkButton(
                self.preview_list_frame,
                text=rel_path,
                anchor="w",
                fg_color="transparent",
                hover_color="#333",
                text_color="#ddd",
                command=lambda p=full_path, idx=i: self.preview_load_file_from_list(p, idx)
            )
            btn.pack(fill="x", pady=2, padx=5)
            self.preview_file_buttons.append(btn)

    def preview_load_file_from_list(self, filepath, idx):
        self.preview_set_selected_index(idx)
        self.preview_load_file(filepath)

    def preview_set_selected_index(self, idx):
        if not self.preview_file_buttons: return
        
        if 0 <= self.preview_selected_index < len(self.preview_file_buttons):
            self.preview_file_buttons[self.preview_selected_index].configure(fg_color="transparent", text_color="#ddd")
            
        self.preview_selected_index = idx
        
        if 0 <= self.preview_selected_index < len(self.preview_file_buttons):
            btn = self.preview_file_buttons[self.preview_selected_index]
            btn.configure(fg_color="#1F6AA5", text_color="#ffffff")
            
            # Basic scrolling logic to keep item in view
            try:
                if hasattr(self.preview_list_frame, "_parent_canvas"):
                    fraction = idx / max(1, len(self.preview_file_buttons))
                    center_frac = max(0, fraction - 0.1)
                    self.preview_list_frame._parent_canvas.yview_moveto(center_frac)
            except Exception: pass

    def preview_select_prev(self, event=None):
        if getattr(self, "preview_files", None):
            new_idx = self.preview_selected_index - 1
            if new_idx < 0: new_idx = 0
            if new_idx != self.preview_selected_index and new_idx < len(self.preview_files):
                filepath = self.preview_files[new_idx][1]
                self.preview_load_file_from_list(filepath, new_idx)
            
    def preview_select_next(self, event=None):
        if getattr(self, "preview_files", None):
            new_idx = self.preview_selected_index + 1
            if new_idx >= len(self.preview_files): new_idx = len(self.preview_files) - 1
            if new_idx != self.preview_selected_index and new_idx >= 0:
                filepath = self.preview_files[new_idx][1]
                self.preview_load_file_from_list(filepath, new_idx)

    def preview_load_file(self, filepath):
        if not os.path.exists(filepath):
            messagebox.showerror("Error", "File not found.", parent=self.preview_window)
            return

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                
            sensor_data = data.get("payload", {}).get("values", [])
            freq = data.get("payload", {}).get("interval_ms")
            
            if freq:
                self.preview_interval_ms = freq
            else:
                self.preview_interval_ms = 1000.0 / self.session_config.get("frequency", 50)
                
            if not sensor_data:
                # If empty
                self.preview_data = []
                self.preview_current_file = ""
                messagebox.showinfo("Empty", "This file contains no data (possibly deleted).", parent=self.preview_window)
                self.preview_update_graph_visibility()
                self.btn_preview_delete.configure(state="disabled")
                self.btn_preview_save_crop.configure(state="disabled")
                return
                
            self.preview_data = sensor_data
            self.preview_current_file = filepath
            
            self.btn_preview_delete.configure(state="normal")
            self.btn_preview_save_crop.configure(state="normal")
            
            # Setup crop sliders
            self.preview_setup_crop_sliders()
            
            # Render graph
            self.preview_render_graph(self.preview_data, title_prefix="Preview")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{e}", parent=self.preview_window)

    def preview_update_graph_visibility(self):
        self.preview_ax1.clear()
        self.preview_ax1.set_facecolor("#2b2b2b")
        self.preview_ax1.set_xticks([])
        self.preview_ax1.tick_params(axis='y', colors='#aaa')
        self.preview_ax1.set_ylim(-2.0, 2.0)
        self.preview_ax1.set_title("Accelerometer (g)", color="#777", fontsize=10)
        
        self.preview_ax2.clear()
        self.preview_ax2.set_facecolor("#2b2b2b")
        self.preview_ax2.set_xticks([])
        self.preview_ax2.tick_params(axis='y', colors='#aaa')
        self.preview_ax2.set_ylim(-400, 400)
        self.preview_ax2.set_title("Gyroscope (deg/s)", color="#777", fontsize=10)
        
        self.preview_canvas.draw()

    def preview_render_graph(self, data, title_prefix="Preview"):
        if not data: return
        cols = len(data[0])
        
        ax_data, ay_data, az_data = [], [], []
        gx_data, gy_data, gz_data = [], [], []
        
        has_accel = False
        has_gyro = False
        
        if cols == 6:
            has_accel = True
            has_gyro = True
            ax_data = [d[0] for d in data]
            ay_data = [d[1] for d in data]
            az_data = [d[2] for d in data]
            gx_data = [d[3] for d in data]
            gy_data = [d[4] for d in data]
            gz_data = [d[5] for d in data]
        elif cols == 3:
            max_val = max([abs(val) for row in data for val in row])
            if max_val > 50:
                has_gyro = True
                gx_data = [d[0] for d in data]
                gy_data = [d[1] for d in data]
                gz_data = [d[2] for d in data]
            else:
                has_accel = True
                ax_data = [d[0] for d in data]
                ay_data = [d[1] for d in data]
                az_data = [d[2] for d in data]
                
        times = range(len(data))
        
        # --- Plot Accel ---
        self.preview_ax1.clear()
        self.preview_ax1.set_facecolor("#2b2b2b")
        if has_accel:
            self.preview_ax1.set_title(f"{title_prefix} Accelerometer (g)", color="white", fontsize=10)
            self.preview_ax1.plot(times, ax_data, label='X', color='#FF5252', linewidth=1)
            self.preview_ax1.plot(times, ay_data, label='Y', color='#448AFF', linewidth=1)
            self.preview_ax1.plot(times, az_data, label='Z', color='#69F0AE', linewidth=1)
            self.preview_ax1.grid(True, color="#444", linestyle='--', linewidth=0.5)
            self.preview_ax1.legend(loc='upper right', facecolor="#333", edgecolor="white", labelcolor="white", fontsize=8)
        else:
            self.preview_ax1.set_title(f"{title_prefix} Accelerometer (Disabled or N/A)", color="#777", fontsize=10)
        self.preview_ax1.set_xticks([])
        self.preview_ax1.tick_params(axis='y', colors='#aaa')
        self.preview_ax1.set_ylim(-2.0, 2.0)

        # --- Plot Gyro ---
        self.preview_ax2.clear()
        self.preview_ax2.set_facecolor("#2b2b2b")
        if has_gyro:
            self.preview_ax2.set_title(f"{title_prefix} Gyroscope", color="white", fontsize=10)
            self.preview_ax2.plot(times, gx_data, label='X', color='#FF5252', linewidth=1)
            self.preview_ax2.plot(times, gy_data, label='Y', color='#448AFF', linewidth=1)
            self.preview_ax2.plot(times, gz_data, label='Z', color='#69F0AE', linewidth=1)
            self.preview_ax2.grid(True, color="#444", linestyle='--', linewidth=0.5)
            self.preview_ax2.legend(loc='upper right', facecolor="#333", edgecolor="white", labelcolor="white", fontsize=8)
        else:
            self.preview_ax2.set_title(f"{title_prefix} Gyroscope (Disabled or N/A)", color="#777", fontsize=10)
        self.preview_ax2.set_xticks([])
        self.preview_ax2.tick_params(axis='y', colors='#aaa')
        self.preview_ax2.set_ylim(-400, 400)
        
        self.preview_canvas.draw()
        
    def preview_setup_crop_sliders(self):
        if not self.preview_data: return
        total_samples = len(self.preview_data)
        
        max_steps = 100
        self.slider_preview_crop_start.configure(from_=0, to=max_steps, number_of_steps=max_steps)
        self.slider_preview_crop_start.set(0)
        
        self.slider_preview_crop_end.configure(from_=0, to=max_steps, number_of_steps=max_steps)
        self.slider_preview_crop_end.set(max_steps)
        
        self.preview_update_crop_labels(0, total_samples * self.preview_interval_ms)

    def on_preview_crop_change(self, val):
        if not self.preview_data: return
        
        start_pct = int(self.slider_preview_crop_start.get())
        end_pct = int(self.slider_preview_crop_end.get())
        total = len(self.preview_data)
        
        start_idx = int((start_pct / 100.0) * total)
        end_idx = int((end_pct / 100.0) * total)
        
        if end_idx == 0: end_idx = 1
        if end_idx > total: end_idx = total
        
        if start_idx >= end_idx:
            start_idx = max(0, end_idx - max(1, int(0.01 * total)))
            self.slider_preview_crop_start.set(int((start_idx / total) * 100))
            
        start_ms = start_idx * self.preview_interval_ms
        end_ms = end_idx * self.preview_interval_ms
        self.preview_update_crop_labels(start_ms, end_ms)
        
        if start_idx < end_idx:
            subset = self.preview_data[start_idx:end_idx]
            self.preview_render_graph(subset, title_prefix="Cropped")

    def preview_update_crop_labels(self, start_ms, end_ms):
        self.lbl_preview_crop_start.configure(text=f"Start: {int(start_ms)}ms")
        self.lbl_preview_crop_end.configure(text=f"End: {int(end_ms)}ms")
        duration_ms = end_ms - start_ms
        self.lbl_preview_crop_duration.configure(text=f"Final Length: {int(duration_ms)}ms")

    def preview_save_crop(self):
        if not self.preview_data or not self.preview_current_file: return
        
        try:
            start_pct = int(self.slider_preview_crop_start.get())
            end_pct = int(self.slider_preview_crop_end.get())
            total = len(self.preview_data)
            
            start_idx = int((start_pct / 100.0) * total)
            end_idx = int((end_pct / 100.0) * total)
            
            if end_idx == 0: end_idx = 1
            if end_idx > total: end_idx = total
            
            if start_idx >= end_idx:
                messagebox.showerror("Error", "Invalid crop selection.", parent=self.preview_window)
                return
                
            new_data = self.preview_data[start_idx:end_idx]
            
            with open(self.preview_current_file, 'r') as f:
                full_json = json.load(f)
                
            full_json["payload"]["values"] = new_data
            
            with open(self.preview_current_file, 'w') as f:
                json.dump(full_json, f)
                
            self.preview_data = new_data
            new_duration_sec = (len(new_data) * self.preview_interval_ms) / 1000.0
            
            if hasattr(self, 'root_folder') and self.root_folder:
                if self.root_folder in self.preview_current_file:
                    self.update_csv_log_path(os.path.basename(self.preview_current_file), None, int(new_duration_sec * 1000))
            
            self.preview_setup_crop_sliders()
            self.preview_render_graph(self.preview_data, title_prefix="Recorded")
            messagebox.showinfo("Success", f"Cropped data saved successfully. New length: {new_duration_sec:.2f}s", parent=self.preview_window)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save cropped data: {e}", parent=self.preview_window)

    def preview_delete_file(self):
        if not self.preview_current_file: return
        
        confirm = messagebox.askyesno("Delete", "Are you sure you want to permanently delete this JSON file?", parent=self.preview_window)
        if not confirm: return
        
        try:
            if hasattr(self, 'root_folder') and self.root_folder:
                if self.root_folder in self.preview_current_file:
                    self.update_csv_log_path(os.path.basename(self.preview_current_file), "deleted")
            
            os.remove(self.preview_current_file)
                    
            self.preview_data = []
            self.preview_current_file = ""
            self.preview_update_graph_visibility()
            self.btn_preview_delete.configure(state="disabled")
            self.btn_preview_save_crop.configure(state="disabled")
            
            self.preview_refresh_file_list()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete file: {e}", parent=self.preview_window)

if __name__ == "__main__":
    app = SoterCareLocalStudio()
    app.mainloop()