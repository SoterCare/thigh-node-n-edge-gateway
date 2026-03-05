import { useEffect, useRef, useState, useCallback } from "react";
import { io } from "socket.io-client";
import {
  Chart,
  LineElement,
  PointElement,
  LineController,
  CategoryScale,
  LinearScale,
  Filler,
  Tooltip,
  Legend,
} from "chart.js";

Chart.register(
  LineElement,
  PointElement,
  LineController,
  CategoryScale,
  LinearScale,
  Filler,
  Tooltip,
  Legend,
);

// ── Constants ─────────────────────────────────────────────────────────────────
const MAX_POINTS = 150;
const SOCKET_URL = "http://localhost:5000"; // Connect directly — bypass Vite proxy

function makeDataset(label, color) {
  return {
    label,
    borderColor: color,
    backgroundColor: color + "18",
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.35,
    fill: false,
    data: [],
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function rssiToLevel(rssi) {
  if (!rssi || rssi === "N/A") return 0;
  const v = parseInt(rssi);
  if (v >= -50) return 4;
  if (v >= -65) return 3;
  if (v >= -80) return 2;
  return 1;
}

// ── Hook: live chart ──────────────────────────────────────────────────────────
function useLiveChart(canvasRef, datasetsInit) {
  const chartRef = useRef(null);

  useEffect(() => {
    const ctx = canvasRef.current.getContext("2d");
    chartRef.current = new Chart(ctx, {
      type: "line",
      data: { labels: Array(MAX_POINTS).fill(""), datasets: datasetsInit },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: "#64748b", boxWidth: 10, font: { size: 11 } },
          },
          tooltip: { enabled: true },
        },
        scales: {
          x: { display: false },
          y: {
            ticks: { color: "#94a3b8", font: { size: 10 } },
            grid: { color: "#f1f5f9" },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, []);

  const push = useCallback((values) => {
    const c = chartRef.current;
    if (!c) return;
    c.data.datasets.forEach((ds, i) => {
      ds.data.push(values[i]);
      if (ds.data.length > MAX_POINTS) ds.data.shift();
    });
    c.update("none");
  }, []);

  return push;
}

// ── Sub-components ────────────────────────────────────────────────────────────
function StatCard({ label, value, unit, sub, variant = "" }) {
  return (
    <div className={`card ${variant}`}>
      <div className="card-label">{label}</div>
      <div className="card-value">
        {value ?? "—"}
        {unit && <span className="card-unit">{unit}</span>}
      </div>
      {sub && <div className="card-sub">{sub}</div>}
    </div>
  );
}

function RSSICard({ rssi, source }) {
  const level = rssiToLevel(rssi);
  const heights = [5, 9, 13, 18];
  return (
    <div className="card">
      <div className="card-label">Signal Strength</div>
      <div className="rssi-bars">
        {heights.map((h, i) => (
          <div
            key={i}
            className={`rssi-bar ${i < level ? "active" : ""}`}
            style={{ height: h }}
          />
        ))}
      </div>
      <div className="rssi-val">
        {rssi && rssi !== "N/A" ? `${rssi} dBm` : "BLE (no RSSI)"}
        {" · "}
        <span style={{ textTransform: "capitalize" }}>{source || "—"}</span>
      </div>
    </div>
  );
}

function MoistureCard({ moisture }) {
  const pct = moisture ?? 0;
  return (
    <div className="card blue">
      <div className="card-label">Moisture Level</div>
      <div className="card-value">
        {pct}
        <span className="card-unit">%</span>
      </div>
      <div className="moisture-bar-bg">
        <div className="moisture-bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function GaitCard({ label }) {
  return (
    <div className="card purple">
      <div className="card-label">Gait Analysis</div>
      <div style={{ marginTop: 6 }}>
        <span className="gait-chip">{label || "N/A"}</span>
      </div>
      <div className="card-sub">Edge Impulse model</div>
    </div>
  );
}

function FallCard({ alert }) {
  return (
    <div className={`card ${alert ? "red-card" : ""}`}>
      <div className="card-label">Fall Detection</div>
      {alert ? (
        <div className="fall-alert-text">⚠ FALL DETECTED</div>
      ) : (
        <div className="fall-ok">✓ Normal</div>
      )}
    </div>
  );
}

function ConnectionPill({ source }) {
  if (source === "wifi")
    return (
      <div className="conn-pill wifi">
        <span className="conn-dot" />
        📡 Wi-Fi
      </div>
    );
  if (source === "ble")
    return (
      <div className="conn-pill ble">
        <span className="conn-dot" />
        🔵 BLE
      </div>
    );
  return (
    <div className="conn-pill search">
      <span className="conn-dot" />
      Searching...
    </div>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [data, setData] = useState(null);
  const [fps, setFps] = useState(0);
  const [clock, setClock] = useState("");
  const fpsCountRef = useRef(0);

  // canvas refs
  const accCanvas = useRef(null);
  const gCanvas = useRef(null);

  // chart push functions
  const pushAcc = useLiveChart(accCanvas, [
    makeDataset("AccX", "#ef4444"),
    makeDataset("AccY", "#22c55e"),
    makeDataset("AccZ", "#3b82f6"),
  ]);
  const pushG = useLiveChart(gCanvas, [makeDataset("G-Total", "#a855f7")]);

  // Clock
  useEffect(() => {
    const id = setInterval(() => {
      setClock(new Date().toLocaleTimeString("en-GB", { hour12: false }));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  // FPS counter
  useEffect(() => {
    const id = setInterval(() => {
      setFps(fpsCountRef.current);
      fpsCountRef.current = 0;
    }, 1000);
    return () => clearInterval(id);
  }, []);

  // Socket.IO
  useEffect(() => {
    const socket = io(SOCKET_URL); // allow polling + websocket (default)
    socket.on("sensor_update", (d) => {
      fpsCountRef.current++;
      setData(d);
      pushAcc([parseFloat(d.accX), parseFloat(d.accY), parseFloat(d.accZ)]);
      pushG([parseFloat(d.gTotal)]);
    });
    return () => socket.disconnect();
  }, [pushAcc, pushG]);

  const temp = data ? parseFloat(data.temp).toFixed(1) : null;
  const gTotal = data ? parseFloat(data.gTotal).toFixed(3) : null;
  const moisture = data ? parseInt(data.moisture) : null;

  return (
    <div className="layout">
      {/* Header */}
      <header className="header">
        <div className="header-brand">
          <div className="header-logo">SC</div>
          <div>
            <div className="header-title">SoterCare</div>
            <div className="header-sub">Thigh Node Monitor</div>
          </div>
        </div>
        <div className="header-right">
          <ConnectionPill source={data?.source} />
          <span className="fps-badge">{fps} fps</span>
          <span className="header-clock">{clock}</span>
        </div>
      </header>

      {/* Content */}
      <div className="content">
        {/* Left: charts */}
        <div className="charts-col">
          <div className="card chart-card" style={{ flex: 2 }}>
            <div className="card-label">
              Acceleration (g) &nbsp;
              <span style={{ color: "#ef4444", fontWeight: 700 }}>X</span>
              {" / "}
              <span style={{ color: "#22c55e", fontWeight: 700 }}>Y</span>
              {" / "}
              <span style={{ color: "#3b82f6", fontWeight: 700 }}>Z</span>
            </div>
            <div className="chart-wrap">
              <canvas ref={accCanvas} />
            </div>
          </div>

          <div className="card chart-card" style={{ flex: 1 }}>
            <div className="card-label">
              G-Total &nbsp;
              <span
                style={{
                  fontVariantNumeric: "tabular-nums",
                  color: "#a855f7",
                  fontWeight: 700,
                }}
              >
                {gTotal ?? "—"} g
              </span>
            </div>
            <div className="chart-wrap">
              <canvas ref={gCanvas} />
            </div>
          </div>
        </div>

        {/* Right: stat cards */}
        <div className="stats-col">
          <StatCard
            label="Skin Temperature"
            value={temp}
            unit="°C"
            sub="MLX90614 object temp"
            variant="amber"
          />
          <MoistureCard moisture={moisture} />
          <RSSICard rssi={data?.rssi} source={data?.source} />
          <GaitCard label={data?.gaitLabel} />
          <FallCard alert={data?.fallAlert === "1"} />
          <StatCard
            label="Last Packet"
            value={
              data?.ts
                ? new Date(parseFloat(data.ts) * 1000).toLocaleTimeString()
                : null
            }
            sub={`Source: ${data?.source || "—"}`}
            variant=""
          />
        </div>
      </div>
    </div>
  );
}
