from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pytest

from beam_profiler.uncertainty import DEFAULTS, FIELDS, MIN_SAMPLES, UncertaintyWindow


def measurement(i=0, *, angle=None, unit="µm"):
    delta = (-1 if i % 2 else 1) * (1 + .01*i)
    return {"valid": True, "warnings": [], "unit":unit,
            "diameter_x":100+delta, "diameter_y":80+2*delta,
            "major":110+delta, "minor":60+.5*delta,
            "centroid_x_px":200+delta, "centroid_y_px":180-.5*delta,
            "ellipticity":(60+.5*delta)/(110+delta),
            "angle_deg":20+.1*delta if angle is None else angle,
            "peak_percent":70+delta, "background_dn":20+.2*delta}


def stamp(i):
    return (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=i/10)).isoformat()


def window(**settings):
    return UncertaintyWindow({**DEFAULTS,"pixel_pitch_um":3.45,"magnification":2.,**settings})


def fill(w, n=30, factory=measurement):
    for i in range(n):
        result = w.observe(factory(i),stamp(i))
    return result


def test_single_frame_uses_sample_sd_not_standard_error():
    result = fill(window(),40)
    values = np.array([measurement(i)["diameter_x"] for i in range(40)])
    field = result["fields"]["diameter_x"]
    assert field["mean"] == pytest.approx(values.mean())
    assert field["repeatability_sd"] == pytest.approx(values.std(ddof=1))
    assert field["known_standard_u"] == pytest.approx(values.std(ddof=1))
    assert field["uncertainty_of_mean"] is None
    assert result["coverage_factor"] == 1 and result["target"] == "single_frame"
    assert not result["budget_complete"]


def test_calibration_and_covariance_match_analytic_propagation():
    result = fill(window(pixel_pitch_u_um=.0345,magnification_u=.04,scale_correlation=.5))
    relative_var = .01**2 + .02**2 - 2*.5*.01*.02
    assert result["calibration"]["known_relative_variance"] == pytest.approx(relative_var)
    assert result["calibration"]["complete"]
    last = measurement(29)
    for key in ["diameter_x","diameter_y","major","minor"]:
        f = result["fields"][key]
        assert f["calibration_u"] == pytest.approx(last[key]*np.sqrt(relative_var))
        assert f["known_standard_u"]**2 == pytest.approx(f["repeatability_sd"]**2+last[key]**2*relative_var)
    assert result["calibration_covariance"][0][1] == pytest.approx(last["diameter_x"]*last["diameter_y"]*relative_var)
    # A shared isotropic scale cancels from ellipticity and pixel coordinates.
    assert result["fields"]["ellipticity"]["calibration_u"] == 0
    assert result["fields"]["centroid_x_px"]["calibration_u"] == 0
    np.testing.assert_allclose(np.array(result["known_covariance"]),
                               np.array(result["temporal_covariance"])+np.array(result["calibration_covariance"]))


def test_missing_calibration_is_not_claimed_exact():
    result = fill(window(pixel_pitch_u_um=.0345))
    assert result["calibration"]["missing"] == ["magnification_u"]
    assert not result["fields"]["diameter_x"]["calibration_complete"]
    assert not result["budget_complete"]


def test_pixel_units_skip_physical_calibration():
    result = fill(window(pixel_pitch_um=None),factory=lambda i:measurement(i,unit="px"))
    assert result["calibration"]["complete"]
    assert result["fields"]["diameter_x"]["calibration_u"] == 0


def test_temporal_covariance_preserves_correlated_widths():
    result = fill(window())
    values = np.array([[measurement(i)[k] for k in FIELDS] for i in range(30)])
    np.testing.assert_allclose(result["temporal_covariance"],np.cov(values,rowvar=False,ddof=1),atol=1e-12)
    assert result["temporal_covariance"][0][1] > 0


def test_axial_angles_wrap_at_180_degrees():
    result = fill(window(),factory=lambda i:measurement(i,angle=89 if i%2 else -89))
    f = result["fields"]["angle_deg"]
    assert abs(abs(f["mean"])-90) < 1e-8
    assert f["known_standard_u"] == pytest.approx(np.sqrt(30/29))


def test_round_beam_has_no_orientation_uncertainty():
    def circular(i):
        m = measurement(i)
        m["major"] = m["minor"] = 100+i*.001
        m["ellipticity"] = 1.
        return m
    result = fill(window(),factory=circular)
    assert result["fields"]["angle_deg"]["known_standard_u"] is None
    assert all(v is None for v in result["known_covariance"][FIELDS.index('angle_deg')])


def test_no_variation_does_not_claim_zero_uncertainty():
    result = fill(window(),factory=lambda _:measurement())
    assert result["fields"]["diameter_x"]["repeatability_sd"] == 0
    assert result["fields"]["diameter_x"]["known_standard_u"] is None
    assert result["fields"]["diameter_x"]["status"] == "unresolved"


@pytest.mark.parametrize("warnings,valid", [([],False),(["Saturated pixels"],True),(["ROI boundary"],True)])
def test_invalid_frame_breaks_the_window(warnings,valid):
    w=window(); fill(w)
    bad={**measurement(31),"warnings":warnings,"valid":valid}
    result=w.observe(bad,stamp(31))
    assert result["status"] == "blocked" and result["sample_count"] == 0
    assert not result["fields"]
    assert w.observe(measurement(32),stamp(32))["sample_count"] == 1


def test_window_bounds_warmup_and_duplicate_frame():
    w=window(window_frames=20)
    result=fill(w,19)
    assert result["status"] == "warming_up" and not result["fields"]
    w.observe(measurement(18),stamp(18))
    assert len(w.rows()) == 19
    result=w.observe(measurement(19),stamp(19))
    assert result["sample_count"] == 20 and result["fields"]
    result=w.observe(measurement(20),stamp(20))
    assert result["sample_count"] == 20 and result["window_start"] == stamp(1)
    assert result["duration_s"] == pytest.approx(1.9)
    w.reset()
    assert w.observe(measurement(20),stamp(20),add=False)["sample_count"] == 0


def test_drift_and_correlation_are_reported_without_sem():
    def moving(i):
        return {**measurement(i),"centroid_x_px":100.+i*.1}
    result=fill(window(),40,factory=moving)
    assert result["status"] == "changing"
    assert "centroid_x_px" in result["correlated_fields"]
    assert "centroid_x_px" in result["changing_fields"]
    assert result["fields"]["centroid_x_px"]["uncertainty_of_mean"] is None
    json.dumps(result,allow_nan=False)
