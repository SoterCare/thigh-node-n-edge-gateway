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
from typing import Optional, Tuple, List, cast

log = logging.getLogger("fall_detector")


# ── Tunable parameters (Refined for Thigh-based Acc+Gyro Fusion) ────────
ACC_IMPACT_THRESHOLD  = 2.8    # g   — SVM_acc spike
GYRO_IMPACT_THRESHOLD = 300.0  # deg/s — AVM_gyro spike (rotation during fall)
ACC_MAX               = 12.0   # g   — sensor limit / error check
FREE_FALL_THRESHOLD   = 0.5    # g   — weightless state before impact

# Stillness (Quiet Period) thresholds
STILL_ACC_LOW         = 0.7    # g
STILL_ACC_HIGH        = 1.3    # g
STILL_GYRO_MAX        = 40.0   # deg/s — very low rotation means lying still
STILL_DURATION        = 1.2    # s     — sustained stillness required

IMPACT_WINDOW         = 2.0    # s   — time allowed to find quiet after impact
FALL_COOLDOWN         = 30.0   # s
POSTURE_CHANGE_MIN    = 40.0   # deg — Orientation shift

# ── State machine states ───────────────────────────────────────────────────────
class FallState(Enum):
    IDLE            = auto()   # Normal monitoring
    IMPACT_DETECTED = auto()   # High-G spike seen, watching for quiet
    FALL_CONFIRMED  = auto()   # Fall confirmed, cooldown active


class FallDetector:
    """
    Enhanced research-backed Fall Detector using Acc+Gyro fusion.
    Optimized for thigh-placed sensors.
    """

    def __init__(self):
        self._state         : FallState = FallState.IDLE
        self._impact_time   : float = 0.0
        self._impact_peak   : float = 0.0

        self._quiet_start   : float = 0.0
        self._quiet_active  : bool  = False

        # 1.5s history @ 50Hz. Stores (ax, ay, az, gx, gy, gz, svm_acc, avm_gyro, ts)
        self._history       = collections.deque(maxlen=75)
        self._pre_impact_vec: Optional[Tuple[float, float, float]] = None
        self._free_fall_seen: bool = False
        self._last_alert_t  : float = 0.0

    def update(self, ax: float, ay: float, az: float, gx: float, gy: float, gz: float, ts: float) -> tuple[bool, str]:
        """Feed 6-axis IMU frame. (ax/ay/az in g, gx/gy/gz in deg/s)."""
        svm_acc  = math.sqrt(ax**2 + ay**2 + az**2)
        avm_gyro = math.sqrt(gx**2 + gy**2 + gz**2)
        
        self._history.append((ax, ay, az, gx, gy, gz, svm_acc, avm_gyro, ts))
        
        if self._state == FallState.IDLE:
            return self._idle_phase(ax, ay, az, gx, gy, gz, svm_acc, avm_gyro, ts)
        elif self._state == FallState.IMPACT_DETECTED:
            return self._impact_phase(ax, ay, az, gx, gy, gz, svm_acc, avm_gyro, ts)
        elif self._state == FallState.FALL_CONFIRMED:
            return self._cooldown_phase(ts)
            
        return False, ""

    def _idle_phase(self, ax: float, ay: float, az: float, gx: float, gy: float, gz: float, 
                    svm_acc: float, avm_gyro: float, ts: float) -> tuple[bool, str]:
        """Trigger on concurrent Acc spike and orientation change (Gyro)."""
        if svm_acc > ACC_MAX: return False, ""

        if svm_acc > ACC_IMPACT_THRESHOLD:
            # Check for Free Fall or Rotation Peak
            history_list = list(self._history)
            
            # A. Free Fall check (last 400ms / 20 samples)
            history_len = len(history_list)
            ff_start = max(0, history_len - 20)
            ff_samples = [history_list[i] for i in range(ff_start, history_len)]
            ff_seen = any(h[6] < FREE_FALL_THRESHOLD for h in ff_samples)
            
            # B. Rotation check (is there a concurrent rotation peak?)
            rot_start = max(0, history_len - 10)
            rot_samples = [history_list[i] for i in range(rot_start, history_len)]
            gyro_peak = max(h[7] for h in rot_samples)
            
            if gyro_peak > GYRO_IMPACT_THRESHOLD or ff_seen:
                self._state          = FallState.IMPACT_DETECTED
                self._impact_time    = ts
                self._impact_peak    = svm_acc
                self._quiet_active   = False
                self._free_fall_seen  = ff_seen
                
                # Baseline calculation (oldest 30% of buffer)
                if len(history_list) > 20:
                    base_end = int(len(history_list) * 0.3)
                    base = [history_list[i] for i in range(base_end)]
                    self._pre_impact_vec = (
                        sum(h[0] for h in base) / len(base),
                        sum(h[1] for h in base) / len(base),
                        sum(h[2] for h in base) / len(base)
                    )
                else:
                    self._pre_impact_vec = (ax, ay, az)

                log.debug(f"[Fall] IMPACT: {svm_acc:.1f}g, Rot: {gyro_peak:.0f}deg/s, FF: {ff_seen}")
                
        return False, ""

    def _impact_phase(self, ax: float, ay: float, az: float, gx: float, gy: float, gz: float, 
                      svm_acc: float, avm_gyro: float, ts: float) -> tuple[bool, str]:
        """Verify stillness after impact."""
        elapsed = ts - self._impact_time
        if elapsed > IMPACT_WINDOW:
            log.debug("[Fall] Aborted - Stillness window timeout.")
            self._state = FallState.IDLE
            return False, ""

        if svm_acc > self._impact_peak: self._impact_peak = svm_acc

        # Stillness condition: gravity-only Acc and minimal rotation
        is_still = (STILL_ACC_LOW < svm_acc < STILL_ACC_HIGH) and (avm_gyro < STILL_GYRO_MAX)

        if is_still:
            if not self._quiet_active:
                self._quiet_start = ts
                self._quiet_active = True
            
            if ts - self._quiet_start >= STILL_DURATION:
                return self._confirm_fall(ts, ts - self._quiet_start, (ax, ay, az))
        else:
            self._quiet_active = False
            
        return False, ""

    def _confirm_fall(self, ts: float, quiet_dur: float, post_vec: tuple[float, float, float]) -> tuple[bool, str]:
        if ts - self._last_alert_t < FALL_COOLDOWN:
            self._state = FallState.IDLE
            return False, ""

        # Posture check
        angle_deg = 0.0
        p_vec = self._pre_impact_vec
        if p_vec is not None:
            v1, v2 = p_vec, post_vec
            mag1 = math.sqrt(float(v1[0])**2 + float(v1[1])**2 + float(v1[2])**2)
            mag2 = math.sqrt(float(v2[0])**2 + float(v2[1])**2 + float(v2[2])**2)
            if mag1 > 0.1 and mag2 > 0.1:
                dot = float(v1[0])*float(v2[0]) + float(v1[1])*float(v2[1]) + float(v1[2])*float(v2[2])
                cos_theta = max(-1.0, min(1.0, dot / (mag1 * mag2)))
                angle_deg = math.degrees(math.acos(cos_theta))

        # Rejection Rules
        if angle_deg < POSTURE_CHANGE_MIN:
            log.debug(f"[Fall] Rejected - No posture change ({angle_deg:.1f} deg)")
            self._state = FallState.IDLE
            return False, ""

        # Stricter rule for low-impact cases
        if not self._free_fall_seen and self._impact_peak < 3.2:
             log.debug(f"[Fall] Rejected - Moderate impact ({self._impact_peak:.1f}g) without FF")
             self._state = FallState.IDLE
             return False, ""

        self._state       = FallState.FALL_CONFIRMED
        self._last_alert_t = ts
        
        inten = "HIGH" if self._impact_peak > 6.0 else ("LOW" if self._impact_peak < 3.5 else "MED")
        info = f"FALL! {inten} IMPACT ({self._impact_peak:.1f}g), Rotation Δ, Posture Δ ({angle_deg:.0f}°)"
        log.warning(info)
        print(f"\n[ALERT] {info}")
        return True, info

    def _cooldown_phase(self, ts: float) -> tuple[bool, str]:
        if ts - self._last_alert_t >= FALL_COOLDOWN:
            self._state = FallState.IDLE
        return False, ""

    @property
    def state(self) -> str:
        return str(self._state.name)

    def reset(self):
        self.__init__()
