"""Small, typed ctypes binding to Aravis 0.8; no GI/PySpin dependency.

All calls, including discovery, must be serialized by the acquisition service.
Native buffers are copied before returning ownership to the stream.
"""
import ctypes as c
import ctypes.util
import os
from pathlib import Path

import numpy as np

from .base import Frame
from .models import MODEL_PROFILES


class CameraError(RuntimeError):
    pass


class GError(c.Structure):
    _fields_ = [("domain", c.c_uint32), ("code", c.c_int), ("message", c.c_char_p)]


P = c.c_void_p
E = c.POINTER(c.POINTER(GError))


class Aravis:
    def __init__(self):
        root = Path(__file__).resolve().parents[2]
        candidates = [os.environ.get("ARAVIS_LIBRARY"),
                      *map(str, sorted(root.glob(".vendor/aravis/*/lib/libaravis-0.8.0.dylib"))),
                      "/opt/homebrew/lib/libaravis-0.8.dylib",
                      "/usr/local/lib/libaravis-0.8.dylib",
                      ctypes.util.find_library("aravis-0.8")]
        errors = []
        for path in filter(None, candidates):
            try:
                self.lib = c.CDLL(path)
                self.path = path
                break
            except OSError as error:
                errors.append(str(error))
        else:
            raise CameraError("Aravis 0.8 is unavailable. Install it with brew install aravis "
                              "or set ARAVIS_LIBRARY. " + "; ".join(errors[-1:]))
        self.bind("g_object_unref", None, P)
        self.bind("g_error_free", None, c.POINTER(GError))
        self.bind("arv_update_device_list", None)
        self.bind("arv_get_n_devices", c.c_uint)
        for name in ["id", "vendor", "model", "serial_nbr", "protocol"]:
            self.bind("arv_get_device_" + name, c.c_char_p, c.c_uint)
        self.bind("arv_camera_new", P, c.c_char_p, E)
        for name in ["vendor_name", "model_name", "device_serial_number", "pixel_format_as_string"]:
            self.bind("arv_camera_get_" + name, c.c_char_p, P, E)
        self.bind("arv_camera_get_region", None, P, *([c.POINTER(c.c_int)] * 4), E)
        self.bind("arv_camera_set_pixel_format_from_string", None, P, c.c_char_p, E)
        self.bind("arv_camera_create_stream", P, P, P, P, E)
        self.bind("arv_camera_get_payload", c.c_uint, P, E)
        for name in ["start_acquisition", "stop_acquisition", "clear_triggers"]:
            self.bind("arv_camera_" + name, None, P, E)
        for name in ["acquisition_mode", "exposure_time_auto", "gain_auto"]:
            self.bind("arv_camera_set_" + name, None, P, c.c_int, E)
        for name in ["exposure_time", "gain", "frame_rate"]:
            self.bind("arv_camera_get_" + name, c.c_double, P, E)
            self.bind("arv_camera_set_" + name, None, P, c.c_double, E)
            self.bind("arv_camera_get_" + name + "_bounds", None, P,
                      c.POINTER(c.c_double), c.POINTER(c.c_double), E)
        self.bind("arv_buffer_new_allocate", P, c.c_size_t)
        self.bind("arv_stream_push_buffer", None, P, P)
        self.bind("arv_stream_timeout_pop_buffer", P, P, c.c_uint64)
        self.bind("arv_stream_try_pop_buffer", P, P)
        self.bind("arv_buffer_get_status", c.c_int, P)
        self.bind("arv_buffer_get_image_data", P, P, c.POINTER(c.c_size_t))
        self.bind("arv_buffer_get_image_pixel_format", c.c_uint32, P)
        for name in ["width", "height"]:
            self.bind("arv_buffer_get_image_" + name, c.c_int, P)
        self.bind("arv_buffer_get_image_padding", None, P, c.POINTER(c.c_int), c.POINTER(c.c_int))

    def bind(self, name, result, *args):
        fn = getattr(self.lib, name)
        fn.restype, fn.argtypes = result, list(args)

    def call(self, name, *args):
        error = c.POINTER(GError)()
        result = getattr(self.lib, "arv_camera_" + name)(*args, c.byref(error))
        if error:
            message = error.contents.message.decode(errors="replace")
            self.lib.g_error_free(error)
            raise CameraError(message)
        return result

    def discover(self):
        self.lib.arv_update_device_list()
        devices = []
        for index in range(self.lib.arv_get_n_devices()):
            fields = {key: (getattr(self.lib, "arv_get_device_" + key)(index) or b"").decode()
                      for key in ["id", "vendor", "model", "serial_nbr", "protocol"]}
            devices.append({"id": fields["id"], "vendor": fields["vendor"],
                            "model": fields["model"], "serial": fields["serial_nbr"],
                            "transport": fields["protocol"], "driver": "aravis", "available": True})
        return devices


class AravisCamera:
    def __init__(self, api: Aravis):
        self.api = api
        self.cam = self.stream = None
        self.running = False
        self.info = {}

    def open(self, device_id):
        a = self.api
        try:
            self.cam = a.call("new", device_id.encode())
            if not self.cam:
                raise CameraError("Camera could not be opened; close other camera applications.")
            model = a.call("get_model_name", self.cam).decode()
            # Unpacked monochrome preserves intensity values without demosaicing.
            # Try the highest supported unpacked format, with Mono8 as fallback.
            for fmt in ["Mono16", "Mono12", "Mono10", "Mono8"]:
                try:
                    a.call("set_pixel_format_from_string", self.cam, fmt.encode())
                    break
                except CameraError:
                    continue
            actual = a.call("get_pixel_format_as_string", self.cam).decode()
            if actual not in {"Mono8", "Mono10", "Mono12", "Mono16"}:
                raise CameraError(f"Unsupported intensity format: {actual}. A monochrome format is required.")
            a.call("clear_triggers", self.cam)
            a.call("set_acquisition_mode", self.cam, 0)
            for mode in ["exposure_time_auto", "gain_auto"]:
                try:
                    a.call("set_" + mode, self.cam, 0)
                except CameraError:
                    pass
            x, y, w, h = (c.c_int() for _ in range(4))
            a.call("get_region", self.cam, c.byref(x), c.byref(y), c.byref(w), c.byref(h))
            self.info = {"id": device_id, "driver": "aravis", "model": model,
                         "vendor": a.call("get_vendor_name", self.cam).decode(),
                         "serial": a.call("get_device_serial_number", self.cam).decode(),
                         "width": w.value, "height": h.value, "offset_x": x.value, "offset_y": y.value,
                         "pixel_format": actual, "simulated": False,
                         "pixel_pitch_um": MODEL_PROFILES.get(model, {}).get("pixel_pitch_um")}
            for key, node in [("exposure_us", "exposure_time"), ("gain_db", "gain")]:
                lo, hi = c.c_double(), c.c_double()
                a.call("get_" + node + "_bounds", self.cam, c.byref(lo), c.byref(hi))
                self.info[key + "_range"] = [lo.value, hi.value]
                self.info[key] = a.call("get_" + node, self.cam)
            # Keep preview bandwidth modest; actual accepted rate is read back.
            try:
                a.call("set_frame_rate", self.cam, 15.0)
            except CameraError:
                pass
            self.stream = a.call("create_stream", self.cam, None, None)
            if not self.stream:
                raise CameraError("Could not create the camera stream.")
            payload = a.call("get_payload", self.cam)
            if not 0 < payload <= 128 * 1024 * 1024:
                raise CameraError("Camera payload is invalid or exceeds 128 MiB.")
            for _ in range(8):
                buffer = a.lib.arv_buffer_new_allocate(payload)
                if not buffer:
                    raise CameraError("Could not allocate a frame buffer.")
                a.lib.arv_stream_push_buffer(self.stream, buffer)
            a.call("start_acquisition", self.cam)
            self.running = True
            return dict(self.info)
        except Exception:
            self.close()
            raise

    def read(self):
        lib = self.api.lib
        timeout = int(min(32_000_000, max(2_000_000, self.info["exposure_us"] + 2_000_000)))
        buf = lib.arv_stream_timeout_pop_buffer(self.stream, timeout)
        if not buf:
            raise CameraError("No frame received. Check the USB cable and close other camera applications.")
        # Prefer the newest completed buffer when processing is slower than the camera.
        # Bound draining so a continuously producing camera cannot starve the reader.
        for _ in range(7):
            newer = lib.arv_stream_try_pop_buffer(self.stream)
            if not newer:
                break
            lib.arv_stream_push_buffer(self.stream, buf)
            buf = newer
        try:
            status = lib.arv_buffer_get_status(buf)
            if status != 0:
                raise CameraError(f"Incomplete USB frame (Aravis status {status}).")
            w, h = lib.arv_buffer_get_image_width(buf), lib.arv_buffer_get_image_height(buf)
            size, xp, yp = c.c_size_t(), c.c_int(), c.c_int()
            ptr = lib.arv_buffer_get_image_data(buf, c.byref(size))
            lib.arv_buffer_get_image_padding(buf, c.byref(xp), c.byref(yp))
            fmt = lib.arv_buffer_get_image_pixel_format(buf)
            formats = {0x01080001: ("Mono8", 1, 255), 0x01100003: ("Mono10", 2, 1023),
                       0x01100005: ("Mono12", 2, 4095), 0x01100007: ("Mono16", 2, 65535)}
            if fmt not in formats:
                raise CameraError(f"Unsupported frame pixel format: 0x{fmt:08x}")
            name, bpp, maximum = formats[fmt]
            stride = w * bpp + xp.value
            if not ptr or min(w, h) <= 0 or size.value < stride * h:
                raise CameraError("Invalid or truncated frame payload.")
            data = c.string_at(ptr, stride * h)
            pixels = np.ndarray((h, w), dtype="u1" if bpp == 1 else "<u2",
                                buffer=data, strides=(stride, bpp)).copy()
            return Frame(pixels, maximum, name)
        finally:
            lib.arv_stream_push_buffer(self.stream, buf)

    def configure(self, exposure_us, gain_db):
        for key, value in [("exposure_us", exposure_us), ("gain_db", gain_db)]:
            lo, hi = self.info[key + "_range"]
            if not np.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"{key} must be between {lo:g} and {hi:g}.")
        self.api.call("stop_acquisition", self.cam)
        self.running = False
        try:
            self.api.call("set_exposure_time", self.cam, exposure_us)
            self.api.call("set_gain", self.cam, gain_db)
        finally:
            try:
                # Reflect actual state even if a later write fails.
                self.info["exposure_us"] = self.api.call("get_exposure_time", self.cam)
                self.info["gain_db"] = self.api.call("get_gain", self.cam)
            finally:
                # Discard frames acquired under the previous settings before restart.
                while True:
                    buffer = self.api.lib.arv_stream_try_pop_buffer(self.stream)
                    if not buffer:
                        break
                    self.api.lib.arv_stream_push_buffer(self.stream, buffer)
                self.api.call("start_acquisition", self.cam)
                self.running = True
        return dict(self.info)

    def close(self):
        if self.cam and self.running:
            try:
                self.api.call("stop_acquisition", self.cam)
            except CameraError:
                pass
        self.running = False
        if self.stream:
            self.api.lib.g_object_unref(self.stream)
            self.stream = None
        if self.cam:
            self.api.lib.g_object_unref(self.cam)
            self.cam = None
