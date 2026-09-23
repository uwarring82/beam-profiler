"""Rolling single-frame repeatability and a declared calibration budget.

No s/sqrt(N) is attached to an instantaneous value. The temporal sample
covariance includes beam motion as well as acquisition noise. Common scale
uncertainty is propagated as a rank-one covariance contribution, not as
independent errors on the two diameters. Unknown systematic terms stay unknown.
"""
from collections import deque
from datetime import datetime
import math

import numpy as np


FIELDS = ("diameter_x", "diameter_y", "centroid_x_px", "centroid_y_px",
          "major", "minor", "ellipticity", "angle_deg", "peak_percent", "background_dn")
LENGTHS = {"diameter_x", "diameter_y", "major", "minor"}
MIN_SAMPLES = 20
DEFAULTS = {"window_frames": 60, "pixel_pitch_u_um": None,
            "magnification_u": None, "scale_correlation": 0.}
LIMITATIONS = [
    "Temporal spread includes beam motion, detector noise and changing background; it is not detector noise alone.",
    "Fixed dark-reference error, threshold/ROI bias, pixel response, optical distortion and axis alignment are not quantified.",
    "No confidence level or uncertainty of the mean is inferred from this window.",
]


class UncertaintyWindow:
    def __init__(self, settings=None):
        self.settings = {**DEFAULTS, "pixel_pitch_um": None, "magnification": 1., **(settings or {})}
        self.samples = deque(maxlen=self.settings["window_frames"])
        self.reset_reason = "Waiting for consecutive valid frames."

    def reset(self, reason="Measurement conditions changed.", settings=None):
        if settings is not None:
            self.settings = dict(settings)
        self.samples = deque(maxlen=self.settings["window_frames"])
        self.reset_reason = reason

    def observe(self, metrics, timestamp, *, add=True):
        # Reject contaminated measurements instead of assigning reassuring error
        # bars to a saturated/truncated beam. Gaps break a repeatability run.
        blocked = not metrics["valid"] or bool(metrics["warnings"])
        if blocked:
            self.reset("Image quality prevents a reliable uncertainty estimate.")
            return self.summary(metrics, blocked=True)
        if add and (not self.samples or self.samples[-1]["timestamp"] != timestamp):
            self.samples.append({"timestamp": timestamp,
                                 **{key: float(metrics[key]) for key in FIELDS}})
        return self.summary(metrics)

    def summary(self, metrics, blocked=False):
        n = len(self.samples)
        result = {"method": "rolling_single_frame_covariance_v1", "coverage_factor": 1,
                  "target": "single_frame", "sample_count": n,
                  "window_frames": self.settings["window_frames"], "minimum_samples": MIN_SAMPLES,
                  "status": "blocked" if blocked else "warming_up" if n < MIN_SAMPLES else "ready",
                  "reset_reason": self.reset_reason, "window_start": None, "window_end": None,
                  "duration_s": 0., "fields": {}, "covariance_order": list(FIELDS),
                  "temporal_covariance": None, "calibration_covariance": None,
                  "known_covariance": None, "assumptions": list(LIMITATIONS), "warnings": [],
                  "calibration": self.calibration(metrics), "budget_complete": False}
        if not n:
            return result
        result["window_start"] = self.samples[0]["timestamp"]
        result["window_end"] = self.samples[-1]["timestamp"]
        result["duration_s"] = max(0., (datetime.fromisoformat(result["window_end"]) -
                                       datetime.fromisoformat(result["window_start"])).total_seconds())
        values = np.array([[row[key] for key in FIELDS] for row in self.samples])
        angle_index = FIELDS.index("angle_deg")
        angles = np.deg2rad(values[:, angle_index] * 2)
        resultant = np.mean(np.exp(1j * angles))
        angle_mean = np.rad2deg(np.angle(resultant)) / 2
        # An ellipse orientation is axial: +89° and -89° are two degrees apart.
        values[:, angle_index] = angle_mean + (values[:, angle_index] - angle_mean + 90) % 180 - 90
        means = np.mean(values, axis=0)
        if n < MIN_SAMPLES:
            return result
        covariance = np.cov(values, rowvar=False, ddof=1)
        calibration_vector = np.array([float(metrics[key]) if key in LENGTHS else 0. for key in FIELDS])
        cal_cov = np.outer(calibration_vector, calibration_vector) * result["calibration"]["known_relative_variance"]
        combined = covariance + cal_cov
        # Unresolvable principal axes cannot have a meaningful orientation error.
        gap = values[:, FIELDS.index("major")] - values[:, FIELDS.index("minor")]
        angle_resolved = (float(np.mean(gap)) > max(3 * float(np.std(gap, ddof=1)),
                         .001 * float(np.mean(values[:, FIELDS.index("major")])))) and abs(resultant) >= .8
        if not angle_resolved:
            result["warnings"].append("Principal-axis angle is unresolved for a nearly circular or unstable beam.")
        correlated, drifting, unresolved = [], [], []
        for i, key in enumerate(FIELDS):
            series = values[:, i]
            std = math.sqrt(max(0., float(covariance[i, i])))
            centered = series - np.mean(series)
            sumsq = float(centered @ centered)
            lag1 = float(centered[:-1] @ centered[1:] / sumsq) if sumsq > 0 else None
            # This is a diagnostic of change across the window, not a correction
            # or a claim of a stationarity test with a fixed significance level.
            third = max(2, n // 3)
            shift = float(np.mean(series[-third:]) - np.mean(series[:third]))
            drift_ratio = abs(shift) / std if std > 0 else 0.
            no_variation = std <= 1e-12 * max(1., abs(float(means[i])))
            available = not no_variation and (key != "angle_deg" or angle_resolved)
            if no_variation:
                unresolved.append(key)
            if lag1 is not None and abs(lag1) > 2 / math.sqrt(n):
                correlated.append(key)
            if drift_ratio > 1:
                drifting.append(key)
            unit = metrics["unit"] if key in LENGTHS else "px" if "centroid" in key else "deg" if key == "angle_deg" else "% FS" if key == "peak_percent" else "DN" if key == "background_dn" else "1"
            field_status = "unresolved" if not available else "changing" if drift_ratio > 1 else "estimated"
            result["fields"][key] = {
                "value": metrics[key],
                "mean": (float((means[i]+90)%180-90) if angle_resolved else None) if key == "angle_deg" else float(means[i]),
                "unit": unit, "status": field_status,
                "repeatability_sd": None if key == "angle_deg" and not angle_resolved else std,
                "calibration_u": math.sqrt(max(0.,float(cal_cov[i,i]))),
                "known_standard_u": math.sqrt(max(0.,float(combined[i,i]))) if available else None,
                "calibration_complete": result["calibration"]["complete"] if key in LENGTHS else True,
                "lag1_correlation": lag1, "end_to_start_shift": shift,
                "uncertainty_of_mean": None,
            }
        if correlated:
            result["warnings"].append("Serial correlation detected; samples must not be treated as independent for averaging.")
        if drifting:
            result["status"] = "changing"
            result["warnings"].append("The signal changes across the window; the spread includes this motion/drift.")
        if unresolved:
            result["warnings"].append("Some values show no resolved variation; zero spread is not proof of zero uncertainty.")
        result["correlated_fields"] = correlated
        result["changing_fields"] = drifting
        # Null an unresolved angle row/column instead of exporting a false covariance.
        def matrix(array):
            return [[None if not angle_resolved and (i == angle_index or j == angle_index)
                     else float(value) for j, value in enumerate(row)] for i, row in enumerate(array)]
        result["temporal_covariance"] = matrix(covariance)
        result["calibration_covariance"] = matrix(cal_cov)
        result["known_covariance"] = matrix(combined)
        return result

    def calibration(self, metrics):
        if metrics["unit"] == "px":
            return {"complete": True, "missing": [], "known_relative_variance": 0.,
                    "note": "Pixel units: physical scale calibration does not apply."}
        # Nominal scale inputs are supplied by the acquisition service when
        # analysis settings change; uncertainties use absolute input units.
        p, m = self.settings["pixel_pitch_um"], self.settings["magnification"]
        up, um = self.settings["pixel_pitch_u_um"], self.settings["magnification_u"]
        missing = [key for key in ["pixel_pitch_u_um", "magnification_u"] if self.settings[key] is None]
        rp, rm = (up / p if up is not None else 0.), (um / m if um is not None else 0.)
        rho = self.settings["scale_correlation"]
        variance = max(0., rp*rp + rm*rm - 2*rho*rp*rm)
        return {"complete": not missing, "missing": missing, "known_relative_variance": variance,
                "pixel_pitch_um": p, "magnification": m, "pixel_pitch_u_um": up,
                "magnification_u": um, "correlation": rho,
                "note": "Common scale p/M; unknown contributions are omitted from the partial budget, not assumed exact."}

    def rows(self):
        return [dict(row) for row in self.samples]
