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

// Distance from x0 to where fn falls to level, searching along +1 or -1.
function edge(fn, x0, sign, level, limit) {
  let lo = 0, hi = Math.max(limit, 1e-9);
  if (fn(x0 + sign * hi) >= level) return Infinity;
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    if (fn(x0 + sign * mid) >= level) lo = mid; else hi = mid;
  }
  return (lo + hi) / 2;
}

// Best focus offset and distance within the coupler's travel and the bench.
export function solveLaunch(path, target, launch) {
  const {w0, f} = launchGeometry(path), lam = path.wavelength;
  const {travel, minDistance, maxDistance} = launch;
  if (!(travel > 0) || !(minDistance >= 0) || !(maxDistance > minDistance))
    throw Error('Enter a positive focus travel and a distance range with maximum above minimum.');
  const eta = (delta, D) => launchOverlap(target, w0, lam, f, delta, D);
  const n = 400;
  let best = {delta: 0, D: minDistance, eta: -1};
  for (let i = 0; i <= n; i++) {
    const delta = -travel + 2 * travel * i / n;
    for (let j = 0; j <= n; j++) {
      const D = minDistance + (maxDistance - minDistance) * j / n;
      const value = eta(delta, D);
      if (value > best.eta) best = {delta, D, eta: value};
    }
  }
  // The optimum is a narrow ridge: delta sets the image distance. Refine the
  // best distance for each delta inside the neighbouring grid cells.
  const dStep = 2 * travel / n, DStep = (maxDistance - minDistance) / n;
  const bestD = delta => golden(D => eta(delta, D),
    Math.max(minDistance, best.D - 4 * DStep), Math.min(maxDistance, best.D + 4 * DStep), 1e-7);
  const seed = imagingSeed(target, w0, lam, f);
  const candidates = [best.delta];
  if (seed && Math.abs(seed.delta) <= travel) candidates.push(seed.delta);
  for (const start of candidates) {
    const delta = golden(x => eta(x, bestD(x)),
      Math.max(-travel, start - 2 * dStep), Math.min(travel, start + 2 * dStep), 1e-10);
    const D = bestD(delta), value = eta(delta, D);
    if (value > best.eta) best = {delta, D, eta: value};
  }
  const level = 0.99 * best.eta;
  const tolerance = {
    delta: Math.min(edge(x => eta(x, best.D), best.delta, 1, level, 2 * travel),
      edge(x => eta(x, best.D), best.delta, -1, level, 2 * travel)),
    D: Math.min(edge(x => eta(best.delta, x), best.D, 1, level, maxDistance),
      edge(x => eta(best.delta, x), best.D, -1, level, Math.max(best.D, 1e-6)))
  };
  const limits = [];
  if (travel - Math.abs(best.delta) < 1e-6) limits.push('focus travel');
  if (best.D - minDistance < 1e-4) limits.push('minimum distance');
  if (maxDistance - best.D < 1e-4) limits.push('maximum distance');
  return {...best, tolerance, limits};
}

// Power through a circular clear aperture of diameter a (mm) at the lens.
export function apertureTransmission(path, aperture) {
  const {w0, f, delta} = launchGeometry(path);
  const w = spot(drift(waistQ(w0, path.wavelength), f + delta), path.wavelength).w / 1000;
  const radius = aperture / 2;
  return {w, transmission: 1 - Math.exp(-2 * radius * radius / (w * w))};
}
