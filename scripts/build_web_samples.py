"""Regenerate the five small, instant-start examples from the current analysis code."""
import base64
from io import BytesIO
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web')]
from beam_profiler.fitting import MODELS, intensity
from bridge import load_frame, measure


def main():
    folder = ROOT / 'web' / 'samples'
    folder.mkdir(exist_ok=True)
    y, x = np.mgrid[:256, :384]
    angle = np.deg2rad(-21.)
    dx, dy = x - 192.4, y - 127.7
    u, v = dx*np.cos(angle) + dy*np.sin(angle), dy*np.cos(angle) - dx*np.sin(angle)
    for i, mode in enumerate(MODELS):
        q = intensity(mode, u/57, v/(57 if mode == 'lg01' else 36), order=3.2)
        raw = np.rint(46 + 3840*q + np.random.default_rng(823+i).normal(0, 1.8, q.shape)).astype('uint16')
        path = folder / (mode + '.png')
        Image.fromarray(raw).save(path, optimize=True)
        frame = load_frame(path.read_bytes())
        frame[2].update(maximum=4095, scale=3.45)
        frame[3].update(kind='sample', model=mode)
        packet = measure(frame)
        (folder / (mode+'.json')).write_text(json.dumps(packet, separators=(',', ':'), allow_nan=False))
        with Image.open(BytesIO(base64.b64decode(packet['preview']))) as thumb:
            thumb = thumb.crop((110, 48, 274, 208))
            thumb.thumbnail((92, 90))
            thumb.save(folder / (mode+'-thumb.png'))


if __name__ == '__main__':
    main()
