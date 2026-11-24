// Gaussian propagation in mm. Input radii are 1/e² intensity radii in µm.
export const axes = ['t', 's'];
export const maxLenses = 12;

export function example() {
  return {
    wavelength: 560, length: 600,
    source: {t: {w: 400, z: 0}, s: {w: 400, z: 0}},
    targetType: 'ring', target: {t: {w: 200, z: 0}, s: {w: 200, z: 0}},
    ring: {roc: 100, angle: 8, short: 115, long: 380, toMirror: 120, crystal: 10, index: 1.65, cut: 'normal'},
    lenses: [{z: 120, f: 150, axis: 'both', locked: false}, {z: 350, f: 100, axis: 'both', locked: false}]
  };
}

function range(n, lo, hi, label) {
  if (!Number.isFinite(n) || n < lo || n > hi) throw Error(`${label} must be between ${lo} and ${hi}.`);
}

function checkWaists(pair) {
  for (const a of axes) {
    range(pair[a].w, 0.1, 100000, 'Waist radius (µm)');
    range(pair[a].z, -100000, 100000, 'Waist location (mm)');
  }
}

export function validatePath(p) {
  range(p.wavelength, 1, 100000, 'Pump wavelength (nm)');
  range(p.length, 1, 100000, 'Path length (mm)');
  checkWaists(p.source);
  if (!Array.isArray(p.lenses) || p.lenses.length > maxLenses) throw Error('Use up to 12 lenses.');
  for (const lens of p.lenses) {
    range(lens.z, 0, p.length, 'Lens position (mm)');
    checkFocus(lens.f);
    if (!['both', ...axes].includes(lens.axis) || typeof lens.locked !== 'boolean') throw Error('Invalid lens type or lock.');
  }
}

export function checkFocus(f) {
  if (!Number.isFinite(f) || Math.abs(f) < 0.1 || Math.abs(f) > 100000)
    throw Error('Enter a signed focal length with magnitude 0.1–100000 mm.');
}

// 1/e² radii of the camera-x and camera-y marginals of an elliptical Gaussian
// with principal radii wu, wv and u axis at angle degrees from +x toward +y.
export function cameraRadii(wu, wv, angle = 0) {
  const c = Math.cos(angle*Math.PI/180), s = Math.sin(angle*Math.PI/180);
  return {x: Math.sqrt(wu*wu*c*c + wv*wv*s*s), y: Math.sqrt(wu*wu*s*s + wv*wv*c*c),
    tilt: Math.abs(((angle % 90) + 135) % 90 - 45)};
}

export function waistQ(w, wavelength, distance = 0) {
  return {re: distance, im: Math.PI * w * w / wavelength};
}

export function drift(q, d) { return {re: q.re + d, im: q.im}; }

export function lensQ(q, f) {
  const a = 1 - q.re / f, b = q.im / f, den = a*a + b*b;
  return {re: (a*q.re - b*q.im) / den, im: q.im / den};
}

export function spot(q, wavelength) {
  const norm = q.re*q.re + q.im*q.im;
  return {w: Math.sqrt(wavelength * norm / (Math.PI*q.im)),
    curvature: q.re / norm, waist: Math.sqrt(wavelength*q.im/Math.PI), rayleigh: q.im};
}

export function axisOverlap(a, b) {
  // Squared overlap integral in one transverse dimension.
  return Math.min(1, 2*Math.sqrt(a.im*b.im) / Math.hypot(a.re-b.re, a.im+b.im));
}

export function sourceQ(p) {
  return axes.map(a => waistQ(p.source[a].w, p.wavelength, -p.source[a].z));
}

export function lensOrder(lenses) { return [...lenses].sort((a,b) => a.z-b.z); }

export function propagate(initial, ordered, z) {
  let q = [...initial], previous = 0;
  for (const lens of ordered) {
    if (lens.z > z) break;
    q = q.map((v, i) => {
      const before = drift(v, lens.z-previous);
      return lens.axis === 'both' || lens.axis === axes[i] ? lensQ(before, lens.f) : before;
    });
    previous = lens.z;
  }
  return q.map(v => drift(v, z-previous));
}

export function overlap(q, target) {
  return axisOverlap(q[0], target[0]) * axisOverlap(q[1], target[1]);
}

function multiply([a,b,c,d], [e,f,g,h]) {
  return [a*e+b*g, a*f+b*h, c*e+d*g, c*f+d*h];
}

// Reference plane is a flat input coupler. The short arm crystal is centered,
// parallel faced; its length is measured along the central ray inside it.
export function ringMode(r, wavelength) {
  range(r.roc, .1, 100000, 'Mirror radius (mm)');
  range(r.angle, 0, 85, 'Mirror incidence from normal (degrees)');
  range(r.short, .1, 100000, 'Short arm (mm)');
  range(r.long, .1, 100000, 'Return arm (mm)');
  range(r.toMirror, 0, r.long, 'Coupler to first curved mirror (mm)');
  range(r.crystal, 0, r.short, 'Crystal length in the short arm (mm)');
  range(r.index, 1, 5, 'Crystal refractive index');
  if (!['normal', 'brewster'].includes(r.cut)) throw Error('Select a supported crystal cut.');
  const cos = Math.cos(r.angle*Math.PI/180);
  const free = d => [1, d, 0, 1];
  const modes = axes.map(a => {
    // At a coplanar Brewster face w_t increases by n. The two faces
    // enclose an air-equivalent drift L/n³ in T and L/n in S.
    const stretch = r.cut === 'brewster' && a === 't' ? r.index : 1;
    const reduced = r.short - r.crystal + r.crystal/(r.index*stretch*stretch);
    const f = r.roc/2 * (a === 't' ? cos : 1/cos);
    const reflection = [1,0,-1/f,1];
    const elements = [free(r.toMirror), reflection, free(reduced), reflection, free(r.long-r.toMirror)];
    const [A,,C,D] = elements.reduce((prev, el) => multiply(el, prev), [1,0,0,1]);
    const g = (A+D)/2;
    if (Math.abs(g) >= 1-1e-12) return {g};
    const q = {re: (A-D)/(2*C), im: Math.sqrt((1-g)*(1+g))/Math.abs(C)};
    const center = drift(lensQ(drift(q, r.toMirror), f), reduced/2);
    return {q, g, crystalRadius: stretch * spot(center, wavelength).w};
  });
  const unstable = axes.filter((_,i) => !modes[i].q);
  if (unstable.length) {
    const planes = unstable.map(a => a==='t'?'tangential':'sagittal').join(' and ');
    throw Error('Cavity is unstable or marginal in the '+planes+(unstable.length===1?' plane.':' planes.')+' Change the mirror geometry.');
  }
  return {q: modes.map(m=>m.q), trace: modes.map(m=>m.g), crystalRadius: modes.map(m=>m.crystalRadius)};
}

export function targetMode(p) {
  if (p.targetType === 'ring') return ringMode(p.ring, p.wavelength);
  if (p.targetType !== 'waists') throw Error('Choose a supported cavity target.');
  checkWaists(p.target);
  // A positive target z is a waist downstream of the coupler.
  return {q: axes.map(a => waistQ(p.target[a].w, p.wavelength, -p.target[a].z))};
}
