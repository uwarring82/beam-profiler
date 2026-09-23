import csv
import hashlib
from io import BytesIO
import json
import time
import zipfile

import numpy as np
from PIL import Image
import pytest

from beam_profiler.service import Profiler


def wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError("Condition not reached")


@pytest.fixture
def session(tmp_path):
    p = Profiler(tmp_path)
    p.request("scan")
    p.request("connect", {"id": "demo"})
    yield p, tmp_path
    p.close()


def events(p):
    return [entry["event"] for entry in p.log.since(0)]


def record(p, frames=6):
    p.request("dark")
    p.request("record", {"recording": True})
    start = p.status()["recording"]
    wait(lambda: (p.status()["recording"] or {}).get("frames", 0) >= frames)
    p.request("record", {"recording": False})
    return start


def test_log_is_written_to_disk_and_exported(session):
    p, root = session
    p.request("analysis", {"magnification": 2})
    p.request("configure", {"exposure_us": 8000, "gain_db": 1})
    with pytest.raises(ValueError):
        p.request("configure", {"exposure_us": 1e9, "gain_db": 0})
    wait(p.packet)
    archive = zipfile.ZipFile(BytesIO(p.export()))
    on_disk = [json.loads(line) for line in (p.log.directory / "log.jsonl").read_text().splitlines()]
    kinds = [entry["event"] for entry in on_disk]
    for expected in ["session_start", "scan", "connect", "analysis", "configure", "error", "quality", "export"]:
        assert expected in kinds
    assert on_disk[0]["data"]["session_folder"] == str(p.log.directory)
    assert any("magnification = 2.0" in entry["message"] for entry in on_disk)
    exported = [json.loads(line) for line in archive.read("session-log.jsonl").decode().splitlines()]
    assert exported == on_disk[:len(exported)] and exported[-1]["event"] == "export"
    assert p.status()["log_seq"] == on_disk[-1]["seq"]
    assert [e["seq"] for e in p.log.since(on_disk[-2]["seq"])] == [on_disk[-1]["seq"]]


def test_recording_manifest_is_exact_and_verifiable(session):
    p, root = session
    start = record(p)
    folder = root / p.log.directory.name / start["name"].split("/")[1]
    meta = json.loads((folder / "recording.json").read_text())
    rows = list(csv.DictReader((folder / "frames.csv").open()))
    assert meta["frame_count"] == len(rows) >= 6 and meta["stop_reason"] == "stopped by user"
    assert meta["camera"]["model"] == "Gaussian beam simulator"
    for row in rows:
        data = (folder / "frames" / row["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
        assert np.array(Image.open(BytesIO(data))).dtype == np.uint16
        assert row["dark_file"] == "dark-001.tiff"
    assert (folder / "dark-001.tiff").exists()
    assert {"recording_start", "recording_stop", "dark_capture"} <= set(events(p))
    assert any(d["id"] == "replay:" + start["name"] for d in p.status()["devices"])


def test_replay_reproduces_recorded_frames_and_timestamps(session):
    p, root = session
    start = record(p)
    folder = p.log.directory / start["name"].split("/")[1]
    rows = list(csv.DictReader((folder / "frames.csv").open()))
    p.request("analysis", {"magnification": 3})
    p.request("connect", {"id": "replay:" + start["name"]})
    status = p.status()
    assert status["camera"]["driver"] == "replay" and status["settings"]["magnification"] == 1
    packet = wait(lambda: p.packet())
    p.request("pause", {"paused": True})
    snap = p.snapshot
    row = next(r for r in rows if r["timestamp"] == snap.packet["timestamp"])
    recorded = np.array(Image.open(folder / "frames" / row["file"]))
    np.testing.assert_array_equal(snap.pixels, recorded)
    assert snap.dark is not None and p.status()["dark_active"]
    assert snap.packet["replay"]["frame"] == int(row["index"])
    text = Image.open(BytesIO(p.inspection())).text["Description"]
    assert "[REPLAY]" in text and start["name"] in text
    with pytest.raises(ValueError):
        p.request("configure", {"exposure_us": 8000, "gain_db": 0})
    with pytest.raises(ValueError):
        p.request("dark")
    with pytest.raises(ValueError):
        p.request("record", {"recording": True})


def test_replay_loops_with_a_fresh_window_and_detects_tampering(session):
    p, root = session
    start = record(p, 3)
    folder = p.log.directory / start["name"].split("/")[1]
    p.request("connect", {"id": "replay:" + start["name"]})
    n = p.camera.info["replay"]["frames"]
    wait(lambda: events(p).count("replay_start") >= 2, timeout=n * 1.5 + 5)
    assert (p.packet()["uncertainty"]["sample_count"]) <= n
    first = next(csv.DictReader((folder / "frames.csv").open()))
    (folder / "frames" / first["file"]).write_bytes(b"tampered")
    wait(lambda: "acquisition_error" in events(p), timeout=n * 1.5 + 5)
    assert any("Checksum mismatch" in e["message"] for e in p.log.since(0))


def test_unknown_or_escaping_replay_ids_are_rejected(session):
    p, root = session
    for device in ["replay:../../etc", "replay:session-x/recording-001"]:
        with pytest.raises(ValueError, match="Unknown recording"):
            p.request("connect", {"id": device})
    assert p.status()["connected"]


def test_no_sessions_folder_keeps_log_in_memory():
    p = Profiler()
    try:
        p.request("connect", {"id": "demo"})
        assert p.status()["session"] is None and p.log.directory is None
        assert "connect" in events(p)
        with pytest.raises(ValueError, match="session folder"):
            p.request("record", {"recording": True})
    finally:
        p.close()
