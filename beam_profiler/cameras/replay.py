"""Replays a recording through the normal analysis path, looping over its frames.

Pixel values, frame timestamps, acquisition settings and dark references are
the recorded ones. Frame pacing follows recorded intervals, clamped to
0.02–1 s; it does not reproduce the original frame rate exactly.
"""
import csv
import hashlib
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from .base import Frame


class ReplayError(RuntimeError):
    pass


class ReplayCamera:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.info, self.rows, self.settings = {}, [], {}
        self.index, self.previous, self.darks = 0, None, {}

    def open(self, device_id):
        meta = json.loads((self.directory / "recording.json").read_text())
        with (self.directory / "frames.csv").open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        if not rows:
            raise ValueError("The recording contains no frames.")
        for row in rows:
            for key in ("file", "dark_file"):
                # Manifest names are plain file names; never follow paths out of the recording.
                if row[key] and Path(row[key]).name != row[key]:
                    raise ValueError("Invalid file name in the recording manifest.")
            datetime.fromisoformat(row["timestamp"])
        self.rows, self.settings = rows, meta.get("analysis_settings") or {}
        camera = meta.get("camera") or {}
        self.info = {**camera, "id": device_id, "driver": "replay", "recorded_driver": camera.get("driver"),
                     "simulated": bool(camera.get("simulated")),
                     "replay": {"session": meta.get("session"), "recording": meta.get("recording"),
                                "frames": len(rows), "frame": None, "sha256_verified": True}}
        return dict(self.info)

    def _dark(self, name):
        if not name:
            return None
        if name not in self.darks:
            self.darks[name] = np.array(Image.open(self.directory / name), dtype=np.float64)
        return self.darks[name]

    def read(self):
        row = self.rows[self.index]
        restart = self.index == 0
        timestamp = datetime.fromisoformat(row["timestamp"])
        delay = .05 if restart or self.previous is None else (timestamp - self.previous).total_seconds()
        time.sleep(min(1., max(.02, delay)))
        data = (self.directory / "frames" / row["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ReplayError(f"Checksum mismatch for recorded frame {row['file']}; the file was modified.")
        pixels = np.array(Image.open(BytesIO(data)))
        self.info.update(exposure_us=float(row["exposure_us"]), gain_db=float(row["gain_db"]),
                         pixel_format=row["pixel_format"])
        self.info["replay"] = {**self.info["replay"], "frame": int(row["index"])}
        dark = self._dark(row["dark_file"])
        self.previous = timestamp
        self.index = (self.index + 1) % len(self.rows)
        return Frame(pixels, int(row["maximum_dn"]), row["pixel_format"], timestamp=row["timestamp"],
                     dark=dark, restart=restart)

    def configure(self, exposure_us, gain_db):
        raise ValueError("Exposure and gain are fixed in a recording.")

    def close(self):
        self.darks.clear()
