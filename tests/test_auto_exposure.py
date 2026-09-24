import numpy as np
import pytest

from beam_profiler.cameras.base import Frame
from beam_profiler.service import Profiler


@pytest.fixture
def profiler():
    p = Profiler()
    p.request("connect", {"id": "demo"})
    yield p
    p.close()


@pytest.mark.parametrize("start", [100000, 150, 10000])
def test_converges_to_the_target_band_from_saturated_dim_and_good_starts(profiler, start):
    profiler.request("configure", {"exposure_us": start, "gain_db": 0})
    profiler.request("dark", {})
    state = profiler.request("auto_exposure")
    result = state["auto_exposure"]
    assert result["status"] == "converged", result
    assert 60 <= result["steps"][-1]["peak_percent"] <= 90
    assert state["camera"]["exposure_us"] == result["steps"][-1]["exposure_us"]
    assert state["camera"]["gain_db"] == 0
    assert not state["dark_active"]
    assert len(result["steps"]) <= 5
    assert any(e["event"] == "auto_exposure" for e in profiler.log.since(0))


def test_hot_pixels_do_not_drive_exposure_down(profiler):
    read = profiler.camera.read
    def with_hot_pixels():
        frame = read()
        frame.pixels[5, 5:15] = 4095
        return frame
    profiler.camera.read = with_hot_pixels
    result = profiler.request("auto_exposure")["auto_exposure"]
    assert result["status"] == "converged"


@pytest.mark.parametrize("level,advice", [(4095, "add ND"), (31, "raise the gain")])
def test_reports_when_the_camera_range_is_insufficient(profiler, level, advice):
    profiler.camera.read = lambda: Frame(np.full((720, 960), level, np.uint16), 4095, "Mono12")
    result = profiler.request("auto_exposure")["auto_exposure"]
    assert result["status"] == "limited" and advice in result["message"]


def test_rejects_invalid_target_and_needs_a_live_camera(profiler):
    with pytest.raises(ValueError):
        profiler.request("auto_exposure", {"target_percent": 99})
    profiler.request("disconnect")
    with pytest.raises(ValueError):
        profiler.request("auto_exposure")
