"""File import and cached browser sample parity with the shared analysis engine."""
from io import BytesIO
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
import pytest

from web import bridge


SAMPLES = Path(__file__).resolve().parents[1] / 'web' / 'samples'


@pytest.mark.parametrize('model', ['gaussian', 'super_gaussian', 'hg10', 'hg11', 'lg01'])
def test_sample_measurements_and_fit_use_native_pixels(model):
    frame = bridge.load_frame((SAMPLES / (model+'.png')).read_bytes())
    cached = json.loads((SAMPLES / (model+'.json')).read_text())
    packet = bridge.measure(frame, cached['settings'])
    for key in ['diameter_x', 'diameter_y', 'centroid_x_px', 'centroid_y_px', 'peak_dn']:
        assert packet['metrics'][key] == pytest.approx(cached['metrics'][key])
    out, residual = bridge.fit(frame, packet['metrics'], model)
    assert out['summary']['candidates'][0]['rmse_dn'] < 2
    assert residual.shape == (256, 384)
    assert frame[0].dtype == np.uint16


def test_snapshot_reuses_dark_reference_scale_and_roi():
    raw = (SAMPLES/'hg10.png').read_bytes()
    dark = np.full((256,384), 23.5, dtype=np.float32)
    meta = {'timestamp':'2026-11-24T00:00:00Z','camera':{'model':'Simulated'},
            'settings':{'roi':[10,10,374,246],'noise_sigma':3.,'subtract_border':True,
                        'pixel_pitch_um':3.45,'magnification':2},'metrics':{'maximum_dn':4095}}
    archive = BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('raw.tiff', bridge.image_bytes(Image.open(BytesIO(raw)), 'TIFF'))
        z.writestr('dark-reference.tiff', bridge.image_bytes(Image.fromarray(dark), 'TIFF'))
        z.writestr('measurement.json', json.dumps(meta))
    frame = bridge.load_frame(archive.getvalue())
    packet = bridge.measure(frame)
    assert packet['source']['dark_active'] and packet['source']['timestamp'] == meta['timestamp']
    assert packet['metrics']['scale'] == 1.725 and packet['metrics']['roi'] == meta['settings']['roi']
    assert packet['metrics']['background_dn'] == pytest.approx(22.5, abs=1.)
    out, residual = bridge.fit(frame, packet['metrics'], 'hg10')
    assert out['summary']['candidates'][0]['radius_u_px'] == pytest.approx(57., abs=.05)
    assert residual.shape == (236,364)


def test_width_sensitivity_cautions_without_blocking_fit():
    y,x = np.mgrid[:720,:960]
    raw = np.rint(35 + 2500*np.exp(-2*((x-600.)**2+(y-325.)**2)/5**2)
                  + np.random.default_rng(53).normal(0,2,x.shape)).astype('uint16')
    frame = bridge.load_frame(bridge.image_bytes(Image.fromarray(raw), 'TIFF'))
    m = bridge.measure(frame)
    assert m['sensitivity_warning'] and not m['metrics']['warnings']
    row = bridge.fit(frame, m['metrics'], 'gaussian')[0]['summary']['candidates'][0]
    assert row['radius_u_px'] == pytest.approx(5., abs=.03)


def test_bad_image_and_out_of_bounds_settings_are_rejected():
    with pytest.raises(ValueError, match='monochrome'):
        bridge.load_frame(bridge.image_bytes(Image.new('RGB', (32,32)), 'PNG'))
    frame = bridge.load_frame((SAMPLES/'gaussian.png').read_bytes())
    for options in [{'roi':[-1,0,20,20]}, {'maximum':255}, {'scale':-1}]:
        with pytest.raises(ValueError):
            bridge.measure(frame, {**frame[2],**options})
