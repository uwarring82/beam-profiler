"""Independent synthetic camera images, using analytical intensity profiles."""
import json

import numpy as np
import pytest

from beam_profiler.analysis import analyze
from beam_profiler.fitting import fit_frame


def exposure(model, radii=(19., 13.), angle=37., shape=(128, 180), center=(100.3, 59.7),
             noise=1.5):
    y, x = np.indices(shape)
    theta = np.radians(angle)
    dx, dy = x - center[0], y - center[1]
    a = np.sqrt(2) * (dx*np.cos(theta) + dy*np.sin(theta)) / radii[0]
    b = np.sqrt(2) * (dy*np.cos(theta) - dx*np.sin(theta)) / radii[1]
    t = a*a + b*b
    profiles = {
        "gaussian": np.exp(-t),
        "super_gaussian": np.exp(-2 * (t/2)**3.4),
        "hg10": a*a * np.exp(1-t),
        "hg11": a*a * b*b * np.exp(2-t),
        "lg01": t * np.exp(1-t),
    }
    dark = (8 + .15*x + .07*y).astype(np.float32)
    dn = dark + 23.5 + 1300 * profiles[model] + np.random.default_rng(1729).normal(0, noise, shape)
    return dn.round().astype(np.uint16), dark


@pytest.mark.parametrize("mode", ["gaussian", "super_gaussian", "hg10", "hg11", "lg01"])
def test_rotated_noisy_models_with_dark_and_offset_roi(mode):
    radii = (19., 19. if mode == "lg01" else 13.)
    raw, dark = exposure(mode, radii)
    m, *_ = analyze(raw, 4095, dark=dark, roi=[30, 7, 175, 124], pixel_pitch_um=3.45, magnification=2)
    result = fit_frame(raw, dark, m)
    summary = json.loads(json.dumps(result.summary, allow_nan=False))
    assert summary["unit"] == "µm" and summary["length_per_px"] == pytest.approx(1.725)
    # A Gaussian is also a super-Gaussian at order 1.
    best = summary["candidates"][0]
    if mode == "gaussian":
        assert best["model"] in ("gaussian", "super_gaussian")
        if best["order"] is not None:
            assert best["order"] == pytest.approx(1., abs=.002)
    else:
        assert best["model"] == mode
    np.testing.assert_allclose([best["center_x_px"], best["center_y_px"]], [100.3, 59.7], atol=.04)
    np.testing.assert_allclose([best["radius_u_px"], best["radius_v_px"]], radii, atol=.06)
    if mode == "lg01":
        assert best["angle_deg"] is None
    else:
        assert best["angle_deg"] == pytest.approx(37., abs=.2)
    if mode == "super_gaussian":
        assert best["order"] == pytest.approx(3.4, abs=.02)
    assert best["offset_dn"] == pytest.approx(23.5, abs=.2)
    assert best["peak_above_offset_dn"] == pytest.approx(1300., abs=2.)
    assert best["rmse_dn"] == pytest.approx(1.5, abs=.12)
    assert best["r_squared"] > .999
    assert result.residual.dtype == np.float32 and result.residual.shape == (117, 145)
    assert result.residual.min() < 0 < result.residual.max()
    assert best["rmse_dn"] == pytest.approx(np.mean(result.residual**2)**.5)


def test_two_lobes_along_shorter_moment_axis():
    raw, dark = exposure("hg10", radii=(9., 21.), angle=-42.)
    m, *_ = analyze(raw, 4095, dark=dark)
    best = fit_frame(raw, dark, m, "hg10").summary["candidates"][0]
    assert best["angle_deg"] == pytest.approx(-42., abs=.2)
    assert best["radius_u_px"] == pytest.approx(9., abs=.03)


def test_ring_detectable_off_center_on_large_sensor_and_wrong_model_has_large_residual():
    raw, dark = exposure("lg01", radii=(8., 8.), shape=(750, 1200), center=(1012.25, 87.3))
    m, *_ = analyze(raw, 4095, dark=dark, noise_sigma=5)
    result = fit_frame(raw, dark, m)
    rows = {r["model"]: r for r in result.summary["candidates"]}
    assert rows["lg01"]["rmse_dn"] * 2 < rows["gaussian"]["rmse_dn"]
    assert rows["lg01"]["radius_u_px"] == pytest.approx(8., abs=.06)
    assert result.summary["sample_pixels"] < 12000
    assert result.summary["roi_pixels"] == raw.size


def test_fit_rejects_bad_inputs():
    raw, dark = exposure("hg10")
    for image, roi in [(np.zeros_like(raw), None), (np.minimum(raw, 1000), [95, 35, 138, 80]),
                       (raw.copy(), None)]:
        if roi is None and image.any():
            image[60, 100] = 4095
        m, *_ = analyze(image, 4095, roi=roi)
        with pytest.raises(ValueError, match="quality warnings"):
            fit_frame(image, None, m, "hg10")
    m, *_ = analyze(raw, 4095, dark=dark)
    with pytest.raises(ValueError, match="supported"):
        fit_frame(raw, dark, m, ["hg10"])
