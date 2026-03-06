import { useEffect, useRef, useState, useCallback } from "react";
import { io } from "socket.io-client";

const SOCKET_URL = "http://localhost:5000";
const MAX_EVENTS = 60;

// ── SVG Icons ─────────────────────────────────────────────────────────────────
const Icon = {
  Temp: () => (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" />
    </svg>
  ),
  Water: () => (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" />
    </svg>
  ),
  Activity: () => (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  Wifi: () => (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12.55a11 11 0 0 1 14.08 0" />
      <path d="M1.42 9a16 16 0 0 1 21.16 0" />
      <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
      <line x1="12" y1="20" x2="12.01" y2="20" />
    </svg>
  ),
  Bluetooth: () => (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6.5 6.5 17.5 17.5 12 23 12 1 17.5 6.5 6.5 17.5" />
    </svg>
  ),
  Alert: () => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="white"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  ),
  Walk: () => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="white"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="4" r="1" />
      <path d="M9 20l3-8 3 8" />
      <path d="M6 12l3-3 1 2 3-2 1 3" />
    </svg>
  ),
  Link: () => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="white"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  ),
  Info: () => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="white"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  ),
  Check: () => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="white"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  ),
  Search: () => (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  ),
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function rssiToLevel(rssi) {
  if (!rssi || rssi === "N/A") return 0;
  const v = parseInt(rssi);
  if (v >= -50) return 4;
  if (v >= -65) return 3;
  if (v >= -80) return 2;
  return 1;
}
function tsFmt(ts) {
  return new Date(parseFloat(ts) * 1000).toLocaleTimeString("en-GB", {
    hour12: false,
  });
}

// ── Sub-components ────────────────────────────────────────────────────────────
function MetricCard({ icon, value, unit, label, colorClass, extra }) {
  return (
    <div className="mcard">
      <div className={`mcard-icon ${colorClass}`}>{icon}</div>
      <div className="mcard-body">
        <div className={`mcard-value ${colorClass}`}>
          {value ?? "—"}
          {unit && <span className="mcard-unit">{unit}</span>}
        </div>
        <div className="mcard-label">{label}</div>
        {extra}
      </div>
    </div>
  );
}

function MoistureCard({ moisture }) {
  const pct = moisture ?? 0;
  const label = pct > 80 ? "High — check" : pct < 10 ? "Dry" : "Normal";
  return (
    <div className="mcard">
      <div className="mcard-icon teal">
        <Icon.Water />
      </div>
      <div className="mcard-body">
        <div className="mcard-value teal">
          {pct}
          <span className="mcard-unit">%</span>
        </div>
        <div className="mcard-label">Moisture · {label}</div>
        <div className="moist-bar">
          <div className="moist-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

function GaitCard({ label }) {
  return (
    <div className="mcard">
      <div className="mcard-icon slate">
        <Icon.Activity />
      </div>
      <div className="mcard-body">
        <span className="gait-label">{label || "No Data"}</span>
        <div className="mcard-label" style={{ marginTop: 6 }}>
          Gait Analysis
        </div>
      </div>
    </div>
  );
}

function SignalBars({ rssi, source }) {
  const level = rssiToLevel(rssi);
  const heights = [5, 8, 11, 15];
  return (
    <div className="sig-bars">
      {heights.map((h, i) => (
        <div
          key={i}
          className={`sig-bar ${i < level ? "on" : ""}`}
          style={{ height: h }}
        />
      ))}
    </div>
  );
}

function ConnChip({ source }) {
  if (source === "wifi")
    return (
      <div className="conn-chip">
        <span className="chip-dot" />
        <Icon.Wifi />
        Wi-Fi
      </div>
    );
  if (source === "ble")
    return (
      <div
        className="conn-chip"
        style={{
          background: "#ebf8ff",
          color: "#2b6cb0",
          borderColor: "#bee3f8",
        }}
      >
        <span className="chip-dot" />
        <Icon.Bluetooth />
        BLE
      </div>
    );
  return (
    <div className="conn-chip searching">
      <span className="chip-dot" />
      <Icon.Search />
      Searching
    </div>
  );
}

function eventIconEl(type) {
  const icons = {
    danger: <Icon.Alert />,
    warning: <Icon.Alert />,
    info: <Icon.Walk />,
    success: <Icon.Check />,
    neutral: <Icon.Link />,
  };
  return icons[type] || <Icon.Info />;
}

function EventItem({ ev }) {
  return (
    <div className={`ev-item ${ev.type}`}>
      <div className={`ev-icon ${ev.type}`}>{eventIconEl(ev.type)}</div>
      <div className="ev-body">
        <div className="ev-title">{ev.title}</div>
        {ev.detail && <div className="ev-detail">{ev.detail}</div>}
      </div>
      <div className="ev-time">{ev.time}</div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [data, setData] = useState(null);
  const [events, setEvents] = useState([]);
  const [hz, setHz] = useState(0);
  const [clock, setClock] = useState("--:--:--");
  const [online, setOnline] = useState(false); // true = receiving data

  const hzRef = useRef(0);
  const prevGait = useRef("");
  const prevSource = useRef("");
  const lastDataTime = useRef(0); // epoch ms of last sensor_update
  const wasOnline = useRef(false); // for offline transition event
  const moistureAlertRef = useRef(0); // timestamp of last moisture alert (cooldown)
  const tempAlertRef = useRef(0); // timestamp of last temp alert (cooldown)

  useEffect(() => {
    const id = setInterval(
      () => setClock(new Date().toLocaleTimeString("en-GB", { hour12: false })),
      1000,
    );
    return () => clearInterval(id);
  }, []);

  // Hz + offline watchdog — runs every second
  useEffect(() => {
    const id = setInterval(() => {
      setHz(hzRef.current);
      hzRef.current = 0;

      const age = Date.now() - lastDataTime.current;
      const nowOnline = lastDataTime.current > 0 && age < 5000;

      setOnline(nowOnline);

      if (!nowOnline && wasOnline.current) {
        // Just went offline
        wasOnline.current = false;
        setData(null);
        prevGait.current = "";
        prevSource.current = "";
        setEvents((p) =>
          [
            {
              type: "danger",
              title: "Thigh Node Offline",
              detail: "No data received for 5 seconds",
              time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
            },
            ...p,
          ].slice(0, MAX_EVENTS),
        );
      } else if (nowOnline && !wasOnline.current) {
        wasOnline.current = true;
      }
    }, 1000);
    return () => clearInterval(id);
  }, []); // no addEvent dependency — uses setEvents directly

  const addEvent = useCallback(
    (ev) => setEvents((p) => [ev, ...p].slice(0, MAX_EVENTS)),
    [],
  );

  useEffect(() => {
    const socket = io(SOCKET_URL);

    socket.on("connect", () =>
      addEvent({
        type: "success",
        title: "Gateway Connected",
        detail: "Dashboard WebSocket established",
        time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
      }),
    );
    socket.on("disconnect", () =>
      addEvent({
        type: "warning",
        title: "Gateway Disconnected",
        detail: "WebSocket lost — reconnecting",
        time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
      }),
    );

    socket.on("sensor_update", (d) => {
      hzRef.current++;
      lastDataTime.current = Date.now();
      setData(d);

      const t = tsFmt(d.ts);
      const mst = parseInt(d.moisture);
      const tmp = parseFloat(d.temp);
      const now = Date.now();

      // ── SOS Button (highest priority) ─────────────────────────────────────
      if (d.sos === "1" || d.sos === 1) {
        addEvent({
          type: "danger",
          title: "SOS Alert",
          detail: "Patient pressed the SOS button",
          time: t,
        });
      }

      // ── Fall Detection ─────────────────────────────────────────────────────
      if (d.fallAlert === "1") {
        addEvent({
          type: "danger",
          title: "Fall Detected",
          detail: `G-total: ${parseFloat(d.gTotal ?? 0).toFixed(2)}g  ·  Temp: ${tmp.toFixed(1)} C`,
          time: t,
        });
      }

      // ── Moisture > 25% (60s cooldown per threshold cross) ─────────────────
      if (mst >= 25) {
        const last = moistureAlertRef.current;
        if (!last || now - last > 60_000) {
          moistureAlertRef.current = now;
          const lvl = mst >= 75 ? "Critical" : mst >= 50 ? "High" : "Elevated";
          addEvent({
            type: mst >= 50 ? "danger" : "warning",
            title: `Moisture ${lvl}: ${mst}%`,
            detail: "Check sensor placement on thigh",
            time: t,
          });
        }
      } else {
        if (moistureAlertRef.current) moistureAlertRef.current = 0;
      }

      // ── Temperature > 38.5°C (120s cooldown) ──────────────────────────────
      if (tmp > 38.5) {
        const last = tempAlertRef.current;
        if (!last || now - last > 120_000) {
          tempAlertRef.current = now;
          addEvent({
            type: tmp > 39.5 ? "danger" : "warning",
            title: `High Temperature: ${tmp.toFixed(1)} C`,
            detail:
              tmp > 39.5
                ? "Urgent — check patient immediately"
                : "Monitor closely",
            time: t,
          });
        }
      } else {
        if (tempAlertRef.current) tempAlertRef.current = 0;
      }

      // ── Connection source change ───────────────────────────────────────────
      if (!prevSource.current && d.source) {
        // First packet after connect / reconnect from offline
        const label =
          d.source === "wifi" ? `Wi-Fi — RSSI: ${d.rssi} dBm` : "BLE";
        addEvent({
          type: "success",
          title: "Thigh Node Online",
          detail: `Connected via ${d.source === "wifi" ? "Wi-Fi" : "BLE"} · ${label}`,
          time: t,
        });
      } else if (d.source !== prevSource.current && prevSource.current) {
        const toWifi = d.source === "wifi";
        addEvent({
          type: toWifi ? "success" : "neutral",
          title: toWifi ? "Reconnected via Wi-Fi" : "Switched to BLE Fallback",
          detail: toWifi ? `RSSI: ${d.rssi} dBm` : "Wi-Fi signal lost",
          time: t,
        });
      }
      prevSource.current = d.source;

      // ── Gait label change ─────────────────────────────────────────────────
      const g = d.gaitLabel && d.gaitLabel !== "N/A" ? d.gaitLabel : null;
      if (g && g !== prevGait.current) {
        prevGait.current = g;
        addEvent({
          type: "info",
          title: `Gait: ${g}`,
          detail: `G-total: ${parseFloat(d.gTotal ?? 0).toFixed(2)}g`,
          time: t,
        });
      }
    });

    return () => socket.disconnect();
  }, [addEvent]);

  const temp = data ? parseFloat(data.temp).toFixed(1) : null;
  const moisture = data ? parseInt(data.moisture) : null;
  const dimmed = !online ? { opacity: 0.45, pointerEvents: "none" } : {};

  return (
    <div style={{ width: 800, height: 480, overflow: "hidden" }}>
      {/* Top Bar */}
      <div className="topbar">
        <div className="device-status">
          <div className="device-row">
            <span className={online ? "online-dot" : "offline-dot"} />
            <span className="device-name">Thigh Node:</span>
            <span
              className={online ? "online-text" : "offline-text"}
              style={!online ? { fontWeight: 700 } : {}}
            >
              {online ? "Online" : "Offline"}
            </span>
          </div>
          <div className="device-row">
            <SignalBars
              rssi={online ? data?.rssi : null}
              source={data?.source}
            />
            <span style={{ fontSize: 10, color: "var(--text-3)" }}>
              {online && data?.rssi && data.rssi !== "N/A"
                ? `${data.rssi} dBm`
                : "—"}
            </span>
          </div>
        </div>

        <div className="topbar-center">
          <div className="topbar-title">SoterCare</div>
          <div className="topbar-sub">Gait Monitoring Gateway</div>
        </div>

        <div className="topbar-right">
          <div className={`hz-pill ${!online || hz < 45 ? "warn" : ""}`}>
            IMU {online ? hz : 0} Hz
          </div>
          <ConnChip source={online ? data?.source : null} />
          <span className="topbar-clock">{clock}</span>
        </div>
      </div>

      {/* Main */}
      <div className="main">
        {/* Left — dimmed when offline */}
        <div className="left" style={dimmed}>
          <div className="metric-row">
            <MetricCard
              icon={<Icon.Temp />}
              value={temp}
              unit={temp ? " °C" : ""}
              label={
                !online
                  ? "No Data"
                  : temp > 38
                    ? "Elevated Temperature"
                    : "Skin Temperature"
              }
              colorClass="amber"
            />
            <MoistureCard moisture={online ? moisture : null} />
          </div>
          <div className="metric-row">
            <GaitCard label={online ? data?.gaitLabel : null} />
            <MetricCard
              icon={<Icon.Activity />}
              value={
                !online ? "—" : data?.fallAlert === "1" ? "FALL" : "Normal"
              }
              label="Fall Detection"
              colorClass={data?.fallAlert === "1" ? "coral" : "teal"}
            />
          </div>
        </div>

        {/* Right: Timeline */}
        <div className="timeline-panel">
          <div className="timeline-head">
            <span className="timeline-title">Activity Timeline</span>
            <span className="timeline-count">{events.length}</span>
          </div>
          <div className="timeline-list">
            {events.length === 0 ? (
              <div className="empty-state">
                <Icon.Activity />
                Waiting for events…
              </div>
            ) : (
              events.map((ev, i) => <EventItem key={i} ev={ev} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
