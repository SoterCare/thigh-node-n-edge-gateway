"""
fall_detector.py — Research-based hard fall detection for SoterCare
====================================================================
Algorithm: Two-phase state machine (Impact → Quiet-Period Verification)

Literature basis:
  · Bourke et al. (2006) – "Evaluation of a threshold-based tri-axial
    accelerometer fall detection algorithm", Gait & Posture 26(2):194-199
  · Tong et al. (2013) – "Acceleration-based fall detection using body sensors"
  · Noury et al. (2007) – "Fall detection - principles and methods"

Thigh placement calibration:
  · Resting/walking   : G_total ≈ 1.0 – 1.2 g
  · Running           : G_total ≈ 1.5 – 2.2 g  (peak)
  · Jump landing      : G_total ≈ 2.0 – 2.8 g  (brief, then active motion)
  · Hard fall impact  : G_total ≈ 3.0 – 7.0 g  → immediately transitions to quiet
  · Post-fall lying   : G_total ≈ 0.6 – 1.5 g  (sustained stillness)

False-positive rejection strategy:
  The ONLY true positive path is:
      spike (G > IMPACT_THRESHOLD)
   followed by
      sustained quiet (QUIET_LOW < G < QUIET_HIGH for QUIET_DURATION seconds)
   within IMPACT_TO_QUIET_WINDOW seconds of the spike.
  
  Activities like jumping, vigorous sport, or bumping a hard surface
  never produce that immediate sustained-stillness post-impact.
"""

import time
import logging
from enum import Enum, auto

log = logging.getLogger("fall_detector")


# ── Tunable parameters ─────────────────────────────────────────────────────────
IMPACT_THRESHOLD      = 3.0    # g   — minimum spike to enter IMPACT phase
IMPACT_MAX            = 12.0   # g   — above this = sensor error, skip
QUIET_LOW             = 0.6    # g   — below this = sensor off-body / error
QUIET_HIGH            = 1.5    # g   — above this = continued activity, not quiet
QUIET_DURATION        = 1.2    # s   — how long quiet must be sustained
IMPACT_TO_QUIET_WINDOW = 2.5   # s   — max time from impact to start of quiet
FALL_COOLDOWN         = 30.0   # s   — no repeat alert for this long

# ── State machine states ───────────────────────────────────────────────────────
class FallState(Enum):
    IDLE            = auto()   # Normal monitoring
    IMPACT_DETECTED = auto()   # High-G spike seen, watching for quiet
    FALL_CONFIRMED  = auto()   # Fall confirmed, cooldown active


class FallDetector:
    """
    Thread-safe, O(1) per sample fall detector.

    Usage (called from pipeline thread at ~50Hz):
        fd = FallDetector()
        ...
        alert, info = fd.update(g_total, timestamp)
        if alert:
            print(info)         # e.g. "Impact 4.2g → quiet 1.3s → FALL"
    """

    def __init__(self):
        self._state         : FallState = FallState.IDLE
        self._impact_time   : float = 0.0   # when the impact spike was detected
        self._impact_peak   : float = 0.0   # peak G seen during impact phase

        # Quiet-period tracker
        self._quiet_start   : float = 0.0   # when quiet period began
        self._quiet_active  : bool  = False  # currently in quiet window

        # Cooldown
        self._last_alert_t  : float = 0.0

    # ── Public API ─────────────────────────────────────────────────────────────
    def update(self, g_total: float, ts: float) -> tuple[bool, str]:
        """
        Feed one IMU frame.

        Parameters
        ----------
        g_total : float — magnitude of acceleration vector in g-units
        ts      : float — frame timestamp (time.time())

        Returns
        -------
        (alert: bool, info: str)
          alert=True  once per confirmed fall (not on every quiet frame).
        """
        if self._state == FallState.IDLE:
            return self._idle(g_total, ts)

        elif self._state == FallState.IMPACT_DETECTED:
            return self._impact_phase(g_total, ts)

        elif self._state == FallState.FALL_CONFIRMED:
            return self._cooldown_phase(ts)

        return False, ""

    # ── State handlers ─────────────────────────────────────────────────────────
    def _idle(self, g: float, ts: float) -> tuple[bool, str]:
        """IDLE: watch for a valid impact spike."""
        if IMPACT_THRESHOLD <= g <= IMPACT_MAX:
            self._state       = FallState.IMPACT_DETECTED
            self._impact_time = ts
            self._impact_peak = g
            self._quiet_start = 0.0
            self._quiet_active = False
            log.debug(f"[FallDetector] Impact detected: {g:.2f}g at ts={ts:.3f}")
        return False, ""

    def _impact_phase(self, g: float, ts: float) -> tuple[bool, str]:
        """
        IMPACT DETECTED: check for post-impact quiet period.
        Two rejection paths back to IDLE:
          · Time window expired without quiet (ADL resumed)
          · Another impact spike (double-impact = vigorous ADL, not a fall)
        """
        elapsed = ts - self._impact_time

        # ── Timeout: window passed, no quiet → not a fall ─────────────────
        if elapsed > IMPACT_TO_QUIET_WINDOW:
            log.debug(
                f"[FallDetector] Timeout without quiet ({elapsed:.1f}s). "
                f"Peak was {self._impact_peak:.2f}g — rejecting."
            )
            self._state = FallState.IDLE
            return False, ""

        # ── Track peak G in the impact phase ──────────────────────────────
        if g > self._impact_peak:
            self._impact_peak = g

        # ── Double-spike: a second large impact → probably sport, not fall ─
        if g > IMPACT_THRESHOLD and elapsed > 0.3:
            log.debug(
                f"[FallDetector] Double spike at {elapsed:.2f}s "
                f"({g:.2f}g) — rejecting as ADL."
            )
            self._state = FallState.IDLE
            return False, ""

        # ── Check for quiet period ─────────────────────────────────────────
        if QUIET_LOW <= g <= QUIET_HIGH:
            if not self._quiet_active:
                self._quiet_active = True
                self._quiet_start  = ts
            quiet_duration = ts - self._quiet_start
            if quiet_duration >= QUIET_DURATION:
                return self._confirm_fall(ts, quiet_duration)
        else:
            # G left the quiet band → reset quiet counter
            self._quiet_active = False
            self._quiet_start  = 0.0

        return False, ""

    def _confirm_fall(self, ts: float, quiet_dur: float) -> tuple[bool, str]:
        """Transition to FALL_CONFIRMED and emit alert."""
        # Respect cooldown from previous alert
        if ts - self._last_alert_t < FALL_COOLDOWN:
            remaining = FALL_COOLDOWN - (ts - self._last_alert_t)
            log.debug(f"[FallDetector] Fall confirmed but in cooldown ({remaining:.0f}s left).")
            self._state = FallState.IDLE
            return False, ""

        self._state       = FallState.FALL_CONFIRMED
        self._last_alert_t = ts
        info = (
            f"Impact {self._impact_peak:.2f}g → "
            f"quiet {quiet_dur:.1f}s → FALL CONFIRMED"
        )
        log.warning(f"[FallDetector] {info}")
        print(f"[FALL] {info}")
        return True, info

    def _cooldown_phase(self, ts: float) -> tuple[bool, str]:
        """FALL_CONFIRMED: stay dormant for FALL_COOLDOWN seconds."""
        if ts - self._last_alert_t >= FALL_COOLDOWN:
            log.debug("[FallDetector] Cooldown expired → IDLE")
            self._state = FallState.IDLE
        return False, ""

    # ── Diagnostics ────────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        return self._state.name

    def reset(self):
        """Force-reset to IDLE (e.g. on reconnect)."""
        self.__init__()
