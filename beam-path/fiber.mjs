import {axisOverlap, drift, lensQ, spot, waistQ} from './optics.mjs';

// Fibre launch: the fibre tip sits at the rail origin and its fundamental
// mode is a Gaussian waist of radius MFD/2. The output coupler is a thin lens
// of focal length f at f + delta from the tip; its focus adjustment sets delta.
// The rail ends at the cavity input coupler, D after the lens.
// Lengths in mm, radii in µm, wavelength in nm, as in optics.mjs.

export function launchQ(w0, wavelength, f, delta, D) {
  return drift(lensQ(drift(waistQ(w0, wavelength), f + delta), f), D);
}

export function launchOverlap(target, w0, wavelength, f, delta, D) {
  const q = launchQ(w0, wavelength, f, delta, D);
  return axisOverlap(q, target[0]) * axisOverlap(q, target[1]);
}

// Waist-to-waist imaging by one thin lens onto the mean of the two target
// planes. Needs f > f0 = π w0 wt / λ; returns null otherwise.
export function imagingSeed(target, w0, wavelength, f) {
  const zR = (target[0].im + target[1].im) / 2, past = (target[0].re + target[1].re) / 2;
  const wt = Math.sqrt(wavelength * zR / Math.PI);
  const f0 = Math.PI * w0 * wt / wavelength;
  if (!(f > f0)) return null;
  const s = Math.sqrt(f * f - f0 * f0);
  return {delta: (w0 / wt) * s, D: f + (wt / w0) * s + past};
}

// Read the launch from a path: collimator = first lens, tip at z = 0.
export function launchGeometry(path) {
  if (path.lenses.length !== 1) throw Error('The fibre launch uses the collimator alone. Remove the other lenses to use it.');
  const [lens] = path.lenses, w0 = path.source.t.w;
  if (lens.axis !== 'both') throw Error('The fibre collimator must be a spherical lens.');
  if (path.source.s.w !== w0 || path.source.t.z !== 0 || path.source.s.z !== 0)
    throw Error('The fibre mode is round, with its waist at the fibre tip (position 0 mm).');
  return {w0, f: lens.f, delta: lens.z - lens.f, D: path.length - lens.z};
}

export function applyLaunch(path, delta, D) {
  const lens = path.lenses[0];
  lens.z = lens.f + delta;
  path.length = lens.z + D;
}

function golden(fn, lo, hi, tolerance) {
  const r = (Math.sqrt(5) - 1) / 2;
  let a = lo, b = hi, c = b - r * (b - a), d = a + r * (b - a), fc = fn(c), fd = fn(d);
  while (b - a > tolerance) {
    if (fc >= fd) { b = d; d = c; fd = fc; c = b - r * (b - a); fc = fn(c); }
    else { a = c; c = d; fc = fd; d = a + r * (b - a); fd = fn(d); }
  }
  return (a + b) / 2;
}

// Distance from x0 to where fn falls to level, searching along +1 or -1
// up to a mechanical stop `room` away. Null: already at the stop.
// Infinity: the level is not reached before the stop.
function edge(fn, x0, sign, level, room) {
  if (!(room > 1e-9)) return null;
  if (fn(x0 + sign * room) >= level) return Infinity;
  let lo = 0, hi = room;
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    if (fn(x0 + sign * mid) >= level) lo = mid; else hi = mid;
  }
  return (lo + hi) / 2;
}

// For fixed focus the overlap against distance has a single broad peak per
// axis: scan, then refine between the neighbours of the best sample.
function bestDistance(eta, delta, lo, hi) {
  const n = 60, step = (hi - lo) / n;
  let bj = 0, bv = -1;
  for (let j = 0; j <= n; j++) { const v = eta(delta, lo + j * step); if (v > bv) { bv = v; bj = j; } }
  const D = golden(x => eta(delta, x), Math.max(lo, lo + (bj - 1) * step), Math.min(hi, lo + (bj + 1) * step), 1e-7 * (1 + hi));
  const v = eta(delta, D);
  return v >= bv ? {D, eta: v} : {D: lo + bj * step, eta: bv};
}

// The optimum is a narrow ridge in (focus, distance): the distance must
// follow the focus. Re-optimise the distance at every focus value instead
// of gridding both, and evaluate the imaging estimate as its own start.
function search(eta, dLo, dHi, DLo, DHi, seeds) {
  let best = {eta: -1};
  const consider = delta => { const r = bestDistance(eta, delta, DLo, DHi); if (r.eta > best.eta) best = {delta, D: r.D, eta: r.eta}; };
  const n = dHi > dLo ? 800 : 0, step = n ? (dHi - dLo) / n : 0;
  for (let i = 0; i <= n; i++) consider(dLo + i * step);
  for (const seed of seeds) {
    if (seed.delta < dLo || seed.delta > dHi) continue;
    if (seed.D >= DLo && seed.D <= DHi && eta(seed.delta, seed.D) > best.eta)
      best = {delta: seed.delta, D: seed.D, eta: eta(seed.delta, seed.D)};
    consider(seed.delta);
  }
  if (n) {
    const c = best.delta;
    consider(golden(x => bestDistance(eta, x, DLo, DHi).eta, Math.max(dLo, c - step), Math.min(dHi, c + step), 1e-10));
  }
  return best;
}

export function checkLaunch(w0, f, launch) {
  const {travel, aperture, minDistance, maxDistance} = launch;
  if (!(w0 > 0)) throw Error('Enter a positive mode field diameter.');
  if (!(f > 0)) throw Error('Enter a positive effective focal length.');
  if (!(travel > 0)) throw Error('Enter a positive focus travel.');
  if (!(aperture > 0)) throw Error('Enter a positive clear aperture.');
  if (!(minDistance >= 0) || !(maxDistance > minDistance)) throw Error('Enter a distance range with the longest above the shortest.');
}

// Best focus offset and distance within the coupler's travel and the bench.
// With lockFocus the current focus offset stays fixed. The result names
// active limits, the overlap without them, and one-sided 1% tolerances.
export function solveLaunch(path, target, launch, {lockFocus = false} = {}) {
  const {w0, f, delta: current} = launchGeometry(path), lam = path.wavelength;
  checkLaunch(w0, f, launch);
  const {travel, minDistance, maxDistance} = launch;
  if (lockFocus && Math.abs(current) > travel) throw Error('The locked focus setting lies outside the focus travel.');
  const eta = (delta, D) => launchOverlap(target, w0, lam, f, delta, D);
  const seed = imagingSeed(target, w0, lam, f);
  const seeds = seed ? [seed] : [];
  const dLo = lockFocus ? current : -travel, dHi = lockFocus ? current : travel;
  const best = search(eta, dLo, dHi, minDistance, maxDistance, seeds);

  const limits = [];
  if (lockFocus) limits.push('locked focus');
  else if (travel - Math.abs(best.delta) < 1e-6) limits.push('focus travel');
  if (best.D - minDistance < 1e-4) limits.push('shortest distance');
  if (maxDistance - best.D < 1e-4) limits.push('longest distance');
  // Without the limits: free focus within most of f, distance up to the
  // imaging estimate's reach or the bench, whichever is longer.
  const reach = Math.max(maxDistance, seed ? 2 * seed.D : 0, 1);
  const free = limits.length ? search(eta, -0.9 * f, 0.9 * f, 0, reach, seeds) : best;
  const ceiling = Math.max(free.eta, best.eta);

  const level = 0.99 * best.eta;
  const focus = d => eta(d, best.D), distance = D => eta(best.delta, D);
  const tolerance = {
    delta: lockFocus ? null : {minus: edge(focus, best.delta, -1, level, best.delta + travel),
      plus: edge(focus, best.delta, 1, level, travel - best.delta)},
    D: {minus: edge(distance, best.D, -1, level, best.D - minDistance),
      plus: edge(distance, best.D, 1, level, maxDistance - best.D)}
  };
  return {...best, ceiling, tolerance, limits};
}

// Power through a circular clear aperture of diameter a (mm) at the lens.
export function apertureTransmission(path, aperture) {
  if (!(aperture > 0)) throw Error('Enter a positive clear aperture.');
  const {w0, f, delta} = launchGeometry(path);
  const w = spot(drift(waistQ(w0, path.wavelength), f + delta), path.wavelength).w / 1000;
  const radius = aperture / 2;
  return {w, transmission: 1 - Math.exp(-2 * radius * radius / (w * w))};
}
