"""Human-readable snapshot records: a plain-text summary and an inspection PNG.

Both are rendered from one exported snapshot, never from later frames. The PNG
is for viewing and reports; measurements stay in the native raw data.
"""
from io import BytesIO
import json
import math
import textwrap

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from . import __version__

REPOSITORY = "https://github.com/uwarring82/beam-profiler"
PALETTES = ("thermal", "gray")
# Same anchors as the browser's thermal color map.
ANCHORS = [(0, 7, 8, 23), (.18, 40, 24, 90), (.38, 111, 39, 110), (.6, 193, 62, 101),
           (.8, 246, 144, 82), (1, 255, 245, 185)]
THERMAL = np.stack([np.interp(np.arange(256) / 255, [a[0] for a in ANCHORS], [a[k] for a in ANCHORS])
                    for k in (1, 2, 3)], axis=1).round().astype(np.uint8)
FONTS = ["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "DejaVuSansMono.ttf"]


def preview_levels(pixels, maximum):
    """Fixed full-scale 8-bit display levels; never used for measurements."""
    return (pixels.astype(np.float32) / maximum * 255).clip(0, 255).astype(np.uint8)


def with_uncertainty(value, u):
    """Round u to two significant digits and the value to the same place."""
    if value is None:
        return "—", None
    if not u or not math.isfinite(u) or u <= 0:
        return f"{value:.6g}", None
    exponent = math.floor(math.log10(u)) - 1
    if round(u, -exponent) >= 10 ** (exponent + 2):
        exponent += 1
    places = max(0, -exponent)
    return f"{round(value, -exponent):.{places}f}", f"{round(u, -exponent):.{places}f}"


def measurement(snap):
    """The measurement.json record for a snapshot."""
    return {"timestamp": snap.packet["timestamp"], "software": {"name": "beam-profiler",
            "version": __version__, "repository": REPOSITORY},
            "camera": snap.camera, "settings": snap.settings, "metrics": snap.metrics,
            "uncertainty": snap.packet["uncertainty"], "dark_active": snap.dark is not None,
            "method": "Thresholded, background-corrected intensity moments; D4sigma diameter"}


def details_text(snap, palette="thermal"):
    camera, settings, m, u = snap.camera, snap.settings, snap.metrics, snap.packet["uncertainty"]
    fields = u.get("fields") or {}
    unit = m["unit"]
    if u["status"] == "blocked":
        pending = "uncertainty withheld: image quality"
    elif u["sample_count"] < u["minimum_samples"]:
        pending = f"uncertainty pending: {u['sample_count']}/{u['minimum_samples']} frames"
    else:
        pending = "uncertainty unresolved"

    def result(label, key, suffix):
        value = m[key]
        f = fields.get(key) or {}
        text, uu = with_uncertainty(value, f.get("known_standard_u"))
        if value is None:
            return f"  {label:<20} —"
        if uu is None:
            return f"  {label:<20} {text}{suffix}  ({pending})"
        return f"  {label:<20} {text} ± {uu}{suffix}"

    x0, y0, x1, y1 = m["roi"]
    pitch, magnification = settings["pixel_pitch_um"], settings["magnification"]
    scale = (f"{pitch:g} µm pixel pitch / {magnification:g}× magnification = {m['scale']:.6g} µm/px"
             f" ({'sensor plane' if magnification == 1 else 'object plane'})" if pitch
             else "pixel units (no pixel pitch specified)")
    calibration = u.get("calibration") or {}
    missing = ", ".join(calibration.get("missing") or []) or "none"
    lines = [
        "BEAM LAB · SINGLE-FRAME BEAM PROFILE" + ("  [REPLAY]" if camera.get("replay") else "")
        + ("  [SIMULATED DATA]" if camera.get("simulated") else ""),
        "",
        f"Frame time (UTC)   {snap.packet['timestamp']}",
        f"Camera             {camera.get('vendor', '')} {camera.get('model', '')}".rstrip(),
        f"Serial / driver    {camera.get('serial', '—')} / {camera.get('recorded_driver') or camera.get('driver', '—')}",
        *([f"Source             replay of {camera['replay']['session']}/{camera['replay']['recording']}, "
           f"recorded frame {camera['replay']['frame']} of {camera['replay']['frames']} (original timestamp)"]
          if camera.get("replay") else []),
        f"Image              {snap.pixels.shape[1]} × {snap.pixels.shape[0]} px · "
        f"{camera.get('pixel_format', '—')} · full scale {m['maximum_dn']} DN",
        f"Exposure / gain    {camera.get('exposure_us', float('nan')):.6g} µs / {camera.get('gain_db', float('nan')):.4g} dB",
        f"Scale              {scale}",
        f"Analysis ROI       x {x0}–{x1 - 1}, y {y0}–{y1 - 1} px" + ("" if settings["roi"] else " (full frame)"),
        f"Background         {'ROI-border median subtracted' if settings['subtract_border'] else 'no border subtraction'}"
        f"; threshold {settings['noise_sigma']:g} σ of border noise",
        f"Dark reference     {'active: mean of 8 blocked-beam frames, subtracted' if snap.dark is not None else 'none'}",
        f"Display            fixed 0 to full-scale {palette} map; box = analysis ROI, lines = centroid,"
        " ellipse = D4σ principal axes. Measure from the raw frame, not this image.",
        "",
        "RESULTS (± = known partial standard uncertainty, k = 1, single frame)",
        result("D4σ diameter X", "diameter_x", f" {unit}"),
        result("D4σ diameter Y", "diameter_y", f" {unit}"),
        result("D4σ major axis", "major", f" {unit}"),
        result("D4σ minor axis", "minor", f" {unit}"),
        result("Ellipticity", "ellipticity", " (minor/major)"),
        result("Major-axis angle", "angle_deg", "° (from +x toward +y)"),
        result("Centroid x", "centroid_x_px", " px"),
        result("Centroid y", "centroid_y_px", " px"),
        result("Peak intensity", "peak_percent", " % of full scale"),
        f"  {'Saturated pixels':<20} {m['saturated_percent']:.3g} %",
        result("Background", "background_dn", " DN"),
        f"  {'Border noise':<20} {m['noise_dn']:.4g} DN",
        "",
        f"Uncertainty        {u['status']}; {u['sample_count']} of {u['window_frames']} frames"
        f" over {u['duration_s']:.1f} s; unspecified calibration inputs: {missing}",
    ]
    notes = list(m["warnings"]) + list(u.get("warnings") or [])
    lines += [""] + [f"Warning: {w}" for w in notes] if notes else []
    lines += ["",
              "Method: D4σ = 4 × intensity-weighted standard deviation of thresholded, background-",
              "corrected native pixels. Not a Gaussian fit; not a certified ISO 11146 result.",
              "Residual systematic effects are outside the partial uncertainty budget.",
              f"Software: beam-profiler {__version__} · {REPOSITORY}"]
    wrapped = []
    for line in lines:
        wrapped += textwrap.wrap(line, 92, subsequent_indent="    ") or [""]
    return "\n".join(wrapped) + "\n"


def _font(size):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _profile(draw, box, values, start, label, color, font):
    left, top, right, bottom = box
    draw.rectangle(box, outline=(200, 205, 212))
    draw.text((left, top - 20), label, fill=(40, 48, 58), font=font)
    if len(values) > 1:
        peak = max(float(np.max(values)), 1e-12)
        xs = np.linspace(left, right, len(values))
        ys = bottom - np.clip(np.asarray(values, float) / peak, 0, 1) * (bottom - top - 4)
        draw.line(list(zip(xs.tolist(), ys.tolist())), fill=color, width=2)
    draw.text((left, bottom + 4), f"{start} px", fill=(90, 100, 112), font=font)
    end = f"{start + len(values) - 1} px"
    draw.text((right - draw.textlength(end, font=font), bottom + 4), end, fill=(90, 100, 112), font=font)


def inspection_png(snap, palette="thermal"):
    """Image, overlays, profiles and the plain-text record on one PNG sheet."""
    if palette not in PALETTES:
        raise ValueError(f"palette must be one of {', '.join(PALETTES)}.")
    m = snap.metrics
    levels = preview_levels(snap.pixels, m["maximum_dn"])
    image = Image.fromarray(THERMAL[levels] if palette == "thermal" else levels).convert("RGB")
    if image.width > 1600:
        image.thumbnail((1600, 1600))
    s = image.width / snap.pixels.shape[1]
    draw = ImageDraw.Draw(image)
    if snap.settings["roi"]:
        x0, y0, x1, y1 = m["roi"]
        draw.rectangle((x0 * s, y0 * s, x1 * s - 1, y1 * s - 1), outline=(185, 236, 208), width=2)
    if m["valid"]:
        cx, cy = (m["centroid_x_px"] + .5) * s, (m["centroid_y_px"] + .5) * s
        draw.line((0, cy, image.width, cy), fill=(209, 229, 232), width=1)
        draw.line((cx, 0, cx, image.height), fill=(209, 229, 232), width=1)
        a, b = m["major"] / m["scale"] / 2 * s, m["minor"] / m["scale"] / 2 * s
        theta, t = math.radians(m["angle_deg"]), np.linspace(0, 2 * math.pi, 181)
        xs = cx + a * math.cos(theta) * np.cos(t) - b * math.sin(theta) * np.sin(t)
        ys = cy + a * math.sin(theta) * np.cos(t) + b * math.cos(theta) * np.sin(t)
        draw.line(list(zip(xs.tolist(), ys.tolist())), fill=(228, 247, 220), width=2)

    text = details_text(snap, palette)
    body, small = _font(15), _font(13)
    line_height, margin, gap = 21, 32, 36
    text_width = max(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(line, font=body)
                     for line in text.splitlines())
    profile_height = 150
    left_height = image.height + gap + profile_height + 40
    width = int(margin + image.width + gap + text_width + margin)
    height = int(margin + max(left_height, line_height * len(text.splitlines())) + margin)
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    sheet.paste(image, (margin, margin))
    draw = ImageDraw.Draw(sheet)
    top = margin + image.height + gap
    half = (image.width - gap) // 2
    _profile(draw, (margin, top, margin + half, top + profile_height), snap.profile_x, m["roi"][0],
             "X integrated profile (normalized)", (40, 140, 190), small)
    _profile(draw, (margin + half + gap, top, margin + image.width, top + profile_height), snap.profile_y,
             m["roi"][1], "Y integrated profile (normalized)", (120, 90, 210), small)
    for i, line in enumerate(text.splitlines()):
        draw.text((margin + image.width + gap, margin + i * line_height), line, fill=(25, 32, 40), font=body)

    info = PngInfo()
    info.add_text("Title", "Beam Lab single-frame beam profile")
    info.add_text("Description", text)
    info.add_text("Creation Time", snap.packet["timestamp"])
    info.add_text("Source", f"{snap.camera.get('vendor', '')} {snap.camera.get('model', '')} "
                            f"S/N {snap.camera.get('serial', '')}".strip())
    info.add_text("Software", f"beam-profiler {__version__} {REPOSITORY}")
    info.add_text("Comment", "Display rendering only; measure from the native raw frame in the export ZIP.")
    info.add_text("measurement.json", json.dumps(measurement(snap), allow_nan=False), zip=True)
    out = BytesIO()
    sheet.save(out, format="PNG", pnginfo=info)
    return out.getvalue()
