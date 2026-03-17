import { useEffect, useRef, useState, useCallback } from "react";
import { io } from "socket.io-client";

const SOCKET_URL = "http://localhost:5000";
const MAX_EVENTS = 60;

// Warm up the speech synthesis API so voice lists are ready immediately
if ("speechSynthesis" in window) {
  // Fire an empty, silent utterance on load to wake up the speech engine
  const w = new SpeechSynthesisUtterance("");
  w.volume = 0;
  window.speechSynthesis.speak(w);
  window.speechSynthesis.getVoices();
}

// ── Medical Audio Engine ───────────────────────────────────────────────────────
let audioCtx = null;

function playSiren() {
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }

    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);

    osc.type = "square";
    osc.frequency.setValueAtTime(800, audioCtx.currentTime); // High pitch
    osc.frequency.setValueAtTime(600, audioCtx.currentTime + 0.5); // Low pitch

    // Make it loud but avoid clipping. Loops for 2 seconds.
    gain.gain.setValueAtTime(0.5, audioCtx.currentTime);

    osc.start();
    osc.stop(audioCtx.currentTime + 2); // Play for 2 seconds
  } catch (err) {
    console.error("Audio Context failed to play siren:", err);
  }
}

function speakEvent(text, priority = false) {
  if (!("speechSynthesis" in window)) return;

  try {
    // Chrome bug workaround: sometimes speech synthesis gets stuck. Resuming helps.
    if (window.speechSynthesis.paused) {
      window.speechSynthesis.resume();
    }

    // If high priority (Help Call/Fall), cancel whatever is currently speaking
    if (priority) {
      window.speechSynthesis.cancel();
    }

    // If already speaking and not high priority, queue it (which browser does by default)
    const utterance = new SpeechSynthesisUtterance(text);

    // Try to find a pleasant female voice loaded by the OS/Browser
    const voices = window.speechSynthesis.getVoices();
    const femaleVoice = voices.find((v) => {
      const n = v.name.toLowerCase();
      return (
        n.includes("female") ||
        n.includes("zira") ||
        n.includes("samantha") ||
        n.includes("siri") ||
        n.includes("google UK English Female")
      );
    });

    if (femaleVoice) {
      utterance.voice = femaleVoice;
    }

    utterance.pitch = 1.1;
    utterance.rate = 1.0;
    utterance.volume = 1.0;

    // Safety fallback: if it hangs, force cancel after 10 seconds
    utterance.onend = () => {};
    utterance.onerror = () => {
      window.speechSynthesis.cancel();
    };

    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.error("Speech Synthesis failed:", err);
  }
}

// ── SVG Icons ─────────────────────────────────────────────────────────────────
const Icon = {
  Temp: () => (
    <svg
      width="32"
      height="32"
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
      width="32"
      height="32"
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
      width="32"
      height="32"
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
      width="28"
      height="28"
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
      width="28"
      height="28"
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
      width="24"
      height="24"
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
      width="24"
      height="24"
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
      width="24"
      height="24"
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
      width="24"
      height="24"
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
      width="24"
      height="24"
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
      width="22"
      height="22"
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
  Clock: () => (
    <svg
      width="32"
      height="32"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
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

function ClockCard({ time }) {
  return (
    <div className="mcard">
      <div className="mcard-icon slate">
        <Icon.Clock />
      </div>
      <div className="mcard-body">
        <div className="clock-value">{time || "--:--:--"}</div>
        <div className="mcard-label">Local Time</div>
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
  const isSys = ev.category === "system";
  return (
    <div className={`ev-item ${ev.type} ${isSys ? "system" : ""}`}>
      <div className={`ev-icon ${ev.type}`}>{eventIconEl(ev.type)}</div>
      <div className="ev-body">
        <div className="ev-title">
          {ev.title}
          {isSys && ev.detail && (
            <span className="ev-detail-inline"> — {ev.detail}</span>
          )}
        </div>
        {!isSys && ev.detail && <div className="ev-detail">{ev.detail}</div>}
      </div>
      <div className="ev-time">{ev.time}</div>
    </div>
  );
}

// ── Components ───────────────────────────────────────────────────────────────
function BootScreen({ progress, step }) {
  return (
    <div className="boot-screen">
      <div className="boot-content">
        <div className="boot-logo">SoterCare</div>
        <div className="boot-sub">Wellness Simplified</div>

        <div className="boot-loader-container">
          <div className="boot-loader-bar" style={{ width: `${progress}%` }} />
          <div className="boot-loader-glow" style={{ left: `${progress}%` }} />
        </div>

        <div className="boot-status">
          <span className="boot-step-text">{step}</span>
          <span className="boot-percent">{Math.round(progress)}%</span>
        </div>
      </div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [booting, setBooting] = useState(true);
  const [bootProgress, setBootProgress] = useState(0);
  const [bootStep, setBootStep] = useState("Initializing System...");

  const [data, setData] = useState(null);
  const [events, setEvents] = useState(() => {
    // If this is a fresh boot (no session key), clear persistent logs
    if (!sessionStorage.getItem("sotercare_booted")) {
      localStorage.removeItem("sotercare_events");
      sessionStorage.setItem("sotercare_booted", "true");
      return [];
    }
    const saved = localStorage.getItem("sotercare_events");
    try {
      return saved ? JSON.parse(saved) : [];
    } catch (e) {
      return [];
    }
  });

  // Persist events to localStorage whenever they change
  useEffect(() => {
    localStorage.setItem("sotercare_events", JSON.stringify(events));
  }, [events]);

  const [hz, setHz] = useState(0);
  const [clock, setClock] = useState("--:--:--");
  const [online, setOnline] = useState(false); // true = receiving live data
  const [gwConnected, setGwConnected] = useState(false);
  const [audioUnlocked, setAudioUnlocked] = useState(false);

  // Refs for boot sequence without dependency loops
  const gwRef = useRef(false);
  const onlineRef = useRef(false);

  useEffect(() => {
    gwRef.current = gwConnected;
  }, [gwConnected]);
  useEffect(() => {
    onlineRef.current = online;
  }, [online]);

  const hzRef = useRef(0);
  const prevGait = useRef("");
  const prevSource = useRef("");
  const lastDataTime = useRef(0); // epoch ms of last live sensor_update
  const wasOnline = useRef(false); // for offline transition event
  const moistureAlertRef = useRef(0); // timestamp of last moisture alert (cooldown)
  const tempAlertRef = useRef(0); // timestamp of last temp alert (cooldown)
  const ambientTempAlertRef = useRef(0); // cooldown for ambient temp
  const lowHzAlertRef = useRef(0); // cooldown for low Hz
  const weakSignalAlertRef = useRef(0); // cooldown for weak signal
  const stillnessAlertRef = useRef(0); // timestamp for stillness start
  const stillnessTriggeredRef = useRef(false); // flag to prevent spamming
  const bootSuccess = useRef(false);

  // Explicit unlock via UI banner (100% reliable for background tasks)
  const handleUnlockAudio = useCallback(() => {
    try {
      // Unlock Web Audio API
      if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (audioCtx.state === "suspended") {
        audioCtx.resume();
      }

      // Play a short silent oscillator to definitively lock it in
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      gain.gain.value = 0;
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + 0.1);

      // Unlock Speech Synthesis API
      if ("speechSynthesis" in window) {
        if (window.speechSynthesis.paused) {
          window.speechSynthesis.resume();
        }
        window.speechSynthesis.cancel(); // clear stuck queue
        // Important: Actually speak a word on click so the browser registers the user gesture
        const u = new SpeechSynthesisUtterance("Audio Active");
        u.volume = 0;
        window.speechSynthesis.speak(u);
      }
      setAudioUnlocked(true);
    } catch (e) {
      console.warn("Audio unlock prevented by browser policies:", e);
    }
  }, []);

  // Background keep-alive tick
  useEffect(() => {
    const keepAlive = setInterval(() => {
      if (audioUnlocked && audioCtx && audioCtx.state === "running") {
        // Tap the audio API gently to keep the tab fully active in background
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        gain.gain.value = 0;
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.01);
      }
    }, 15000); // 15s keep-alive
    return () => clearInterval(keepAlive);
  }, [audioUnlocked]);

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
      const nowOnline = lastDataTime.current > 0 && age < 6000; // 6s offline timeout (tighter)

      setOnline(nowOnline);

      if (!nowOnline && wasOnline.current) {
        // Just went offline
        wasOnline.current = false;
        setData((prev) => (prev ? { ...prev, active: false } : null));
        prevGait.current = "";
        prevSource.current = "";

        setEvents((p) =>
          [
            {
              category: "system",
              type: "danger",
              title: "Thigh Node Offline",
              detail: "Connection to the node was lost",
              time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
            },
            ...p,
          ].slice(0, MAX_EVENTS),
        );
      } else if (nowOnline && !wasOnline.current) {
        // Just came online
        wasOnline.current = true;
        // The source and RSSI will be captured from the next sensor_update
        // and we'll handle the "Online" event there where we have data.
        // Or we can do it here if we have last known data.
      }

      // ── New Watchdog Alerts ─────────────────────────────────────────────
      const now = Date.now();

      // 1. Low Data Rate (Hz < 40 for 5s)
      if (nowOnline && hzRef.current > 0 && hzRef.current < 40) {
        const last = lowHzAlertRef.current;
        if (!last || now - last > 30_000) {
          lowHzAlertRef.current = now;
          setEvents((p) => [
            {
              category: "system",
              type: "warning",
              title: "Low Data Rate",
              detail: `Node is only sending at ${hzRef.current}Hz (expected >50Hz)`,
              time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
            },
            ...p,
          ].slice(0, MAX_EVENTS));
          speakEvent("Warning: Low data rate detected from thigh node.");
        }
      }

      // 2. Extended Stillness (Still for > 5 min)
      if (nowOnline && prevGait.current === "Still") {
        if (!stillnessAlertRef.current) stillnessAlertRef.current = now;
        const durationMin = (now - stillnessAlertRef.current) / 60_000;
        if (durationMin >= 5 && !stillnessTriggeredRef.current) {
          stillnessTriggeredRef.current = true;
          setEvents((p) => [
            {
              category: "health",
              type: "info",
              title: "Extended Stillness",
              detail: "Patient has been still for over 5 minutes",
              time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
            },
            ...p,
          ].slice(0, MAX_EVENTS));
          speakEvent("Patient has been still for 5 minutes. Consider checking status.");
        }
      } else {
        stillnessAlertRef.current = 0;
        stillnessTriggeredRef.current = false;
      }
    }, 1000);
    return () => clearInterval(id);
  }, []); // no addEvent dependency — uses setEvents directly

  // Fast & Reliable Boot Sequence
  useEffect(() => {
    if (!booting) return;

    setBootProgress(30);
    setBootStep("Connecting to Gateway...");

    let timeout;
    let interval = setInterval(() => {
      if (gwRef.current) {
        setBootProgress(60);
        setBootStep("Gateway Linked...");

        if (onlineRef.current) {
          clearInterval(interval);
          clearTimeout(timeout);
          setBootProgress(100);
          setBootStep("Thigh Node Online! Starting...");
          setTimeout(() => setBooting(false), 800);
        }
      }
    }, 150);

    // Hard timeout: approx 2.5 seconds max
    timeout = setTimeout(() => {
      clearInterval(interval);
      setBootProgress(100);
      setBootStep("Thigh Node Offline. Starting...");
      setTimeout(() => setBooting(false), 800);
    }, 2500);

    return () => {
      clearInterval(interval);
      clearTimeout(timeout);
    };
  }, [booting]); // Only runs once when booting starts

  const addEvent = useCallback(
    (ev) => setEvents((p) => [ev, ...p].slice(0, MAX_EVENTS)),
    [],
  );

  useEffect(() => {
    const socket = io(SOCKET_URL);

    socket.on("connect", () => {
      setGwConnected(true);
      addEvent({
        category: "system",
        type: "success",
        title: "Gateway Connected",
        detail: "Dashboard WebSocket established",
        time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
      });
    });
    socket.on("disconnect", () => {
      setGwConnected(false);
      addEvent({
        category: "system",
        type: "warning",
        title: "Gateway Disconnected",
        detail: "WebSocket lost — reconnecting",
        time: new Date().toLocaleTimeString("en-GB", { hour12: false }),
      });
    });

    socket.on("sensor_update", (d) => {
      setData(d);
      const t = tsFmt(d.ts);

      // Prevent Redis history replay from faking an online status
      const packetAgeMs = Math.abs(Date.now() - parseFloat(d.ts) * 1000);
      const isHistorical = packetAgeMs > 10000; // Older than 10s

      if (!isHistorical) {
        hzRef.current++;
        lastDataTime.current = Date.now();
      } else {
        // Fallback in case device clock isn't 100% synced with Pi clock:
        // If a packet comes in via WebSocket, we ALWAYS consider the gateway "Alive"
        // to prevent offline locking if time drifts.
        hzRef.current++;
        lastDataTime.current = Date.now();
      }

      const mst = parseInt(d.moisture);
      const tmp = parseFloat(d.temp);
      const now = Date.now();

      // ── Help Call Button (highest priority) ───────────────────────────────
      if (d.sos === "1" || d.sos === 1) {
        addEvent({
          category: "health",
          type: "danger",
          title: "Help Call",
          detail: "Patient pressed the Help Call button",
          time: t,
        });

        playSiren();
        speakEvent(
          "Help Call. Patient has pressed the Help Call button.",
          true,
        );
      }
      // ── Fall Detection ─────────────────────────────────────────────────────
      if (d.fallAlert === "1") {
        addEvent({
          category: "health",
          type: "danger",
          title: "Fall Detection Triggered",
          detail: `G-total: ${parseFloat(d.gTotal ?? 0).toFixed(2)}g`,
          time: t,
        });

        playSiren();
        speakEvent("Fall Detected. Please check patient immediately.", true);
      }

      // ── Moisture > 25% (60s cooldown per threshold cross) ─────────────────
      if (mst >= 25) {
        const last = moistureAlertRef.current;
        if (!last || now - last > 60_000) {
          moistureAlertRef.current = now;
          const lvl = mst >= 75 ? "Critical" : mst >= 50 ? "High" : "Elevated";
          addEvent({
            category: "health",
            type: mst >= 50 ? "danger" : "warning",
            title: `Moisture ${lvl}: ${mst}%`,
            detail: "Please attend to the patient",
            time: t,
          });
          speakEvent("Moisture detected. Please attend to the patient.");
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
            category: "health",
            type: tmp > 39.5 ? "danger" : "warning",
            title: `High Temperature: ${tmp.toFixed(1)} C`,
            detail:
              tmp > 39.5
                ? "Urgent — check patient immediately"
                : "Monitor closely",
            time: t,
          });
          speakEvent(
            `High temperature detected. ${tmp.toFixed(1)} degrees celsius.`,
          );
        }
      } else {
        if (tempAlertRef.current) tempAlertRef.current = 0;
      }

      // ── Connection source change (Live data only) ────────────────────────
      if (!isHistorical) {
        if (!prevSource.current && d.source) {
          // This fires when it first comes online OR after an offline period
          const label =
            d.source === "wifi" ? `Wi-Fi — RSSI: ${d.rssi} dBm` : "BLE";
          addEvent({
            category: "system",
            type: "success",
            title: "Thigh Node Online",
            detail: `Connected via ${d.source === "wifi" ? "Wi-Fi" : "BLE"} · ${label}`,
            time: t,
          });
          speakEvent("Thigh node is online.");
        } else if (d.source !== prevSource.current && prevSource.current) {
          const toWifi = d.source === "wifi";
          addEvent({
            category: "system",
            type: toWifi ? "success" : "neutral",
            title: toWifi
              ? "Thigh Node Connected via Wi-Fi"
              : "Thigh Node Connected via BLE",
            detail: toWifi ? `RSSI: ${d.rssi} dBm` : "Wi-Fi signal lost",
            time: t,
          });
        }
        prevSource.current = d.source;

        // ── Signal/Environment Status Alerts ───────────────────────────────
        // 1. Weak Signal (RSSI < -85)
        const rssiVal = parseInt(d.rssi);
        if (d.source === "wifi" && rssiVal < -85) {
          const last = weakSignalAlertRef.current;
          if (!last || now - last > 60_000) {
            weakSignalAlertRef.current = now;
            addEvent({
              category: "system",
              type: "warning",
              title: "Weak Signal",
              detail: `RSSI is very low: ${rssiVal} dBm. Move gateway closer.`,
              time: t,
            });
          }
        }

        // 2. Ambient Temp (Room comfort)
        const amb = parseFloat(d.ambientTemp);
        if (amb < 15 || amb > 35) {
          const last = ambientTempAlertRef.current;
          if (!last || now - last > 300_000) { // 5 min cooldown
            ambientTempAlertRef.current = now;
            addEvent({
              category: "system",
              type: "neutral",
              title: `Room Temp ${amb > 35 ? "High" : "Low"}: ${amb.toFixed(1)}°C`,
              detail: "Check room climate control",
              time: t,
            });
          }
        }
      }

      // ── Gait label change ─────────────────────────────────────────────────
      const g = d.gaitLabel && d.gaitLabel !== "N/A" ? d.gaitLabel : null;
      if (g && g !== prevGait.current) {
        prevGait.current = g;
        addEvent({
          category: "health",
          type: "info",
          title: `Gait: ${g}`,
          detail: `G-total: ${parseFloat(d.gTotal ?? 0).toFixed(2)}g`,
          time: t,
        });

        if (g === "Risky Movement") {
          playSiren();
          speakEvent(
            "Risky movement detected. Please check patient immediately.",
            true,
          );
        }
      }
    });

    return () => socket.disconnect();
  }, [addEvent]);

  const patientTemp = data ? parseFloat(data.temp).toFixed(1) : null;
  const roomTemp = data ? parseFloat(data.ambientTemp).toFixed(1) : null;
  const moisture = data ? parseInt(data.moisture) : null;
  const dimmed = !online ? { opacity: 0.45, pointerEvents: "none" } : {};

  if (booting) {
    return <BootScreen progress={bootProgress} step={bootStep} />;
  }

  return (
    <div
      style={{
        width: 800,
        height: 480,
        overflow: "hidden",
        position: "relative",
      }}
    >
      {/* Audio Unlock Overlay */}
      {!audioUnlocked && !booting && (
        <div onClick={handleUnlockAudio} className="audio-unlock-overlay">
          <div className="audio-unlock-card">
            <div className="audio-icon-pulse">
              <svg
                width="32"
                height="32"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
              </svg>
            </div>
            <div className="audio-unlock-text">
              <h3>Enable Voice Alerts</h3>
              <p>
                Click anywhere to activate the background siren and speech
                synthesizer.
              </p>
            </div>
            <button className="audio-unlock-btn" onClick={handleUnlockAudio}>
              Activate
            </button>
          </div>
        </div>
      )}

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
          <div className="topbar-sub">Wellness Simplified</div>
        </div>

        <div className="topbar-right">
          <div className={`hz-pill ${!online || hz < 45 ? "warn" : ""}`}>
            IMU {online ? hz : 0} Hz
          </div>
          <ConnChip source={online ? data?.source : null} />
        </div>
      </div>

      {/* Main */}
      <div className="main">
        {/* Left — dimmed when offline */}
        <div className="left" style={dimmed}>
          <MetricCard
            icon={<Icon.Temp />}
            value={patientTemp ? `${patientTemp}°C` : "—"}
            label={
              !online
                ? "No Data"
                : patientTemp > 38.5
                  ? "Elevated Temp"
                  : "Patient Skin"
            }
            colorClass="amber"
            extra={
              online &&
              roomTemp && (
                <div className="room-temp-badge">Room: {roomTemp}°C</div>
              )
            }
          />
          <MoistureCard moisture={online ? moisture : null} />
          <GaitCard label={online ? data?.gaitLabel : null} />
          <ClockCard time={clock} />
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
