from io import BytesIO
import json
import time
import zipfile

import numpy as np
from PIL import Image
import pytest

from beam_profiler.service import Profiler


def wait_frame(p, after=0):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        packet = p.packet()
        if packet and packet["id"] > after:
            return packet
        time.sleep(.03)
    raise AssertionError("No frame was published")


@pytest.fixture
def profiler():
    p = Profiler()
    p.request("connect", {"id":"demo"})
    yield p
    p.close()


def test_pause_export_resume_and_disconnect(profiler):
    p = profiler
    wait_frame(p)
    p.request("pause", {"paused":True})
    frozen = p.packet()
    time.sleep(.15)
    assert p.packet()["id"] == frozen["id"]
    assert p.packet()["uncertainty"] == frozen["uncertainty"]
    archive = zipfile.ZipFile(BytesIO(p.export()))
    metadata = json.loads(archive.read("measurement.json"))
    raw = np.array(Image.open(BytesIO(archive.read("raw.tiff"))))
    assert raw.shape == (720, 960)
    assert raw.dtype == np.uint16
    assert metadata["timestamp"] == frozen["timestamp"]
    assert metadata["camera"]["simulated"]
    assert metadata["uncertainty"] == frozen["uncertainty"]
    assert len(archive.read("uncertainty-samples.csv").splitlines()) == frozen["uncertainty"]["sample_count"]+1
    assert len(archive.read("profile_x.csv").splitlines()) == 961
    p.request("pause", {"paused":False})
    wait_frame(p, frozen["id"])
    p.request("disconnect")
    assert p.packet() is None
    assert not p.status()["connected"]
    with pytest.raises(ValueError): p.export()


@pytest.mark.parametrize("data", [{"roi":[-1,0,50,50]}, {"roi":[0,0,4,4]},
                                  {"magnification":0}, {"pixel_pitch_um":float('nan')},
                                  {"noise_sigma":11}, {"subtract_border":"true"}])
def test_invalid_analysis_settings_are_atomic(profiler, data):
    before = profiler.status()["settings"]
    with pytest.raises(ValueError): profiler.request("analysis", data)
    assert profiler.status()["settings"] == before


def test_settings_reanalyze_frozen_frame(profiler):
    wait_frame(profiler)
    profiler.request("pause", {"paused":True})
    original = profiler.packet()
    profiler.request("analysis", {"magnification":2})
    changed = profiler.packet()
    assert changed["timestamp"] == original["timestamp"]
    assert changed["metrics"]["diameter_x"] == pytest.approx(original["metrics"]["diameter_x"] / 2)


def test_camera_change_clears_dark(profiler):
    profiler.request("dark")
    wait_frame(profiler)
    assert profiler.status()["dark_active"]
    archive = zipfile.ZipFile(BytesIO(profiler.export()))
    assert "dark-reference.tiff" in archive.namelist()
    profiler.request("configure", {"exposure_us":8000,"gain_db":1})
    assert not profiler.status()["dark_active"]
    assert profiler.status()["camera"]["exposure_us"] == 8000


@pytest.mark.parametrize("data", [{"exposure_us":1e9,"gain_db":0}, {"exposure_us":8000,"gain_db":float('nan')},
                                  {"exposure_us":"fast","gain_db":0}])
def test_rejected_camera_settings_keep_dark_and_camera(profiler, data):
    profiler.request("dark")
    before = profiler.status()["camera"]
    with pytest.raises(ValueError): profiler.request("configure", data)
    assert profiler.status()["dark_active"]
    assert profiler.status()["camera"] == before


@pytest.mark.parametrize("data", [{"window_frames":1},{"window_frames":60.5},
                                  {"window_frames":True},{"window_frames":601},
                                  {"pixel_pitch_u_um":-1},{"magnification_u":float('nan')},
                                  {"magnification_u":.2},{"scale_correlation":1.1}])
def test_invalid_uncertainty_settings_are_atomic(profiler,data):
    previous=profiler.status()["uncertainty_settings"]
    with pytest.raises(ValueError): profiler.request("uncertainty",data)
    assert profiler.status()["uncertainty_settings"] == previous


def test_calibration_unknown_and_reset_on_changed_nominal(profiler):
    wait_frame(profiler)
    profiler.request("pause",{"paused":True})
    assert profiler.status()["uncertainty_settings"]["pixel_pitch_u_um"] is None
    profiler.request("uncertainty",{"pixel_pitch_u_um":.01,"magnification_u":.01})
    assert profiler.packet()["uncertainty"]["sample_count"] == 0
    assert profiler.packet()["uncertainty"]["calibration"]["complete"]
    profiler.request("analysis",{"pixel_pitch_um":3.45,"magnification":1})
    assert profiler.status()["uncertainty_settings"]["pixel_pitch_u_um"] == .01
    profiler.request("analysis",{"pixel_pitch_um":4,"magnification":2})
    assert profiler.status()["uncertainty_settings"]["pixel_pitch_u_um"] is None
    assert profiler.status()["uncertainty_settings"]["magnification_u"] is None


def test_frozen_reanalysis_and_explicit_reset_do_not_add_samples(profiler):
    wait_frame(profiler)
    profiler.request("pause",{"paused":True})
    original=profiler.packet()
    assert original["uncertainty"]["sample_count"] >= 1
    profiler.request("reset_statistics")
    assert profiler.packet()["id"] == original["id"]
    assert profiler.packet()["uncertainty"]["sample_count"] == 0
    profiler.request("analysis",{"roi":[200,100,750,620]})
    assert profiler.packet()["uncertainty"]["sample_count"] == 0


def test_full_uncertainty_window_is_exported_consistently(profiler):
    profiler.request("uncertainty",{"window_frames":20})
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        packet=profiler.packet()
        if packet and packet["uncertainty"]["sample_count"]==20:
            break
        time.sleep(.03)
    profiler.request("pause",{"paused":True})
    frozen=profiler.packet()["uncertainty"]
    assert frozen["sample_count"] == 20
    assert frozen["fields"]["diameter_x"]["known_standard_u"] > 0
    archive=zipfile.ZipFile(BytesIO(profiler.export()))
    import csv
    from io import StringIO
    rows=list(csv.DictReader(StringIO(archive.read("uncertainty-samples.csv").decode())))
    series=np.array([float(row["diameter_x"]) for row in rows])
    assert series.std(ddof=1) == pytest.approx(frozen["fields"]["diameter_x"]["repeatability_sd"])
    assert rows[0]["timestamp"] == frozen["window_start"]
    assert rows[-1]["timestamp"] == frozen["window_end"]
    # Resume clears the old window rather than treating a long pause as adjacent frames.
    profiler.request("pause",{"paused":False})
    next_frame=wait_frame(profiler,profiler.packet()["id"])
    assert next_frame["uncertainty"]["sample_count"] < 20


def test_restore_preserves_frozen_pixels_and_dark_but_not_statistics(profiler):
    profiler.request("dark")
    wait_frame(profiler)
    profiler.request("pause",{"paused":True})
    previous=profiler.snapshot
    archive=profiler.export()
    profiler.request("restore_snapshot",{"archive":archive})
    state=profiler.status()
    restored=profiler.snapshot
    assert state["paused"] and state["dark_active"]
    assert restored.packet["timestamp"] == previous.packet["timestamp"]
    np.testing.assert_array_equal(restored.pixels,previous.pixels)
    np.testing.assert_allclose(restored.dark,previous.dark,rtol=1e-6)
    assert restored.packet["uncertainty"]["sample_count"] == 0


def test_png_and_raw_export_carry_the_same_plain_text_record(profiler):
    profiler.request("uncertainty",{"window_frames":20})
    deadline=time.monotonic()+6
    while time.monotonic()<deadline and (profiler.packet() or {"uncertainty":{"sample_count":0}})["uncertainty"]["sample_count"]<20:
        time.sleep(.03)
    profiler.request("pause",{"paused":True})
    frozen=profiler.packet()
    png=Image.open(BytesIO(profiler.inspection("gray")))
    png.load()
    text=png.text["Description"]
    assert png.format == "PNG" and png.mode == "RGB"
    for expected in [frozen["timestamp"], "SIMULATED", "Gaussian beam simulator", "Mono12",
                     "Exposure / gain", "D4σ diameter X", "± ", "3.45 µm pixel pitch", "Dark reference     none"]:
        assert expected in text
    assert png.text["Creation Time"] == frozen["timestamp"]
    record=json.loads(png.text["measurement.json"])
    assert record["metrics"] == frozen["metrics"] and record["uncertainty"] == frozen["uncertainty"]
    archive=zipfile.ZipFile(BytesIO(profiler.export("gray")))
    assert {"raw.tiff","measurement.json","details.txt","inspection.png"} <= set(archive.namelist())
    assert archive.read("details.txt").decode() == text
    assert json.loads(archive.read("measurement.json")) == record
    with pytest.raises(ValueError): profiler.inspection("rainbow")


def test_png_requires_a_frame():
    p=Profiler()
    try:
        with pytest.raises(ValueError): p.inspection()
    finally:
        p.close()
