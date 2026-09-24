import base64
import csv
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
import math
from queue import Queue, Empty
import threading
import time
import zipfile

import numpy as np
from PIL import Image

from . import __version__
from .analysis import analyze
from .report import details_text, inspection_png, measurement, preview_levels
from .cameras.aravis import Aravis, AravisCamera, CameraError
from .cameras.demo import DemoCamera
from .cameras.replay import ReplayCamera
from .session import Recorder, SessionLog, list_recordings
from .uncertainty import DEFAULTS as UNCERTAINTY_DEFAULTS, FIELDS, MIN_SAMPLES, UncertaintyWindow


# Auto exposure aims for a peak well below saturation and accepts a band around it.
AUTO_TARGET, AUTO_BAND, AUTO_MAX_STEPS, AUTO_MAX_EXPOSURE_US = .75, (.6, .9), 8, 1_000_000


@dataclass(frozen=True)
class Snapshot:
    pixels: np.ndarray
    metrics: dict
    profile_x: np.ndarray
    profile_y: np.ndarray
    packet: dict
    camera: dict
    settings: dict
    dark: np.ndarray | None
    uncertainty_samples: list


class Profiler:
    """One acquisition thread owns every native call; HTTP only reads snapshots."""
    def __init__(self, sessions_dir=None):
        self.api = None
        self.sessions_dir = sessions_dir
        self.log = SessionLog(sessions_dir)
        self.recorder = None
        self.recordings = {}
        self._quality = None
        self.camera = None
        self.snapshot = None
        self.dark = None
        self.dark_warning = None
        self.commands = Queue()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.settings = {"pixel_pitch_um": None, "magnification": 1., "roi": None,
                         "subtract_border": True, "noise_sigma": 3.}
        self.uncertainty_settings = dict(UNCERTAINTY_DEFAULTS)
        self.uncertainty = UncertaintyWindow({**self.uncertainty_settings, **self.settings})
        self.state = {"connected": False, "paused": False, "camera": None, "error": None,
                      "frame_count": 0, "fps": 0., "dark_active": False, "devices": [],
                      "driver_error": None, "recording": None, "auto_exposure": None,
                      "session": self.log.directory.name if self.log.directory else None,
                      "log_error": self.log.error}
        self.log.add("session_start", f"Beam profiler {__version__} session started.",
                     session_folder=str(self.log.directory) if self.log.directory else None)
        self.thread = threading.Thread(target=self._run, name="camera-acquisition", daemon=True)
        self.thread.start()

    def request(self, action, data=None):
        future = Future()
        self.commands.put((action, data or {}, future))
        return future.result(timeout=45)

    def status(self):
        with self.lock:
            return {**self.state, "log_seq": self.log.seq, "settings": dict(self.settings),
                    "uncertainty_settings": dict(self.uncertainty_settings)}

    def packet(self):
        with self.lock:
            return self.snapshot.packet if self.snapshot else None

    def _update(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)

    def _invalidate(self, dark=True):
        self._reset_statistics()
        with self.lock:
            self.snapshot = None
        if dark:
            self._set_dark(None)
            self._update(dark_active=False)

    def _reset_statistics(self, reason="Measurement conditions changed."):
        self.uncertainty.reset(reason, {**self.uncertainty_settings, **self.settings})

    def _replaying(self):
        return bool(self.camera and self.camera.info.get("driver") == "replay")

    def _stop_recording(self, reason):
        if self.recorder:
            try:
                result = self.recorder.stop(reason)
                self.log.add("recording_stop", f"Recording {result['name']} stopped after {result['frames']} frames: {reason}.",
                             **result, reason=reason)
            finally:
                self.recorder = None
                self._update(recording=None)
                self.recordings = list_recordings(self.sessions_dir)
                with self.lock:
                    self.state["devices"] = [d for d in self.state["devices"] if d.get("driver") != "replay"] + \
                        [r["device"] for r in self.recordings.values()]

    def _disconnect(self):
        if self.camera:
            self.log.add("disconnect", f"Disconnected {self.camera.info.get('model', 'camera')}.")
        try:
            self._stop_recording("camera disconnected")
            if self.camera:
                self.camera.close()
        finally:
            self.camera = None
            self._invalidate()
            self._update(connected=False, paused=False, camera=None, fps=0.)

    def _execute(self, action, data):
        if action == "scan":
            if self.camera:
                raise ValueError("Disconnect before rescanning cameras.")
            devices = []
            try:
                if self.api is None:
                    self.api = Aravis()
                devices = self.api.discover()
                self._update(driver_error=None)
            except (CameraError, OSError) as error:
                self._update(driver_error=str(error))
            devices.append({"id": "demo", "model": "Gaussian beam simulator", "vendor": "Demo",
                            "serial": "SIMULATED", "transport": "Synthetic", "driver": "demo",
                            "available": True})
            self.recordings = list_recordings(self.sessions_dir)
            devices += [r["device"] for r in self.recordings.values()]
            self._update(devices=devices)
            self.log.add("scan", f"Found {len(devices)} sources ({len(self.recordings)} recordings).",
                         devices=[d["id"] for d in devices], driver_error=self.state["driver_error"])
        elif action == "connect":
            device_id = data.get("id")
            if not isinstance(device_id, str) or not device_id:
                raise ValueError("Choose a camera first.")
            if device_id.startswith("replay:") and device_id not in self.recordings:
                raise ValueError("Unknown recording; rescan sources.")
            self._disconnect()
            if device_id == "demo":
                camera = DemoCamera()
            elif device_id.startswith("replay:"):
                camera = ReplayCamera(self.recordings[device_id]["path"])
            else:
                if self.api is None:
                    self.api = Aravis()
                camera = AravisCamera(self.api)
            try:
                info = camera.open(device_id)
            except Exception:
                camera.close()
                raise
            self.camera = camera
            with self.lock:
                self.settings.update(pixel_pitch_um=info.get("pixel_pitch_um"), roi=None)
                # A new camera has no established pitch uncertainty. An uncertainty
                # entered for a different sensor must not silently follow it.
                self.uncertainty_settings["pixel_pitch_u_um"] = None
            self._reset_statistics("Camera connected; collecting a new window.")
            self._update(connected=True, paused=False, camera=info, error=None, frame_count=0)
            self._quality = None
            self.log.add("connect", f"Connected {info.get('vendor', '')} {info.get('model', '')} · {info.get('serial', '')}"
                         + (" (replay)" if device_id.startswith("replay:") else "") + ".", camera=info)
            if device_id.startswith("replay:"):
                # Start from the recorded analysis settings; they remain editable.
                try:
                    self._execute("analysis", {k: v for k, v in camera.settings.items() if k in self.settings})
                except (ValueError, TypeError) as error:
                    self.log.add("warning", f"Recorded analysis settings not applied: {error}")
        elif action == "disconnect":
            self._disconnect()
        elif action == "pause":
            self._require_camera()
            if not isinstance(data.get("paused"), bool):
                raise ValueError("paused must be true or false.")
            if self.state["paused"] and not data["paused"]:
                self._reset_statistics("Acquisition resumed; collecting a new window.")
            if self.state["paused"] != data["paused"]:
                self.log.add("pause" if data["paused"] else "resume", "Frame frozen." if data["paused"] else "Acquisition resumed.",
                             frame=self.snapshot.packet["timestamp"] if self.snapshot and data["paused"] else None)
            self._update(paused=data["paused"])
        elif action == "configure":
            self._require_camera()
            exposure_us, gain_db = float(data["exposure_us"]), float(data["gain_db"])
            rejected = False
            try:
                self.camera.configure(exposure_us, gain_db)
            except ValueError:
                # Out-of-range input is rejected before any camera write; keep
                # the dark reference and statistics that still match the camera.
                rejected = True
                raise
            finally:
                if not rejected:
                    self._invalidate()
                    self._update(camera=dict(self.camera.info), paused=False)
                    self.log.add("configure", f"Camera set to {self.camera.info['exposure_us']:.6g} µs exposure, "
                                 f"{self.camera.info['gain_db']:.4g} dB gain (requested {exposure_us:g} µs, {gain_db:g} dB); "
                                 "dark reference cleared.", requested={"exposure_us": exposure_us, "gain_db": gain_db},
                                 actual={"exposure_us": self.camera.info["exposure_us"], "gain_db": self.camera.info["gain_db"]})
        elif action == "analysis":
            new = dict(self.settings)
            if "pixel_pitch_um" in data:
                value = data["pixel_pitch_um"]
                new["pixel_pitch_um"] = None if value is None else self._number(value, .001, 1000, "Pixel pitch")
            if "magnification" in data:
                new["magnification"] = self._number(data["magnification"], .001, 10000, "Magnification")
            if "noise_sigma" in data:
                new["noise_sigma"] = self._number(data["noise_sigma"], 0, 10, "Noise floor")
            if "subtract_border" in data:
                if not isinstance(data["subtract_border"], bool):
                    raise ValueError("subtract_border must be true or false.")
                new["subtract_border"] = data["subtract_border"]
            if "roi" in data:
                roi = data["roi"]
                if roi is not None:
                    self._require_camera()
                    if not isinstance(roi, list) or len(roi) != 4 or any(type(v) is not int for v in roi):
                        raise ValueError("ROI must contain four integer pixel coordinates.")
                    x0, y0, x1, y1 = roi
                    if not (0 <= x0 < x1 <= self.camera.info["width"] and
                            0 <= y0 < y1 <= self.camera.info["height"] and x1-x0 >= 8 and y1-y0 >= 8):
                        raise ValueError("Analysis region must be within the image and at least 8 × 8 pixels.")
                new["roi"] = roi
            previous = self.settings
            with self.lock:
                self.settings = new
                old = self.snapshot
                if new["pixel_pitch_um"] != previous["pixel_pitch_um"]:
                    self.uncertainty_settings["pixel_pitch_u_um"] = None
                if new["magnification"] != previous["magnification"]:
                    self.uncertainty_settings["magnification_u"] = None
            self._reset_statistics("Analysis/calibration settings changed.")
            changed = {k: v for k, v in new.items() if previous[k] != v}
            if changed:
                self.log.add("analysis", "Analysis settings changed: " +
                             ", ".join(f"{k} = {v}" for k, v in changed.items()) + ".", changed=changed, settings=new)
            if old:
                self._publish(old.pixels, old.metrics["maximum_dn"], old.packet["timestamp"], count=False)
        elif action == "uncertainty":
            new = dict(self.uncertainty_settings)
            if "window_frames" in data:
                n = data["window_frames"]
                if type(n) is not int or not MIN_SAMPLES <= n <= 600:
                    raise ValueError(f"The statistics window must be an integer from {MIN_SAMPLES} to 600 frames.")
                new["window_frames"] = n
            for key, label in [("pixel_pitch_u_um", "Pixel pitch standard uncertainty"),
                               ("magnification_u", "Magnification standard uncertainty")]:
                if key in data:
                    value = data[key]
                    new[key] = None if value is None else self._number(value, 0, 1000, label)
            if "scale_correlation" in data:
                new["scale_correlation"] = self._number(data["scale_correlation"], -1, 1, "Scale correlation")
            # First-order propagation is unsuitable when the denominator or scale
            # distribution is broad; require a calibrated positive scale first.
            for key, nominal in [("pixel_pitch_u_um", self.settings["pixel_pitch_um"]),
                                 ("magnification_u", self.settings["magnification"])]:
                if new[key] is not None and nominal is None:
                    raise ValueError("Specify the nominal pixel pitch before its uncertainty.")
                if new[key] is not None and new[key] > .1 * nominal:
                    raise ValueError("This linear calibration model requires standard uncertainty ≤10% of the nominal value.")
            with self.lock:
                previous, self.uncertainty_settings = self.uncertainty_settings, new
            self._reset_statistics("Uncertainty settings changed.")
            changed = {k: v for k, v in new.items() if previous.get(k) != v}
            if changed:
                self.log.add("uncertainty", "Uncertainty settings changed: " +
                             ", ".join(f"{k} = {v}" for k, v in changed.items()) + ".", changed=changed, settings=new)
            if self.snapshot:
                old = self.snapshot
                self._publish(old.pixels, old.metrics["maximum_dn"], old.packet["timestamp"], count=False)
        elif action == "reset_statistics":
            self._reset_statistics("Statistics reset by user.")
            self.log.add("reset_statistics", "Uncertainty window reset by user.")
            if self.snapshot:
                old = self.snapshot
                self._publish(old.pixels, old.metrics["maximum_dn"], old.packet["timestamp"], count=False)
        elif action == "restore_snapshot":
            # Internal startup operation only; not exposed by the HTTP API.
            self._restore_snapshot(data["archive"])
            self.log.add("restore_snapshot", f"Restored snapshot frame {self.snapshot.packet['timestamp']}; frozen.",
                         source=data.get("source"))
        elif action == "auto_exposure":
            self._require_camera()
            if self._replaying():
                raise ValueError("Exposure is fixed in a recording.")
            target = self._number(data.get("target_percent", AUTO_TARGET * 100), 20, 95, "Target peak") / 100
            self._auto_exposure(target)
        elif action == "record":
            self._require_camera()
            if not isinstance(data.get("recording"), bool):
                raise ValueError("recording must be true or false.")
            if data["recording"] and not self.recorder:
                if self._replaying():
                    raise ValueError("Recording is unavailable during replay.")
                if self.log.directory is None:
                    raise ValueError("No session folder is available for recordings.")
                self.recorder = Recorder(self.log.directory, self.camera.info, self.settings, self.uncertainty_settings)
                self._update(recording=self.recorder.status())
                self.log.add("recording_start", f"Recording raw frames to {self.recorder.status()['name']}.",
                             **self.recorder.status())
            elif not data["recording"]:
                self._stop_recording("stopped by user")
        elif action == "dark":
            self._require_camera()
            if self._replaying() and not data.get("clear"):
                raise ValueError("A replay uses its recorded dark references.")
            if data.get("clear"):
                self._set_dark(None)
                self.log.add("dark_clear", "Dark reference cleared.")
            else:
                if self.camera.info["exposure_us"] > 1_000_000:
                    raise ValueError("Use an exposure of 1 second or less when capturing a dark reference.")
                # Drain queued frames first so the reference reflects the blocked beam.
                for _ in range(8):
                    self.camera.read()
                frames = [self.camera.read() for _ in range(8)]
                self._set_dark(np.mean([f.pixels.astype(np.float64) for f in frames], axis=0), frames[-1].maximum)
                self.log.add("dark_capture", f"Dark reference captured: mean of 8 frames, mean level {float(self.dark.mean()):.4g} DN.",
                             exposure_us=self.camera.info["exposure_us"], gain_db=self.camera.info["gain_db"])
            self._invalidate(dark=False)
            self._update(dark_active=self.dark is not None, paused=False)
        else:
            raise ValueError("Unknown action.")
        return self.status()

    def _restore_snapshot(self, archive_bytes):
        """Restore an exported frame and reference without treating it as a sample."""
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Snapshot exceeds the 128 MiB restore limit.")
            metadata = json.loads(archive.read("measurement.json"))
            pixels = np.array(Image.open(BytesIO(archive.read("raw.tiff"))))
            dark = (np.array(Image.open(BytesIO(archive.read("dark-reference.tiff"))))
                    if "dark-reference.tiff" in archive.namelist() else None)
        if pixels.ndim != 2 or pixels.dtype not in (np.dtype('uint8'),np.dtype('uint16')):
            raise ValueError("Snapshot must contain a native monochrome image.")
        if dark is not None and (dark.shape != pixels.shape or not np.isfinite(dark).all()):
            raise ValueError("Invalid snapshot dark reference.")
        maximum = metadata["metrics"]["maximum_dn"]
        if maximum not in (255,1023,4095,65535) or pixels.max() > maximum:
            raise ValueError("Invalid snapshot intensity scale.")
        timestamp = metadata["timestamp"]
        datetime.fromisoformat(timestamp)
        info = metadata["camera"]
        try:
            self._execute("connect", {"id":info["id"]})
            if (self.camera.info["height"],self.camera.info["width"]) != pixels.shape:
                raise ValueError("Snapshot size differs from the connected camera.")
            self._execute("configure", {key:info[key] for key in ["exposure_us","gain_db"]})
            self._execute("analysis", metadata["settings"])
            if "uncertainty" in metadata:
                previous = metadata["uncertainty"]
                calibration = previous.get("calibration", {})
                self._execute("uncertainty", {"window_frames":previous["window_frames"],
                    "pixel_pitch_u_um":calibration.get("pixel_pitch_u_um"),
                    "magnification_u":calibration.get("magnification_u"),
                    "scale_correlation":calibration.get("correlation",0.)})
            self._set_dark(dark, maximum)
            self._update(paused=True, dark_active=dark is not None)
            self._reset_statistics("Snapshot restored; resume to collect fresh uncertainty samples.")
            self._publish(pixels, maximum, timestamp, count=False)
        except Exception:
            self._disconnect()
            raise

    @staticmethod
    def _robust_peak(pixels):
        # The 20th-brightest pixel: isolated hot pixels cannot set the exposure.
        flat = pixels.ravel()
        k = min(20, flat.size)
        return float(np.partition(flat, flat.size - k)[flat.size - k])

    def _auto_exposure(self, target=None):
        """One-shot: iterate exposure at fixed gain until the ROI peak is 60–90% of full scale."""
        target = target or AUTO_TARGET
        low, high = self.camera.info["exposure_us_range"]
        high = min(high, AUTO_MAX_EXPOSURE_US)
        gain = self.camera.info["gain_db"]
        exposure = min(max(self.camera.info["exposure_us"], low), high)
        steps, outcome = [], None
        try:
            for _ in range(AUTO_MAX_STEPS):
                self.camera.configure(exposure, gain)
                exposure = self.camera.info["exposure_us"]
                self.camera.read()
                frame = self.camera.read()  # the second frame is fully exposed at the new setting
                x0, y0, x1, y1 = self.settings["roi"] or (0, 0, frame.pixels.shape[1], frame.pixels.shape[0])
                region = frame.pixels[y0:y1, x0:x1]
                peak, offset = self._robust_peak(region), float(np.percentile(region, .5))
                fraction = peak / frame.maximum
                steps.append({"exposure_us": exposure, "peak_percent": round(fraction * 100, 2)})
                if AUTO_BAND[0] <= fraction <= AUTO_BAND[1]:
                    outcome = ("converged", f"Auto exposure: {exposure:.6g} µs at {gain:.4g} dB gives a peak of {fraction:.0%}.")
                    break
                if fraction >= .998:
                    proposed = exposure / 4
                else:
                    # Linear response above the offset; bounded steps absorb nonlinearity and noise.
                    signal = max(peak - offset, .005 * frame.maximum)
                    proposed = exposure * min(10., max(.1, (target * frame.maximum - offset) / signal))
                # Whole microseconds: cameras quantize exposure anyway, and the value stays readable.
                proposed = min(max(float(round(proposed)), low), high)
                if abs(proposed - exposure) <= 1e-6 * exposure:
                    advice = ("still saturated at the shortest exposure: add ND or lower the gain" if fraction >= .998
                              else "signal too weak at the longest auto exposure: raise the gain or the power")
                    outcome = ("limited", f"Auto exposure stopped at {exposure:.6g} µs, peak {fraction:.0%}: {advice}.")
                    break
                exposure = proposed
            else:
                outcome = ("not_converged", f"Auto exposure did not settle in {AUTO_MAX_STEPS} steps; "
                           f"last {exposure:.6g} µs, peak {steps[-1]['peak_percent']:.0f}%. Is the signal changing?")
        finally:
            # Exposure changed: the dark reference and statistics no longer apply.
            self._invalidate()
            self._update(camera=dict(self.camera.info), paused=False,
                         auto_exposure={"status": outcome[0] if outcome else "failed",
                                        "message": outcome[1] if outcome else "Auto exposure failed.",
                                        "target_percent": target * 100, "steps": steps})
            if outcome:
                self.log.add("auto_exposure", outcome[1] + " Dark reference cleared.", status=outcome[0],
                             target_percent=target * 100, gain_db=gain, steps=steps)

    def _set_dark(self, dark, maximum=None):
        """Use a dark reference, checking it for beam light that would bias every measurement."""
        self.dark, self.dark_warning = dark, None
        if dark is None:
            return
        check, _, _ = analyze(dark, maximum or float(dark.max()) or 1.)
        if check["valid"]:
            self.dark_warning = (f"Dark reference contains a beam-like signal (peak {check['peak_dn']} DN); "
                                 "block the beam and recapture it.")
            self.log.add("warning", self.dark_warning, dark_centroid_px=[check["centroid_x_px"], check["centroid_y_px"]])

    def _require_camera(self):
        if not self.camera:
            raise ValueError("Connect a camera first.")

    @staticmethod
    def _number(value, low, high, label):
        number = float(value)
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f"{label} must be between {low:g} and {high:g}.")
        return number

    def _publish(self, pixels, maximum, timestamp=None, count=True):
        settings = dict(self.settings)
        metrics, px, py = analyze(pixels, maximum, dark=self.dark, **settings)
        if self.dark is not None and self.dark_warning:
            metrics["warnings"].append(self.dark_warning)
        # Preview is display-only: all measurements above use the full native array.
        display = Image.fromarray(preview_levels(pixels, maximum))
        display.thumbnail((960, 720))
        out = BytesIO()
        display.save(out, format="PNG")
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        uncertainty = self.uncertainty.observe(metrics, timestamp, add=count)
        with self.lock:
            frame_id = self.state["frame_count"] + int(count)
        def profile(values, start):
            indices = np.linspace(0, len(values) - 1, min(480, len(values))).astype(int)
            return {"position": (indices + start).tolist(), "intensity": values[indices].tolist()}
        packet = {"id": frame_id, "timestamp": timestamp, "width": pixels.shape[1], "height": pixels.shape[0],
                  "preview_width": display.width, "preview_height": display.height,
                  "image": base64.b64encode(out.getvalue()).decode(), "metrics": metrics,
                  "profile_x": profile(px, metrics["roi"][0]), "profile_y": profile(py, metrics["roi"][1]),
                  "uncertainty": uncertainty,
                  "simulated": self.camera.info["simulated"], "replay": self.camera.info.get("replay")}
        snapshot = Snapshot(pixels, metrics, px, py, packet, dict(self.camera.info), settings, self.dark,
                            self.uncertainty.rows())
        with self.lock:
            self.snapshot = snapshot
            self.state["frame_count"] = frame_id
            self.state["error"] = None
        if count:
            self._log_quality(metrics)
        return timestamp

    def _log_quality(self, metrics):
        # Log changes of image-quality state, at most every 2 s so flapping cannot flood the log.
        state = tuple(metrics["warnings"]) if metrics["valid"] else ("No clear beam signal.",)
        now = time.monotonic()
        if self._quality is None or (state != self._quality[0] and now - self._quality[1] >= 2):
            self._quality = (state, now)
            self.log.add("quality", "Image quality: " + (" ".join(state) if state else "valid beam, no warnings."),
                         valid=metrics["valid"], warnings=list(metrics["warnings"]))

    def _run(self):
        last_frame = time.monotonic()
        failures = 0
        try:
            while not self.stop_event.is_set():
                try:
                    action, data, future = self.commands.get(timeout=0 if self.camera else .1)
                except Empty:
                    pass
                else:
                    try:
                        result = self._execute(action, data)
                        failures = 0
                        future.set_result(result)
                    except Exception as error:
                        self.log.add("error", f"{action} failed: {error}", action=action)
                        future.set_exception(error)
                if not self.camera:
                    continue
                try:
                    frame = self.camera.read()
                    failures = 0
                    if self._replaying():
                        if frame.restart:
                            self._reset_statistics("Replay restarted from the first recorded frame.")
                            self.log.add("replay_start", f"Replay started at recorded frame 1 of {len(self.camera.rows)}.")
                        if frame.dark is not self.dark:
                            self._set_dark(frame.dark, frame.maximum)
                            self._update(dark_active=frame.dark is not None)
                    if not self.state["paused"]:
                        timestamp = self._publish(frame.pixels, frame.maximum, frame.timestamp)
                        if self.recorder:
                            reason = self.recorder.add(frame.pixels, frame.maximum, timestamp, self.camera.info, self.dark)
                            if reason:
                                self._stop_recording(reason)
                            else:
                                self._update(recording=self.recorder.status())
                        now = time.monotonic()
                        instant = 1 / max(now - last_frame, .001)
                        self._update(fps=round(.8 * self.state["fps"] + .2 * instant, 1))
                        last_frame = now
                except Exception as error:
                    failures += 1
                    self._reset_statistics("Frame acquisition failed; collecting a new window.")
                    self._update(error=str(error))
                    self.log.add("acquisition_error", f"Frame acquisition failed ({failures}/3): {error}")
                    if failures >= 3:
                        self.log.add("error", "Three consecutive acquisition failures; disconnecting.")
                        self._disconnect()
                    self.stop_event.wait(.15)
        finally:
            self._disconnect()

    def _exported_snapshot(self):
        with self.lock:
            snap = self.snapshot
        if snap is None:
            raise ValueError("No frame is available to export.")
        return snap

    def inspection(self, palette="thermal"):
        """Inspection PNG of the same frame and statistics an export would contain."""
        snap = self._exported_snapshot()
        png = inspection_png(snap, palette)
        self.log.add("save_png", f"Inspection PNG saved for frame {snap.packet['timestamp']}.", palette=palette)
        return png

    def export(self, palette="thermal"):
        snap = self._exported_snapshot()
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            raw = BytesIO()
            Image.fromarray(snap.pixels).save(raw, format="TIFF")
            archive.writestr("raw.tiff", raw.getvalue())
            archive.writestr("measurement.json", json.dumps(measurement(snap), indent=2))
            archive.writestr("details.txt", details_text(snap, palette))
            archive.writestr("inspection.png", inspection_png(snap, palette))
            self.log.add("export", f"Raw data exported for frame {snap.packet['timestamp']}.", palette=palette)
            archive.writestr("session-log.jsonl", self.log.jsonl())
            samples = StringIO(newline="")
            writer = csv.DictWriter(samples, fieldnames=["timestamp", *FIELDS])
            writer.writeheader()
            writer.writerows(snap.uncertainty_samples)
            archive.writestr("uncertainty-samples.csv", samples.getvalue())
            if snap.dark is not None:
                reference = BytesIO()
                Image.fromarray(snap.dark.astype(np.float32)).save(reference, format="TIFF")
                archive.writestr("dark-reference.tiff", reference.getvalue())
            for axis, values, start in [("x", snap.profile_x, snap.metrics["roi"][0]),
                                        ("y", snap.profile_y, snap.metrics["roi"][1])]:
                profile_csv = "position_px,integrated_intensity_dn\n" + "\n".join(
                    f"{i+start},{value:.8g}" for i, value in enumerate(values))
                archive.writestr(f"profile_{axis}.csv", profile_csv)
        return out.getvalue()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=40)
