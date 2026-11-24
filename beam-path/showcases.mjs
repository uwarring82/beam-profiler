import {example} from './optics.mjs';

// Friedenauer et al. (2006), §3 and Table 1. The external launch and
// coupler position are illustrative. Refractive indices are model inputs.
// Rail lengths leave the two-lens optimum clear of the coupler clearance.
export const showcases = {
  lbo: {name: 'LBO · infrared to green', pump: 1118, harmonic: 559, short: 62,
    total: 400, fold: 10, crystal: 18, cut: 'normal', index: 1.60, rail: 600,
    designWaist: 27.3, inputPower: 1.80, outputPower: 0.95, efficiency: 52.7},
  bbo: {name: 'BBO · green to ultraviolet', pump: 559, harmonic: 279.5, short: 59.4,
    total: 470, fold: 27.4, crystal: 10, cut: 'brewster', index: 1.67, rail: 700,
    designWaist: 19.4, inputPower: 0.95, outputPower: 0.275, efficiency: 28.9},
  // A fibre output coupler alone matches the LBO cavity: the fibre mode is the
  // source waist, and the collimator's focus offset and distance are the two
  // free settings. Fibre and collimator values are illustrative.
  fiber: {name: 'Fibre launch · infrared to green', cavity: 'lbo', pump: 1118, harmonic: 559,
    mfd: 6.6, f: 11, offset: 0, distance: 600,
    launch: {travel: 0.5, aperture: 5.5, minDistance: 30, maxDistance: 1500,
      confirmed: {mfd: false, f: false, travel: false, aperture: false}}}
};

export function showcase(id) {
  if (!showcases[id]) throw Error('Unknown showcase.');
  if (showcases[id].cavity) return fiberShowcase(id);
  const ref = showcases[id];
  const p = example();
  p.wavelength = ref.pump;
  p.length = ref.rail;
  const long = ref.total-ref.short;
  p.ring = {roc: 50, angle: ref.fold/2, short: ref.short, long,
    toMirror: long/3, crystal: ref.crystal, cut: ref.cut, index: ref.index};
  return p;
}

function fiberShowcase(id) {
  const ref = showcases[id], p = showcase(ref.cavity);
  const w = ref.mfd / 2;
  p.source = {t: {w, z: 0}, s: {w, z: 0}};
  p.lenses = [{z: ref.f + ref.offset, f: ref.f, axis: 'both', locked: false}];
  p.length = ref.f + ref.offset + ref.distance;
  p.launch = structuredClone(ref.launch);
  return p;
}
