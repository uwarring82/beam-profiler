"""Verified model calibration. Unknown models deliberately default to pixels.

Add exact DeviceModelName keys after validating capture and calibration on
hardware. Transport support alone does not imply every camera feature works.
"""
MODEL_PROFILES = {
    "Firefly FFY-U3-16S2M-DL": {
        "pixel_pitch_um": 3.45,
        "sensor": "Sony IMX296",
        "source": "https://softwareservices.flir.com/FFY-U3-16S2-DL/latest/Model/spec.html",
    },
}
