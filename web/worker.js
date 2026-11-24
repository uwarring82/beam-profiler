// Native pixels and Python objects live in this worker for the active frame.
const runtimeURL = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
let runtime, activeSource;
const message = text => self.postMessage({status: text});

async function engine() {
  if (runtime) return runtime;
  message('Loading the browser analysis engine…');
  importScripts(runtimeURL + 'pyodide.js');
  const py = await loadPyodide({indexURL: runtimeURL});
  await py.loadPackage(['numpy', 'pillow']);
  py.FS.mkdir('/home/pyodide/beam_profiler');
  for (const file of ['../beam_profiler/__init__.py', '../beam_profiler/analysis.py',
                      '../beam_profiler/fitting.py', 'bridge.py']) {
    const response = await fetch(new URL(file, self.location.href));
    if (!response.ok) throw new Error('Could not load the Beam Lab analysis code.');
    py.FS.writeFile('/home/pyodide/' + file.replace('../', ''), await response.text());
  }
  py.runPython('import json, bridge\nfrom PIL import Image');
  runtime = py;
  return py;
}

self.onmessage = async ({data}) => {
  try {
    const py = await engine();
    let packet;
    if (data.id !== activeSource || data.action === 'measure') {
      message('Measuring native pixels…');
      if (data.id !== activeSource) {
        const bytes = data.file ? new Uint8Array(await data.file.arrayBuffer()) :
          new Uint8Array(await (async () => {
            const res = await fetch(new URL('samples/' + data.sample + '.png', self.location.href));
            if (!res.ok) throw new Error('The sample image could not be loaded.');
            return res.arrayBuffer();
          })());
        py.FS.writeFile('/frame', bytes);
        try { py.runPython("next_frame = bridge.load_frame(open('/frame','rb').read())"); }
        finally { py.FS.unlink('/frame'); }
        if (data.sample) {
          py.globals.set('sample_mode', data.sample);
          py.runPython("next_frame[2].update(maximum=4095, scale=3.45)\nnext_frame[3].update(kind='sample', model=sample_mode)");
        }
      } else {
        py.runPython('next_frame = frame');
      }
      py.globals.set('options_json', JSON.stringify(data.settings ?? null));
      packet = JSON.parse(py.runPython(
        'next_packet = bridge.measure(next_frame, json.loads(options_json))\n' +
        'frame, packet = next_frame, next_packet\njson.dumps(packet, allow_nan=False)'));
      activeSource = data.id;
      py.runPython('residual = None');
    }
    if (data.action === 'fit') {
      message('Loading the fitting tools…');
      await py.loadPackage('scipy');
      message('Fitting the selected intensity models…');
      py.globals.set('model', data.model);
      const fit = JSON.parse(py.runPython(
        "fit_packet, residual = bridge.fit(frame, packet['metrics'], model)\njson.dumps(fit_packet, allow_nan=False)"));
      const bytes = py.runPython("bridge.image_bytes(Image.fromarray(residual), 'TIFF')");
      try {
        const tiff = bytes.toJs();
        self.postMessage({fit, packet, tiff}, [tiff.buffer]);
      } finally { bytes.destroy(); }
    } else {
      self.postMessage({packet});
    }
  } catch (err) {
    const lines = String(err?.message || err).trim().split('\n');
    self.postMessage({error: lines.at(-1).replace(/^(ValueError|OSError|UnidentifiedImageError): /, '')});
  }
};
