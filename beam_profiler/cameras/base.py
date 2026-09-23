from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Frame:
    pixels: np.ndarray
    maximum: int
    pixel_format: str


class Camera(Protocol):
    info: dict

    def open(self, device_id: str) -> dict: ...
    def read(self) -> Frame: ...
    def configure(self, exposure_us: float, gain_db: float) -> dict: ...
    def close(self) -> None: ...
