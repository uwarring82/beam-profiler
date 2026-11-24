import {cameraRadii, maxLenses, targetMode, validatePath} from './optics.mjs';
import {showcase} from './showcases.mjs';

// A shared link carries the original fit parameters separately from the
// editable source. Restoring the provenance must never overwrite user edits.
function readFit(data) {
  if (!data || typeof data !== 'object') throw Error('Invalid fit.');
  const principal = 'wu' in data || 'wv' in data;
  const keys = principal ? ['wu', 'wv'] : ['wt', 'ws'];
  const fit = {};
  for (const key of keys) {
    const value = data[key];
    if (!Number.isFinite(value) || value < 0.1 || value > 100000) throw Error('Invalid fit radius.');
    fit[key] = value;
  }
  if (principal) {
    fit.angle = data.angle === undefined ? 0 : data.angle;
    if (!Number.isFinite(fit.angle)) throw Error('Invalid fit angle.');
  }
  return fit;
}

export function fitProjection(fit) {
  if ('wu' in fit) return cameraRadii(fit.wu, fit.wv, fit.angle);
  return {x: fit.wt, y: fit.ws}; // Older profiler links carry no measured orientation.
}

function readShape(shape, input) {
  if (Array.isArray(shape)) {
    if (!Array.isArray(input) || input.length > maxLenses) throw Error();
    return input.map(item => readShape(shape[0], item));
  }
  if (typeof shape === 'object') {
    if (!input || typeof input !== 'object') throw Error();
    return Object.fromEntries(Object.entries(shape).map(([k,v]) => [k,readShape(v,input[k])]));
  }
  if (typeof input !== typeof shape || (typeof input === 'number' && !Number.isFinite(input))) throw Error();
  return input;
}

export function openLayout(url) {
  const params = url.searchParams;
  const stage = ['bbo', 'fiber'].includes(params.get('stage')) ? params.get('stage') : 'lbo';
  const layout = {stage, path: showcase(stage), gap: 10, fit: null, notice: ''};
  if (url.hash.startsWith('#layout=')) {
    try {
      if (url.hash.length > 20000) throw Error();
      const data = JSON.parse(decodeURIComponent(url.hash.slice(8)));
      if (data.v !== 1) throw Error();
      // Fibre links saved before datasheet confirmations existed keep their optics.
      if (stage === 'fiber' && data.path?.launch && typeof data.path.launch === 'object' && data.path.launch.confirmed === undefined)
        data.path.launch.confirmed = structuredClone(layout.path.launch.confirmed);
      const incoming = readShape(layout.path, data.path);
      validatePath(incoming);
      targetMode(incoming);
      layout.path = incoming;
      if (Number.isFinite(data.gap) && data.gap >= 0 && data.gap <= incoming.length) layout.gap = data.gap;
      if (data.fit != null) {
        try { layout.fit = readFit(data.fit); }
        catch { layout.notice = 'Layout loaded. Its original profiler fit could not be read.'; }
      }
    } catch {
      layout.notice = 'This layout link could not be read. The original showcase is loaded.';
    }
    return layout;
  }

  if (['wu','wv','wt','ws'].some(k => params.has(k))) {
    try {
      const values = {};
      for (const key of ['wu','wv','angle','wt','ws']) {
        if (params.has(key)) values[key] = params.get(key).trim() === '' ? NaN : Number(params.get(key));
      }
      layout.fit = readFit(values);
      const {x,y} = fitProjection(layout.fit);
      layout.path.source.t.w = Number(x.toPrecision(8));
      layout.path.source.s.w = Number(y.toPrecision(8));
    } catch {
      layout.notice = 'Invalid profiler radii or angle. The showcase launch is loaded.';
    }
  }
  return layout;
}

export function shareLayout(url, {stage, path, gap, fit}) {
  const link = new URL(url);
  link.search = new URLSearchParams({stage}).toString();
  const stored = {...path};
  // An unfinished edit in the hidden target form cannot affect the active design.
  const unused = path.targetType === 'waists' ? 'ring' : 'target';
  const defaults = showcase(stage);
  try { readShape(defaults[unused], stored[unused]); }
  catch { stored[unused] = defaults[unused]; }
  const data = {v: 1, path: stored};
  if (Number.isFinite(gap) && gap >= 0 && gap <= path.length) data.gap = gap;
  if (fit) data.fit = fit;
  link.hash = 'layout=' + encodeURIComponent(JSON.stringify(data));
  return link;
}
