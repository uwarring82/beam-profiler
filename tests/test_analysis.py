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


@pytest.mark.parametrize("value,u,expected", [(123.456,.0996,("123.46","0.10")), (123.456,1.234,("123.5","1.2")),
                                              (1234.5,123,("1230","120")), (5.,None,("5",None))])
def test_plain_text_rounding_matches_two_digit_uncertainty(value,u,expected):
    from beam_profiler.report import with_uncertainty
    assert with_uncertainty(value,u) == expected


def test_roi_cutting_the_beam_is_flagged_even_when_moments_shrink():
    # Left ROI edge 0.8 of the 1/e² radius from the centre: moments shrink, so
    # only the profile-edge test catches the truncation.
    m, px, _ = analyze(gaussian(), 4095, roi=[217, 0, 500, 400])
    assert m["valid"] and px[0] / px.max() > .2
    assert any("boundary" in w for w in m["warnings"])
    contained, _, _ = analyze(gaussian(), 4095)
    assert contained["warnings"] == []


def test_beam_wider_than_the_roi_is_not_mistaken_for_background():
    # A diverging fibre output larger than the sensor: the border holds smooth beam
    # wings, which the border estimate would otherwise treat as background and noise.
    y, x = np.mgrid[:400, :500]
    pixels = 60000 * np.exp(-2 * ((x - 250) ** 2 + (y - 200) ** 2) / 260 ** 2)
    pixels += np.random.default_rng(4).normal(30, 3, pixels.shape)
    m, _, _ = analyze(pixels, 65535)
    assert any("border is not dark" in w for w in m["warnings"])


@pytest.mark.parametrize("pixels", [gaussian(), np.random.default_rng(3).normal(100, 3, (100, 100)),
                                    np.maximum(0, np.round(np.random.default_rng(12).normal(0, .6, (400, 500)))) * 64])
def test_dark_or_noisy_borders_are_not_flagged(pixels):
    m, _, _ = analyze(pixels, 65535)
    assert not any("border is not dark" in w for w in m["warnings"])


def test_encircled_signal_diameters_follow_circular_gaussian():
    y, x = np.mgrid[:180, :200]
    radius = 23.
    data = 17 + 5000*np.exp(-2*((x-95.2)**2 + (y-82.7)**2)/radius**2)
    metrics, *_ = analyze(data.round(), 65535, roi=[23, 12, 170, 160],
                          pixel_pitch_um=3., magnification=2.)
    ee = metrics["encircled_energy"]
    assert ee["d50"] == pytest.approx(radius * np.sqrt(2*np.log(2)) * 1.5, abs=.4)
    assert ee["d86_5"] == pytest.approx(2 * radius * 1.5, abs=.4)


def test_encircled_energy_matches_pixel_aperture_for_asymmetric_lobes():
    frame = np.zeros((60, 80), dtype=np.uint16)
    frame[20:26, 20:29] = 30
    frame[35:43, 43:50] = 75
    m, *_ = analyze(frame, 255, roi=[10, 8, 70, 55])
    y, x = np.indices(frame.shape)
    r = np.hypot(x - m["centroid_x_px"], y - m["centroid_y_px"])
    for name, fraction in [("d50", .5), ("d86_5", 1-np.exp(-2))]:
        radius = m["encircled_energy"][name] / 2
        assert frame[r < radius - 1e-10].sum() < frame.sum() * fraction
        assert frame[r <= radius + 1e-10].sum() >= frame.sum() * fraction
