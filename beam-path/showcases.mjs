import {example} from './optics.mjs';

// Friedenauer et al. (2006), §3 and Table 1. The external launch and
// coupler position are illustrative. Refractive indices are model inputs.
export const showcases = {
  lbo: {name: 'LBO · infrared to green', pump: 1118, harmonic: 559, short: 62,
    total: 400, fold: 10, crystal: 18, cut: 'normal', index: 1.60,
    designWaist: 27.3, inputPower: 1.80, outputPower: 0.95, efficiency: 52.7},
  bbo: {name: 'BBO · green to ultraviolet', pump: 559, harmonic: 279.5, short: 59.4,
    total: 470, fold: 27.4, crystal: 10, cut: 'brewster', index: 1.67,
    designWaist: 19.4, inputPower: 0.95, outputPower: 0.275, efficiency: 28.9}
};

export function showcase(id) {
  const ref = showcases[id];
  if (!ref) throw Error('Unknown showcase.');
  const p = example();
  p.wavelength = ref.pump;
  const long = ref.total-ref.short;
  p.ring = {roc: 50, angle: ref.fold/2, short: ref.short, long,
    toMirror: long/3, crystal: ref.crystal, cut: ref.cut, index: ref.index};
  return p;
}
