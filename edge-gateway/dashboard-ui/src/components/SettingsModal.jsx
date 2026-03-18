import React, { useState, useEffect } from "react";

export default function SettingsModal({
  onClose,
  volume,
  setVolume,
  tempUnit,
  setTempUnit,
  currentDevice,
  setCurrentDevice,
  localIp,
}) {
  const [activeTab, setActiveTab] = useState("general");
  const [scanning, setScanning] = useState(false);
  const [devices, setDevices] = useState([]);
  const [selectedDevice, setSelectedDevice] = useState(null);
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [ip, setIp] = useState(localIp || "192.168.");
  const [configureStatus, setConfigureStatus] = useState(null); // {status: 'success'|'error'|'loading', message: ''}

  // Handle outside click
  useEffect(() => {
    const handleEsc = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [onClose]);

  // Fetch current WiFi info when entering the connection tab once
  useEffect(() => {
    // Only fetch if ssid is empty and ip is still the default fallback
    if (activeTab === "connection" && !ssid && (ip === "192.168." || ip === localIp || !ip)) {
      const fetchWifi = async () => {
        try {
          const res = await fetch("http://localhost:5000/api/wifi-current");
          const data = await res.json();
          if (data.status === "ok") {
            if (data.ssid) setSsid(data.ssid);
            if (data.ip) setIp(data.ip);
          }
        } catch (e) {
          console.error("Failed to fetch active Wi-Fi info", e);
        }
      };
      fetchWifi();
    }
  }, [activeTab, ssid, ip, localIp]);

  const handleScan = async () => {
    setScanning(true);
    setDevices([]);
    setSelectedDevice(null);
    setConfigureStatus(null);
    try {
      const res = await fetch("http://localhost:5000/api/scan");
      const data = await res.json();
      if (data.status === "ok") {
        setDevices(data.devices || []);
      } else {
        setConfigureStatus({ status: "error", message: data.message });
      }
    } catch (e) {
      setConfigureStatus({ status: "error", message: "Scan request failed" });
    }
    setScanning(false);
  };

  const handleReset = async () => {
    if (!window.confirm("Are you sure you want to forget the last connected device?")) return;
    try {
      const res = await fetch("http://localhost:5000/api/reset", { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        setConfigureStatus({ status: "success", message: "Connection memory cleared." });
        if (setCurrentDevice) setCurrentDevice(null);
        setTimeout(onClose, 1500);
      } else {
        setConfigureStatus({ status: "error", message: data.message });
      }
    } catch (e) {
      setConfigureStatus({ status: "error", message: "Failed to reset connection data" });
    }
  };

  const handleConfigure = async (e) => {
    e.preventDefault();
    if (!selectedDevice || !ssid || !ip) return;
    setConfigureStatus({ status: "loading", message: "Sending configuration..." });
    try {
      const res = await fetch("http://localhost:5000/api/configure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address: selectedDevice.address,
          ssid,
          password,
          ip,
        }),
      });
      const data = await res.json();
      if (data.status === "success") {
        setConfigureStatus({
          status: "success",
          message: "Configured successfully. Node is rebooting.",
        });
        setTimeout(onClose, 3000);
      } else {
        setConfigureStatus({ status: "error", message: data.message });
      }
    } catch (err) {
      setConfigureStatus({ status: "error", message: "Failed to connect to gateway" });
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Dashboard Settings</h2>
          <button className="modal-close" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-tabs">
          <button
            className={`tab-btn ${activeTab === "general" ? "active" : ""}`}
            onClick={() => setActiveTab("general")}
          >
            General
          </button>
          <button
            className={`tab-btn ${activeTab === "connection" ? "active" : ""}`}
            onClick={() => setActiveTab("connection")}
          >
            Connection
          </button>
        </div>

        <div className="modal-body">
          {activeTab === "general" ? (
            <div className="tab-panel general-tab">
              <div className="setting-group">
                <label>Alert Volume</label>
                <div className="slider-container">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={volume}
                    onChange={(e) => setVolume(parseFloat(e.target.value))}
                    className="volume-slider"
                  />
                  <span>{Math.round(volume * 100)}%</span>
                </div>
              </div>

              <div className="setting-group">
                <label>Temperature Unit</label>
                <div className="toggle-group">
                  <button
                    className={`toggle-btn ${tempUnit === "C" ? "active" : ""}`}
                    onClick={() => setTempUnit("C")}
                  >
                    Celsius (°C)
                  </button>
                  <button
                    className={`toggle-btn ${tempUnit === "F" ? "active" : ""}`}
                    onClick={() => setTempUnit("F")}
                  >
                    Fahrenheit (°F)
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="tab-panel connection-tab">
              <div className="current-device-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div style={{ flex: 1 }}>
                    <h3>Last Connected Device</h3>
                    {currentDevice ? (
                      <div className="device-info">
                        <div className="device-address">{currentDevice.address}</div>
                        <div className="device-time">
                          Connected at: {new Date(currentDevice.timestamp * 1000).toLocaleString()}
                        </div>
                      </div>
                    ) : (
                      <div className="device-info text-muted">No device configured yet.</div>
                    )}
                  </div>
                  {currentDevice && (
                    <button 
                      onClick={handleReset}
                      style={{
                        padding: '6px 12px',
                        background: 'transparent',
                        border: '1px solid var(--coral)',
                        color: 'var(--coral)',
                        borderRadius: '6px',
                        fontSize: '12px',
                        fontWeight: '600',
                        cursor: 'pointer'
                      }}
                    >
                      Flush Memory
                    </button>
                  )}
                </div>
              </div>

              <div className="scan-section">
                <button
                  className="btn-primary scan-btn"
                  onClick={handleScan}
                  disabled={scanning}
                >
                  {scanning ? "Scanning..." : "Scan for SoterCare Nodes"}
                </button>

                {devices.length > 0 && (
                  <div className="device-list">
                    {devices.map((d) => (
                      <div
                        key={d.address}
                        className={`device-item ${selectedDevice?.address === d.address ? "selected" : ""}`}
                        onClick={() => setSelectedDevice(d)}
                      >
                        <div className="device-name">{d.name}</div>
                        <div className="device-details">
                          <span>{d.address}</span>
                          <span className="rssi-pill">{d.rssi} dBm</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {selectedDevice && (
                <form className="config-form" onSubmit={handleConfigure}>
                  <h3>Configure {selectedDevice.name}</h3>
                  <div className="input-group">
                    <label>Wi-Fi SSID</label>
                    <input
                      type="text"
                      required
                      placeholder="Network Name"
                      value={ssid}
                      onChange={(e) => setSsid(e.target.value)}
                    />
                  </div>
                  <div className="input-group">
                    <label>Wi-Fi Password</label>
                    <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
                      <input
                        type={showPassword ? "text" : "password"}
                        placeholder="Leave blank if open"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        style={{ width: "100%", paddingRight: "45px" }}
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        style={{
                          position: "absolute",
                          right: "10px",
                          background: "transparent",
                          border: "none",
                          cursor: "pointer",
                          color: "var(--text-dim)",
                          fontSize: "12px",
                          fontWeight: "bold",
                        }}
                      >
                        {showPassword ? "HIDE" : "SHOW"}
                      </button>
                    </div>
                  </div>
                  <div className="input-group">
                    <label>Gateway IP</label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. 192.168.1.10"
                      value={ip}
                      onChange={(e) => setIp(e.target.value)}
                    />
                  </div>

                  <button
                    type="submit"
                    className="btn-primary configure-btn"
                    disabled={configureStatus?.status === "loading"}
                  >
                    {configureStatus?.status === "loading"
                      ? "Configuring..."
                      : "Connect & Configure"}
                  </button>

                  {configureStatus && (
                    <div className={`status-msg ${configureStatus.status}`}>
                      {configureStatus.message}
                    </div>
                  )}
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
