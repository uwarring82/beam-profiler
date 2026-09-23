import time

import numpy as np

from .base import Frame


class DemoCamera:
    def __init__(self):
        self.info = {}
        self.rng = np.random.default_rng(42)
        self.y, self.x = np.mgrid[:720, :960]

    def open(self, device_id="demo"):
        self.info = {"id": "demo", "driver": "demo", "model": "Gaussian beam simulator",
                     "vendor": "Demo", "serial": "SIMULATED", "width": 960, "height": 720,
                     "pixel_format": "Mono12", "pixel_pitch_um": 3.45, "simulated": True,
                     "exposure_us": 10000., "gain_db": 0.,
                     "exposure_us_range": [100., 100000.], "gain_db_range": [0., 24.]}
        return dict(self.info)

    def read(self):
        time.sleep(1 / 20)
        t = time.monotonic()
        dx, dy = self.x - 480 - 4 * np.sin(t / 4), self.y - 350 - 3 * np.cos(t / 5)
        u, v = .94 * dx + .342 * dy, -.342 * dx + .94 * dy
        amplitude = 3100 * self.info["exposure_us"] / 10000 * 10 ** (self.info["gain_db"] / 20)
        signal = amplitude * np.exp(-2 * ((u / 125) ** 2 + (v / 91) ** 2))
        pixels = np.clip(30 + signal + self.rng.normal(0, 1.5, signal.shape), 0, 4095).astype(np.uint16)
        return Frame(pixels, 4095, "Mono12")

    def configure(self, exposure_us, gain_db):
        for key, value in [("exposure_us", exposure_us), ("gain_db", gain_db)]:
            low, high = self.info[key + "_range"]
            if not np.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}.")
        self.info.update(exposure_us=exposure_us, gain_db=gain_db)
        return dict(self.info)

    def close(self):
        pass
