"""File and display adapter for the static workbench. Measurements use the desktop core."""
import base64
from io import BytesIO
import json
import math
import zipfile

import numpy as np
from PIL import Image
from beam_profiler.analysis import analyze
from beam_profiler import __version__


MAX_PIXELS = 4_000_000
MAX_BYTES = 48 * 1024 * 1024
ANCHORS = [(0, 8, 12, 24), (.07, 24, 20, 62), (.22, 66, 35, 112),
           (.45, 145, 46, 128), (.68, 223, 78, 100), (.84, 252, 148, 77),
           (1, 255, 235, 156)]
LUT = np.stack([np.interp(np.arange(256)/255, [p[0] for p in ANCHORS],
                          [p[k] for p in ANCHORS]) for k in [1, 2, 3]], axis=1).round().astype('uint8')


def encoded_image(image, fmt='PNG'):
    return base64.b64encode(image_bytes(image, fmt)).decode('ascii')


def image_bytes(image, fmt):
    out = BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def read_image(data, dark=False):
    with Image.open(BytesIO(data), formats=('PNG', 'TIFF')) as im:
        if min(im.size) < 8 or im.width * im.height > MAX_PIXELS or im.n_frames != 1:
            raise ValueError("Choose a single 8 × 8 pixel or larger image, up to 4 megapixels.")
        if im.mode not in ('L', 'I;16', 'I;16L', 'I;16B') and not (dark and im.mode == 'F'):
            raise ValueError("Use a monochrome 8- or 16-bit PNG or TIFF from the camera.")
        if im.format == 'TIFF' and not dark and im.tag_v2.get(262, 1) != 1:
            raise ValueError("Use a grayscale TIFF with zero representing black.")
        return np.array(im)


def load_frame(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Maximum file size is 48 MiB.")
    meta, dark = None, None
    if zipfile.is_zipfile(BytesIO(data)):
        with zipfile.ZipFile(BytesIO(data)) as z:
            names = z.namelist()
            if not {'measurement.json', 'raw.tiff'} <= set(names):
                raise ValueError("Choose a Beam Lab snapshot ZIP containing raw.tiff and measurement.json.")
            def member(name, limit):
                if names.count(name) != 1 or z.getinfo(name).file_size > limit:
                    raise ValueError("Duplicate or oversized snapshot member.")
                return z.read(name)
            meta = json.loads(member('measurement.json', 2 * 1024 * 1024))
            raw = read_image(member('raw.tiff', MAX_BYTES))
            if 'dark-reference.tiff' in names:
                dark = read_image(member('dark-reference.tiff', MAX_BYTES), dark=True)
                if dark.shape != raw.shape or not np.isfinite(dark).all():
                    raise ValueError("The dark reference does not match the frame.")
    else:
        raw = read_image(data)
    maximum = int(np.iinfo(raw.dtype).max)
    settings = {'roi': None, 'noise_sigma': 3., 'subtract_border': True,
                'scale': None, 'maximum': maximum}
    source = {'kind': 'image', 'dark_active': dark is not None}
    if meta is not None:
        original = meta['settings']
        pitch = original.get('pixel_pitch_um')
        settings.update(roi=original.get('roi'), noise_sigma=original['noise_sigma'],
                        subtract_border=original['subtract_border'],
                        scale=pitch / original['magnification'] if pitch else None,
                        maximum=meta['metrics']['maximum_dn'])
        source.update(kind='snapshot', timestamp=meta.get('timestamp'),
                      camera=(meta.get('camera') or {}).get('model'))
    dark_warning = bool(dark is not None and analyze(dark, settings['maximum'])[0]['valid'])
    return raw, dark, settings, source, dark_warning


def measure(frame, options=None):
    raw, dark, defaults, source, dark_warning = frame
    settings = dict(defaults if options is None else options)
    height, width = raw.shape
    roi = settings['roi']
    scale, sigma, maximum = settings['scale'], settings['noise_sigma'], settings['maximum']
    if roi is not None and not (isinstance(roi, list) and len(roi) == 4 and
            all(type(i) is int for i in roi) and 0 <= roi[0] <= roi[2]-8 <= width-8 and
            0 <= roi[1] <= roi[3]-8 <= height-8):
        raise ValueError("Select a region at least 8 × 8 pixels inside the frame.")
    if scale is not None and (not math.isfinite(scale) or scale <= 0):
        raise ValueError("Enter a positive image scale or leave it blank for pixels.")
    if not 0 <= sigma <= 10 or not isinstance(settings['subtract_border'], bool):
        raise ValueError("Invalid analysis settings.")
    if not isinstance(maximum, int) or not int(raw.max()) <= maximum <= 65535 or maximum < 1:
        raise ValueError("Full scale must cover the largest pixel and be between 1 and 65535 DN.")
    kwargs = dict(pixel_pitch_um=scale, roi=roi, subtract_border=settings['subtract_border'], dark=dark)
    m, px, py = analyze(raw, maximum, noise_sigma=sigma, **kwargs)
    if dark_warning:
        m['warnings'].append("The snapshot dark reference contains beam-like signal.")
    sensitivity = None
    if m['valid'] and m['noise_dn'] > 0:
        other_sigma = 5. if sigma < 5 else 3.
        other = analyze(raw, maximum, noise_sigma=other_sigma, **kwargs)[0]
        if other['valid']:
            change = max(abs(other[k]/m[k]-1) for k in ('diameter_x', 'diameter_y'))
            if change > .1:
                sensitivity = (f"D4σ changes by {change:.0%} when the threshold moves from "
                               f"{sigma:g}σ to {other_sigma:g}σ. Check the ROI for background noise or weak tails.")
    levels = Image.fromarray(np.round(np.clip(raw.astype(np.float32)/maximum, 0, 1)*255).astype('uint8'))
    levels.thumbnail((720, 480), Image.Resampling.BOX)
    def profile(values, start):
        indices = np.linspace(0, len(values)-1, min(320, len(values))).round().astype(int)
        return {'position': (indices + start).tolist(), 'dn': values[indices].tolist()}
    return {'software': __version__, 'source': source, 'settings': settings, 'metrics': m,
            'width': width, 'height': height, 'sensitivity_warning': sensitivity,
            'preview': encoded_image(Image.fromarray(LUT[np.asarray(levels)])),
            'profiles': {'x': profile(px, m['roi'][0]), 'y': profile(py, m['roi'][1])}}


def fit(frame, metrics, model):
    from beam_profiler.fitting import fit_frame
    result = fit_frame(frame[0], frame[1], metrics, model)
    residual = result.residual
    limit = float(np.max(np.abs(residual)))
    preview = Image.fromarray(residual)
    preview.thumbnail((480, 320), Image.Resampling.BOX)
    levels = np.clip(np.asarray(preview)/(limit or 1), -1., 1.)
    rgb = np.stack([1+np.minimum(0, levels), 1-np.abs(levels), 1-np.maximum(0, levels)], axis=-1)
    return {'summary': result.summary, 'limit': limit,
            'preview': encoded_image(Image.fromarray(np.rint(rgb*255).astype('uint8')))}, residual
