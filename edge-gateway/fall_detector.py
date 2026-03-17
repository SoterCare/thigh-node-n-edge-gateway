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
import math
import logging
import collections
from enum import Enum, auto
from typing import Optional, Tuple

log = logging.getLogger("fall_detector")


# ── Tunable parameters ─────────────────────────────────────────────────────────
# ── Tunable parameters (Refined for Thigh-based detection) ───────────────
IMPACT_THRESHOLD      = 2.5    # g   — lowered from 3.0 to catch "slump" falls
IMPACT_MAX            = 12.0   # g   — above this = sensor error, skip
FREE_FALL_THRESHOLD   = 0.5    # g   — weightless state before impact
QUIET_LOW             = 0.6    # g   — below this = sensor off-body / error
QUIET_HIGH            = 1.4    # g   — lowered for stricter quiet-period
QUIET_DURATION        = 1.0    # s   — shortened from 1.2 for faster detection
IMPACT_TO_QUIET_WINDOW = 2.0   # s   — tight window for valid fall
FALL_COOLDOWN         = 30.0   # s   — no repeat alert for this long
POST_IMPACT_STABILITY  = 0.35  # s   — window to compute stable "quiet" vector

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

        # History and Kinematics
        self._history       = collections.deque(maxlen=75) # 1.5s at 50Hz
        self._pre_impact_vec: Optional[Tuple[float, float, float]] = None
        self._free_fall_seen: bool = False

        # Cooldown
        self._last_alert_t  : float = 0.0

    # ── Public API ─────────────────────────────────────────────────────────────
    def update(self, ax: float, ay: float, az: float, ts: float) -> tuple[bool, str]:
        """
        Feed one IMU frame with 3-axis vectors.

        Parameters
        ----------
        ax, ay, az : float — raw acceleration axes in g-units
        ts         : float — frame timestamp (time.time())

        Returns
        -------
        (alert: bool, info: str)
          alert=True  once per confirmed fall (not on every quiet frame).
        """
        g_total = math.sqrt(ax**2 + ay**2 + az**2)
        
        # Maintain history buffer for pre-impact analysis
        self._history.append((ax, ay, az, g_total, ts))

        if self._state == FallState.IDLE:
            return self._idle(ax, ay, az, g_total, ts)

        elif self._state == FallState.IMPACT_DETECTED:
            return self._impact_phase(ax, ay, az, g_total, ts)

        elif self._state == FallState.FALL_CONFIRMED:
            return self._cooldown_phase(ts)

        return False, ""

    # ── State handlers ─────────────────────────────────────────────────────────
    def _idle(self, ax: float, ay: float, az: float, g: float, ts: float) -> tuple[bool, str]:
        """IDLE: watch for a valid impact spike."""
        if IMPACT_THRESHOLD <= g <= IMPACT_MAX:
            self._state       = FallState.IMPACT_DETECTED
            self._impact_time = ts
            self._impact_peak = g
            self._quiet_start = 0.0
            self._quiet_active = False
            
            # Analyze pre-impact history
            history_list = list(self._history)
            
            # 1. Free-fall check: any sample below the threshold in recent history
            self._free_fall_seen = any(item[3] < FREE_FALL_THRESHOLD for item in history_list)
            
            # 2. Compute pre-impact baseline orientation
            # We look at the oldest 20-40% of the buffer to get a stable pre-fall baseline
            if len(history_list) > 20:
                baseline_end = int(len(history_list) * 0.4)
                baseline_samples = [history_list[i] for i in range(baseline_end)]
                avg_ax = sum(item[0] for item in baseline_samples) / len(baseline_samples)
                avg_ay = sum(item[1] for item in baseline_samples) / len(baseline_samples)
                avg_az = sum(item[2] for item in baseline_samples) / len(baseline_samples)
                self._pre_impact_vec = (avg_ax, avg_ay, avg_az)
            else:
                self._pre_impact_vec = (ax, ay, az)
                
            log.debug(f"[FallDetector] Impact {g:.2f}g. FreeFall: {self._free_fall_seen}")
        return False, ""

    def _impact_phase(self, ax: float, ay: float, az: float, g: float, ts: float) -> tuple[bool, str]:
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
                post_vec = (ax, ay, az)
                return self._confirm_fall(ts, quiet_duration, post_vec)
        else:
            # G left the quiet band → reset quiet counter
            self._quiet_active = False
            self._quiet_start  = 0.0

        return False, ""

    def _confirm_fall(self, ts: float, quiet_dur: float, post_vec: tuple[float, float, float]) -> tuple[bool, str]:
        """Verify posture and transition to FALL_CONFIRMED and emit alert."""
        # Respect cooldown from previous alert
        if ts - self._last_alert_t < FALL_COOLDOWN:
            remaining = FALL_COOLDOWN - (ts - self._last_alert_t)
            log.debug(f"[FallDetector] Fall confirmed but in cooldown ({remaining:.0f}s left).")
            self._state = FallState.IDLE
            return False, ""

        # ── Posture angle check ────────────────────────────────────────────
        p_vec = self._pre_impact_vec
        if p_vec is not None:
            v1: Tuple[float, float, float] = p_vec
            v2: Tuple[float, float, float] = post_vec
            mag1 = math.sqrt(v1[0]**2 + v1[1]**2 + v1[2]**2)
            mag2 = math.sqrt(v2[0]**2 + v2[1]**2 + v2[2]**2)
            if mag1 > 0 and mag2 > 0:
                dot = v1[0]*v2[0] + v1[1]*v2[1] + v1[2]*v2[2]
                cos_theta = max(-1.0, min(1.0, dot / (mag1 * mag2)))
                angle_deg = math.degrees(math.acos(cos_theta))
            else:
                angle_deg = 0.0
        else:
            angle_deg = 90.0 # fallback if no history
            
        # ── Rejection rules ────────────────────────────────────────────────
        if angle_deg < 45.0:
            log.debug(f"[FallDetector] Rejecting: Posture angle change ({angle_deg:.1f}°) < 45° (Not horizontal)")
            self._state = FallState.IDLE
            return False, ""
            
        if not self._free_fall_seen and self._impact_peak < 4.0:
             log.debug(f"[FallDetector] Rejecting: No free-fall and moderate impact {self._impact_peak:.1f}g (Likely heavy sitting)")
             self._state = FallState.IDLE
             return False, ""

        self._state       = FallState.FALL_CONFIRMED
        self._last_alert_t = ts
        info = (
            f"Impact {self._impact_peak:.2f}g -> "
            f"quiet {quiet_dur:.1f}s -> "
            f"angle {angle_deg:.0f} deg -> "
            f"FALL CONFIRMED"
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
        res: str = str(self._state.name)
        return res

    def reset(self):
        """Force-reset to IDLE (e.g. on reconnect)."""
        self.__init__()
