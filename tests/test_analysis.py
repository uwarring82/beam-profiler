import numpy as np
import pytest

from beam_profiler.analysis import analyze


def gaussian(angle=0):
    y, x = np.mgrid[:400, :500]
    dx, dy = x - 260.25, y - 188.75
    theta = np.deg2rad(angle)
    u = np.cos(theta)*dx + np.sin(theta)*dy
    v = -np.sin(theta)*dx + np.cos(theta)*dy
    return 80 + 3000 * np.exp(-.5*((u/27)**2 + (v/17)**2))


def test_gaussian_centroid_diameters_and_calibration():
    m, px, py = analyze(gaussian(), 4095, pixel_pitch_um=3.45, magnification=2, noise_sigma=0)
    assert m["valid"]
    assert m["centroid_x_px"] == pytest.approx(260.25, abs=.001)
    assert m["centroid_y_px"] == pytest.approx(188.75, abs=.001)
    assert m["diameter_x"] == pytest.approx(4*27*3.45/2, rel=1e-5)
    assert m["diameter_y"] == pytest.approx(4*17*3.45/2, rel=1e-5)
    assert px.sum() == pytest.approx(py.sum())
    assert m["unit"] == "µm"


def test_rotated_beam_principal_axes():
    m, _, _ = analyze(gaussian(31), 4095, noise_sigma=0)
    assert m["major"] == pytest.approx(108, rel=1e-5)
    assert m["minor"] == pytest.approx(68, rel=1e-5)
    assert m["angle_deg"] == pytest.approx(31, abs=.001)
    assert m["ellipticity"] == pytest.approx(17/27, rel=1e-5)


def test_roi_preserves_image_coordinates():
    m, px, py = analyze(gaussian(), 4095, roi=[100, 60, 430, 330], noise_sigma=0)
    assert m["centroid_x_px"] == pytest.approx(260.25, abs=.01)
    assert m["centroid_y_px"] == pytest.approx(188.75, abs=.01)
    assert len(px) == 330 and len(py) == 270


@pytest.mark.parametrize("pixels", [np.zeros((100,100)), np.full((100,100), 120),
                                   np.random.default_rng(3).normal(100, 3, (100,100))])
def test_no_beam_does_not_report_spurious_widths(pixels):
    m, _, _ = analyze(pixels, 4095)
    assert not m["valid"]
    assert m["diameter_x"] is None and m["centroid_x_px"] is None


def test_saturation_and_clipping_are_reported():
    pixels = np.minimum(gaussian(), 2000)
    m, _, _ = analyze(pixels, 2000, roi=[0,0,280,400])
    assert m["saturated_percent"] > 0
    assert any("Saturated" in w for w in m["warnings"])
    assert any("boundary" in w for w in m["warnings"])


def test_dark_subtraction_and_input_not_modified():
    pixels = gaussian()
    before = pixels.copy()
    m, _, _ = analyze(pixels, 4095, dark=np.full_like(pixels,80), subtract_border=False, noise_sigma=0)
    assert m["diameter_x"] == pytest.approx(108, rel=1e-5)
    np.testing.assert_array_equal(pixels, before)


def test_scattered_hot_pixels_do_not_look_like_a_beam():
    pixels = np.random.default_rng(5).normal(100, 3, (400,500))
    pixels[10::20, 10::20] = 2000
    m, _, _ = analyze(pixels, 4095)
    assert not m["valid"]


def test_black_clipped_quantized_sensor_noise():
    rng = np.random.default_rng(12)
    pixels = np.maximum(0, np.round(rng.normal(0,.6,(400,500)))) * 64
    pixels[10::50,10::50] = 2600
    m, _, _ = analyze(pixels, 65535)
    assert m["noise_dn"] > 0
    assert not m["valid"]
