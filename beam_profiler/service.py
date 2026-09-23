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

from .analysis import analyze
from .cameras.aravis import Aravis, AravisCamera, CameraError
from .cameras.demo import DemoCamera
from .uncertainty import DEFAULTS as UNCERTAINTY_DEFAULTS, FIELDS, MIN_SAMPLES, UncertaintyWindow


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
    def __init__(self):
        self.api = None
        self.camera = None
        self.snapshot = None
        self.dark = None
        self.commands = Queue()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.settings = {"pixel_pitch_um": None, "magnification": 1., "roi": None,
                         "subtract_border": True, "noise_sigma": 3.}
        self.uncertainty_settings = dict(UNCERTAINTY_DEFAULTS)
        self.uncertainty = UncertaintyWindow({**self.uncertainty_settings, **self.settings})
        self.state = {"connected": False, "paused": False, "camera": None, "error": None,
                      "frame_count": 0, "fps": 0., "dark_active": False, "devices": [],
                      "driver_error": None}
        self.thread = threading.Thread(target=self._run, name="camera-acquisition", daemon=True)
        self.thread.start()

    def request(self, action, data=None):
        future = Future()
        self.commands.put((action, data or {}, future))
        return future.result(timeout=45)

    def status(self):
        with self.lock:
            return {**self.state, "settings": dict(self.settings),
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
            self.dark = None
            self._update(dark_active=False)

    def _reset_statistics(self, reason="Measurement conditions changed."):
        self.uncertainty.reset(reason, {**self.uncertainty_settings, **self.settings})

    def _disconnect(self):
        try:
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
            self._update(devices=devices)
        elif action == "connect":
            device_id = data.get("id")
            if not isinstance(device_id, str) or not device_id:
                raise ValueError("Choose a camera first.")
            self._disconnect()
            if device_id == "demo":
                camera = DemoCamera()
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
        elif action == "disconnect":
            self._disconnect()
        elif action == "pause":
            self._require_camera()
            if not isinstance(data.get("paused"), bool):
                raise ValueError("paused must be true or false.")
            if self.state["paused"] and not data["paused"]:
                self._reset_statistics("Acquisition resumed; collecting a new window.")
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
                self.uncertainty_settings = new
            self._reset_statistics("Uncertainty settings changed.")
            if self.snapshot:
                old = self.snapshot
                self._publish(old.pixels, old.metrics["maximum_dn"], old.packet["timestamp"], count=False)
        elif action == "reset_statistics":
            self._reset_statistics("Statistics reset by user.")
            if self.snapshot:
                old = self.snapshot
                self._publish(old.pixels, old.metrics["maximum_dn"], old.packet["timestamp"], count=False)
        elif action == "restore_snapshot":
            # Internal startup operation only; not exposed by the HTTP API.
            self._restore_snapshot(data["archive"])
        elif action == "dark":
            self._require_camera()
            if data.get("clear"):
                self.dark = None
            else:
                if self.camera.info["exposure_us"] > 1_000_000:
                    raise ValueError("Use an exposure of 1 second or less when capturing a dark reference.")
                # Drain queued frames first so the reference reflects the blocked beam.
                for _ in range(8):
                    self.camera.read()
                frames = [self.camera.read().pixels.astype(np.float64) for _ in range(8)]
                self.dark = np.mean(frames, axis=0)
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
            self.dark = dark
            self._update(paused=True, dark_active=dark is not None)
            self._reset_statistics("Snapshot restored; resume to collect fresh uncertainty samples.")
            self._publish(pixels, maximum, timestamp, count=False)
        except Exception:
            self._disconnect()
            raise

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
        # Preview is display-only: all measurements above use the full native array.
        display = Image.fromarray((pixels.astype(np.float32) / maximum * 255).clip(0, 255).astype(np.uint8))
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
                  "simulated": self.camera.info["simulated"]}
        snapshot = Snapshot(pixels, metrics, px, py, packet, dict(self.camera.info), settings, self.dark,
                            self.uncertainty.rows())
        with self.lock:
            self.snapshot = snapshot
            self.state["frame_count"] = frame_id
            self.state["error"] = None

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
                        future.set_exception(error)
                if not self.camera:
                    continue
                try:
                    frame = self.camera.read()
                    failures = 0
                    if not self.state["paused"]:
                        self._publish(frame.pixels, frame.maximum)
                        now = time.monotonic()
                        instant = 1 / max(now - last_frame, .001)
                        self._update(fps=round(.8 * self.state["fps"] + .2 * instant, 1))
                        last_frame = now
                except Exception as error:
                    failures += 1
                    self._reset_statistics("Frame acquisition failed; collecting a new window.")
                    self._update(error=str(error))
                    if failures >= 3:
                        self._disconnect()
                    self.stop_event.wait(.15)
        finally:
            self._disconnect()

    def export(self):
        with self.lock:
            snap = self.snapshot
        if snap is None:
            raise ValueError("No frame is available to export.")
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            raw = BytesIO()
            Image.fromarray(snap.pixels).save(raw, format="TIFF")
            archive.writestr("raw.tiff", raw.getvalue())
            archive.writestr("measurement.json", json.dumps({"timestamp": snap.packet["timestamp"],
                "camera": snap.camera, "settings": snap.settings, "metrics": snap.metrics,
                "uncertainty": snap.packet["uncertainty"],
                "dark_active": snap.dark is not None,
                "method": "Thresholded, background-corrected intensity moments; D4sigma diameter"}, indent=2))
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
