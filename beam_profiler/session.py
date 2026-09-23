"""Session event log and raw-frame recordings for later replay.

Each server run appends events to ``sessions/session-<UTC>/log.jsonl``. A
recording keeps every analyzed live frame as a native TIFF, listed with its
timestamp, acquisition settings, dark reference and SHA-256 in ``frames.csv``,
so the session can be replayed through the same analysis.
"""
from collections import deque
import csv
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import threading

from PIL import Image

from . import __version__

MAX_FRAMES = 5000
MIN_FREE_BYTES = 2 * 1024 ** 3
MANIFEST_FIELDS = ["index", "timestamp", "file", "sha256", "exposure_us", "gain_db",
                   "pixel_format", "maximum_dn", "dark_file"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def tiff_bytes(array):
    out = BytesIO()
    Image.fromarray(array).save(out, format="TIFF")
    return out.getvalue()


class SessionLog:
    """Append-only event log; in memory only when no sessions directory is given."""

    def __init__(self, root=None, keep=2000):
        self.lock = threading.Lock()
        self.entries = deque(maxlen=keep)
        self.seq = 0
        self.directory = self.path = None
        self.error = None
        if root is not None:
            directory = Path(root) / ("session-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self.directory, self.path = directory, directory / "log.jsonl"
            except OSError as error:
                self.error = f"Session folder unavailable; log kept in memory only: {error}"

    def add(self, event, message, **data):
        with self.lock:
            self.seq += 1
            entry = {"seq": self.seq, "time": utc_now(), "event": event, "message": message, "data": data}
            self.entries.append(entry)
            if self.path:
                try:
                    with self.path.open("a", encoding="utf-8") as file:
                        file.write(json.dumps(entry, default=str) + "\n")
                except OSError as error:
                    self.error = f"Could not write the session log: {error}"
            return entry

    def since(self, after=0):
        with self.lock:
            return [entry for entry in self.entries if entry["seq"] > after]

    def jsonl(self):
        with self.lock:
            if self.path and self.path.exists():
                return self.path.read_bytes()
            return "".join(json.dumps(entry, default=str) + "\n" for entry in self.entries).encode()


class Recorder:
    """Writes analyzed live frames of one recording; runs on the acquisition thread."""

    def __init__(self, session_dir, camera, settings, uncertainty_settings, max_frames=MAX_FRAMES):
        number = len(list(session_dir.glob("recording-*"))) + 1
        self.directory = session_dir / f"recording-{number:03d}"
        (self.directory / "frames").mkdir(parents=True)
        self.max_frames = max_frames
        self.count = 0
        self._dark, self._dark_file, self._darks = None, "", 0
        self.metadata = {"format": "beam-profiler-recording", "format_version": 1,
                         "software": {"name": "beam-profiler", "version": __version__},
                         "session": session_dir.name, "recording": self.directory.name,
                         "started": utc_now(), "stopped": None, "stop_reason": None, "frame_count": 0,
                         "camera": dict(camera), "analysis_settings": dict(settings),
                         "uncertainty_settings": dict(uncertainty_settings),
                         "frames": "frames.csv",
                         "note": "Frames are native, unscaled camera values; analysis settings are those at the start."}
        self._write_metadata()
        self.manifest = (self.directory / "frames.csv").open("w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.manifest, fieldnames=MANIFEST_FIELDS)
        self.writer.writeheader()

    def _write_metadata(self):
        (self.directory / "recording.json").write_text(json.dumps(self.metadata, indent=2, default=str))

    def status(self):
        return {"path": str(self.directory), "name": f"{self.metadata['session']}/{self.directory.name}",
                "frames": self.count, "max_frames": self.max_frames}

    def add(self, pixels, maximum, timestamp, camera, dark):
        """Store one frame; return a reason string when recording must stop."""
        if self.count >= self.max_frames:
            return f"frame limit of {self.max_frames} reached"
        if self.count % 100 == 0 and shutil.disk_usage(self.directory).free < MIN_FREE_BYTES:
            return "less than 2 GB free disk space"
        if dark is not self._dark:
            self._dark = dark
            self._dark_file = ""
            if dark is not None:
                self._darks += 1
                self._dark_file = f"dark-{self._darks:03d}.tiff"
                (self.directory / self._dark_file).write_bytes(tiff_bytes(dark.astype("float32")))
        self.count += 1
        name = f"frame-{self.count:06d}.tiff"
        data = tiff_bytes(pixels)
        (self.directory / "frames" / name).write_bytes(data)
        self.writer.writerow({"index": self.count, "timestamp": timestamp, "file": name,
                              "sha256": hashlib.sha256(data).hexdigest(),
                              "exposure_us": camera.get("exposure_us"), "gain_db": camera.get("gain_db"),
                              "pixel_format": camera.get("pixel_format"), "maximum_dn": maximum,
                              "dark_file": self._dark_file})
        self.manifest.flush()
        return None

    def stop(self, reason):
        self.manifest.close()
        self.metadata.update(stopped=utc_now(), stop_reason=reason, frame_count=self.count)
        self._write_metadata()
        return self.status()


def list_recordings(root):
    """Replayable recordings under a sessions directory, as camera-list entries."""
    recordings = {}
    if root is None or not Path(root).is_dir():
        return recordings
    for meta_path in sorted(Path(root).glob("session-*/recording-*/recording.json")):
        try:
            meta = json.loads(meta_path.read_text())
            with (meta_path.parent / "frames.csv").open(newline="", encoding="utf-8") as file:
                frames = sum(1 for _ in csv.DictReader(file))
        except (OSError, ValueError):
            continue
        if not frames:
            continue
        name = f"{meta_path.parent.parent.name}/{meta_path.parent.name}"
        camera = meta.get("camera") or {}
        recordings["replay:" + name] = {
            "path": meta_path.parent,
            "device": {"id": "replay:" + name, "vendor": "Replay", "model": f"{camera.get('model', 'Camera')} (recorded)",
                       "serial": f"{name} · {frames} frames", "transport": "Recorded session",
                       "driver": "replay", "available": True}}
    return recordings
