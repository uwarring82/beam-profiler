import test from 'node:test';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {axisOverlap, cameraRadii, drift, lensQ, lensOrder, overlap, propagate, ringMode, sourceQ, spot, targetMode, validatePath, waistQ} from '../beam-path/optics.mjs';
import {showcase, showcases} from '../beam-path/showcases.mjs';
import {optimize} from '../beam-path/optimize.mjs';
import {fitProjection, openLayout, shareLayout} from '../beam-path/layout.mjs';
import {lensMarkerAt} from '../beam-path/plot.mjs';
import {renderSearchResult} from '../beam-path/search-result.mjs';

function close(a,b,epsilon=1e-9){assert.ok(Math.abs(a-b)<epsilon, a+' != '+b);}
function sameQ(a,b){close(a.re,b.re);close(a.im,b.im);}

test('Free propagation yields the analytic Rayleigh length and radius',()=>{
  const q=waistQ(100,1000);
  close(q.im,10*Math.PI);close(spot(drift(q,q.im),1000).w,Math.sqrt(2)*100);
  close(spot(q,1000).curvature,0);
});
test('Thin lenses preserve radius and change reciprocal curvature by -1/f',()=>{
  const q=waistQ(75,780,137);
  for (const f of [-50,125]){
    const a=spot(q,780),b=spot(lensQ(q,f),780);
    close(a.w,b.w);close(b.curvature,a.curvature-1/f);
  }
});
test('A cylindrical lens acts on only its selected plane at its position',()=>{
  const p=showcase('lbo'), q=sourceQ(p), l={z:10,f:75,axis:'s'};
  const actual=propagate(q,[l],30);
  sameQ(actual[0],drift(q[0],30));sameQ(actual[1],drift(lensQ(drift(q[1],10),75),20));
  sameQ(propagate(q,[l],5)[1],drift(q[1],5));
  sameQ(propagate(q,[l],10)[1],lensQ(drift(q[1],10),75));
});
test('Gaussian power overlap includes curvature, ellipticity and is invariant under common optics',()=>{
  const a=waistQ(100,1000),b=waistQ(200,1000);
  close(overlap([a,a],[b,b]),(2*100*200/(100**2+200**2))**2);
  close(overlap([a,a],[a,b]),.8);
  const c=drift(a,-a.im),d=drift(a,a.im);
  close(spot(c,1000).w,spot(d,1000).w);close(axisOverlap(c,d)**2,.5);
  close(axisOverlap(c,b),axisOverlap(lensQ(drift(c,85),50),lensQ(drift(b,85),50)));
});
for(const stage of ['lbo','bbo']){
  test(stage+' eigenmode returns to the same q after a round trip',()=>{
    const p=showcase(stage),r=p.ring,mode=targetMode(p);
    for(const [i,q] of mode.q.entries()){
      const c=Math.cos(r.angle*Math.PI/180), f=r.roc/2*(i===0?c:1/c);
      const stretch=stage==='bbo'&&i===0?r.index:1;
      const short=r.short-r.crystal+r.crystal/(r.index*stretch**2);
      const after= lensQ(drift(q,r.toMirror),f);
      const center=drift(after,short/2);
      close(center.re,0); close(mode.crystalRadius[i],spot(center,p.wavelength).w*stretch);
      sameQ(drift(lensQ(drift(after,short),f),r.long-r.toMirror),q);
      assert.ok(Math.abs(mode.trace[i])<1);
    }
  });
}
test('Paper showcases use half-fold incidence and the correct round-trip geometry',()=>{
  const a=showcase('lbo'),b=showcase('bbo');
  for(const [i,p] of [a,b].entries()){
    close(p.ring.short+p.ring.long,[400,470][i]);close(p.ring.angle,[5,13.7][i]);
    close(p.ring.crystal,[18,10][i]);close(p.wavelength,[1118,559][i]);
  }
  const [t,s]=targetMode(a).crystalRadius;
  assert.ok(Math.abs(t-27.3)<.5 && Math.abs(s-27.3)<.5);
  assert.throws(()=>ringMode({...b.ring,cut:'normal'},b.wavelength),/tangential/);
  const r={...a.ring,angle:0,short:55,crystal:2};
  const brewster=ringMode({...r,cut:'brewster'},1118),normal=ringMode(r,1118);
  sameQ(brewster.q[1],normal.q[1]);assert.ok(Math.abs(brewster.q[0].im-normal.q[0].im)>1);
});
test('The confocal boundary and unstable rings are rejected',()=>{
  const r={...showcase('lbo').ring,angle:0,crystal:0,short:50};
  assert.throws(()=>ringMode(r,1118),/unstable or marginal/);
  assert.throws(()=>ringMode({...r,short:2},1118),/unstable or marginal/);
  const [t,s]=ringMode({...r,short:54},1118).q;sameQ(t,s);
});
test('A target waist downstream of the coupler has negative real q',()=>{
  const p=showcase('lbo');p.targetType='waists';p.target.t={w:100,z:120};
  const [q]=targetMode(p).q;close(q.re,-120);close(spot(drift(q,120),p.wavelength).w,100);
});
test('Optimization finds a better realizable layout for both published targets',()=>{
  for(const stage of ['lbo','bbo']){
    const p=showcase(stage), original=structuredClone(p);
    const fit=optimize(p,10);
    assert.deepEqual(p,original);assert.ok(fit.after>.97 && fit.after>fit.before+.2);
    close(fit.after,overlap(propagate(sourceQ(p),lensOrder(fit.lenses),p.length),targetMode(p).q));
    assert.deepEqual(fit.lenses.map(l=>l.f),p.lenses.map(l=>l.f));
  }
});
test('Catalog search preserves locks, signed focal length options, clearance and lens identity',()=>{
  const p=showcase('lbo');p.lenses.reverse();p.lenses[0].locked=true;
  const fit=optimize(p,12,[-75,80,150]);
  assert.deepEqual(fit.lenses[0],p.lenses[0]);assert.ok(fit.after>=fit.before);
  assert.ok([150,-75,80].includes(fit.lenses[1].f));
  const [left,right]=lensOrder(fit.lenses);
  assert.ok(left.z>=12 && right.z-left.z>=12 && p.length-right.z>=12);
  assert.throws(()=>optimize(p,150),/clearance/);
});
test('An exact input match is kept and invalid inputs are rejected',()=>{
  const p=showcase('lbo');p.targetType='waists';
  const q=propagate(sourceQ(p),lensOrder(p.lenses),p.length);
  ['t','s'].forEach((a,i)=>p.target[a]={w:spot(q[i],p.wavelength).waist,z:-q[i].re});
  close(optimize(p,10).after,1);
  assert.throws(()=>optimize(p,10,[0]),/focal length/);
  p.lenses=[];validatePath(p);assert.throws(()=>optimize(p,10),/add a lens/);
  assert.throws(()=>validatePath({...p,wavelength:NaN}),/wavelength/);
});

test('Showcase optima clear every constraint; a short BBO rail reports the coupler limit',()=>{
  for(const [stage,rail] of [['lbo',600],['bbo',700]]){
    const p=showcase(stage);close(p.length,rail);
    const fit=optimize(p,10);
    assert.deepEqual(fit.limits,[]);assert.ok(fit.after>.998,stage+' '+fit.after);
  }
  const short={...showcase('bbo'),length:600};
  const fit=optimize(short,10);
  assert.deepEqual(fit.limits,[{lens:1,side:'coupler'}]);
  close(fit.lenses[1].z,590,1e-6);assert.ok(fit.after<.99);
});
test('Gaussian fit radii project onto camera axes for T/S',()=>{
  let r=cameraRadii(300,200,0);close(r.x,300);close(r.y,200);close(r.tilt,0);
  r=cameraRadii(300,200,90);close(r.x,200);close(r.y,300);close(r.tilt,0);
  r=cameraRadii(300,200,-80);close(r.tilt,10);
  r=cameraRadii(300,200,30);close(r.x,Math.sqrt(300**2*.75+200**2*.25));close(r.y,Math.sqrt(300**2*.25+200**2*.75));close(r.tilt,30);
  r=cameraRadii(250,250,37);close(r.x,250);close(r.y,250);
});

test('Fit-to-separable overlap captures covariance even at four degrees',()=>{
  const r=cameraRadii(400,40,4);
  close(r.fitOverlap,0.8955588352155894); // Direct integration of the two normalized fields.
  close(cameraRadii(400,40,-4).fitOverlap,r.fitOverlap);
  close(cameraRadii(400,40,94).fitOverlap,r.fitOverlap);
  for (const angle of [0,90,180]) close(cameraRadii(400,40,angle).fitOverlap,1);
  close(cameraRadii(250,250,45).fitOverlap,1);
  assert.ok(cameraRadii(250,249,45).fitOverlap>.9999);
  const thin=cameraRadii(1e5,0.1,45);
  assert.ok(thin.fitOverlap>0 && thin.fitOverlap<.00001);
});

test('Sharing a tilted fit retains the original projection, edited launch and clearance',()=>{
  const url=new URL('https://example.org/beam-path/?stage=bbo&wu=400&wv=40&angle=4');
  const layout=openLayout(url);
  const original=fitProjection(layout.fit);
  close(layout.path.source.t.w,original.x,1e-5);
  close(layout.path.source.s.w,original.y,1e-5);
  layout.path.source.t.w=475;layout.path.source.s.z=15;
  layout.gap=24;
  const copied=shareLayout(url,layout);
  assert.deepEqual([...copied.searchParams.keys()],['stage']);
  const restored=openLayout(copied);
  assert.deepEqual(restored,layout);
  close(fitProjection(restored.fit).fitOverlap,0.8955588352155894);
});

test('Legacy fit links and v1 layouts without provenance retain their original meaning',()=>{
  const url=new URL('https://example.org/beam-path/?wt=228&ws=227');
  const legacy=openLayout(url);
  assert.deepEqual(fitProjection(legacy.fit),{x:228,y:227});
  assert.deepEqual(openLayout(shareLayout(url,legacy)),legacy);
  const path=showcase('lbo');path.length=800;
  url.hash='layout='+encodeURIComponent(JSON.stringify({v:1,path}));
  const old=openLayout(url);
  assert.equal(old.fit,null);assert.deepEqual(old.path,path);assert.equal(old.gap,10);
  url.hash='layout='+encodeURIComponent(JSON.stringify({v:1,path,fit:{wu:-10,wv:40,angle:4}}));
  const invalid=openLayout(url);
  assert.equal(invalid.fit,null);assert.deepEqual(invalid.path,path);assert.match(invalid.notice,/fit could not be read/);
});

test('Active coupler clearance survives applying, sharing and repeating a constrained search',()=>{
  const url=new URL('https://example.org/?stage=bbo');
  const layout=openLayout(url);layout.path.length=600;
  const proposal=optimize(layout.path,layout.gap);
  const ui={panel:{},apply:{},status:{},moves:{},limits:{}};
  renderSearchResult(proposal,ui);
  assert.equal(ui.apply.hidden,false);assert.equal(ui.limits.hidden,false);
  assert.match(ui.limits.textContent,/L2.*input coupler/);
  layout.path.lenses=proposal.lenses;
  renderSearchResult(proposal,ui,true);
  assert.equal(ui.apply.hidden,true);assert.equal(ui.limits.hidden,false);
  const reopened=openLayout(shareLayout(url,layout));
  const again=optimize(reopened.path,reopened.gap);
  close(again.before,again.after);
  renderSearchResult(again,ui);
  assert.match(ui.status.textContent,/No improvement/);
  assert.equal(ui.apply.hidden,true);assert.equal(ui.limits.hidden,false);
  assert.match(ui.limits.textContent,/L2.*input coupler/);
  renderSearchResult(optimize(showcase('bbo'),10),ui);
  assert.equal(ui.limits.hidden,true);assert.equal(ui.limits.textContent,'');
});

test('Both sides of each free lens are reported on a fully packed rail',()=>{
  const p=showcase('bbo');p.length=30;
  p.lenses[0].z=20;p.lenses[1].z=10; // Display IDs are independent of propagation order.
  const r=optimize(p,10);
  assert.deepEqual(r.limits,[
    {lens:1,side:'start'}, {lens:1,side:'lens',neighbor:0},
    {lens:0,side:'lens',neighbor:1}, {lens:0,side:'coupler'}
  ]);
  p.lenses[1].locked=true;
  assert.deepEqual(optimize(p,10).limits,r.limits.slice(2));
});

test('The entrance describes the wavelengths in the linked doubling chain', () => {
  const html = readFileSync(new URL('../start/index.html', import.meta.url), 'utf8');
  assert.ok(html.includes(showcases.lbo.pump + ' nm'));
  assert.ok(html.includes(showcases.bbo.harmonic + ' nm'));
  assert.equal(showcases.lbo.harmonic, showcases.bbo.pump);
});

test('A touch can grab the nearest lens across a 44 px target without moving the probe', () => {
  // One CSS pixel per mm, with the rail starting at x = 59 on the page.
  const canvas = {getBoundingClientRect: () => ({left: 10, top: 20, width: 671})};
  const path = {length: 600, lenses: [{z: 120}, {z: 150}]};
  const pick = (x, y, pointerType = 'touch') =>
    lensMarkerAt(canvas, {clientX: x, clientY: y, pointerType}, path);

  assert.equal(pick(179 - 22, 20), path.lenses[0]);
  assert.equal(pick(209 + 22, 20 + 44), path.lenses[1]);
  assert.equal(pick(179 - 23, 40), null);
  assert.equal(pick(179, 65), null);
  assert.equal(pick(190, 40), path.lenses[0]);
  assert.equal(pick(199, 40), path.lenses[1]);
  assert.equal(pick(179 - 22, 40, 'mouse'), null);
  assert.equal(pick(179, 40, 'mouse'), path.lenses[0]);
});

import {applyLaunch, apertureTransmission, imagingSeed, launchGeometry, launchOverlap, roundBound, solveLaunch} from '../beam-path/fiber.mjs';

test('The fibre showcase launches a round fibre mode through one collimator into the LBO cavity', () => {
  const p = showcase('fiber'), ref = showcases.fiber;
  assert.deepEqual(p.ring, showcase('lbo').ring);
  assert.deepEqual(p.source, {t: {w: ref.mfd/2, z: 0}, s: {w: ref.mfd/2, z: 0}});
  assert.equal(p.lenses.length, 1);
  assert.deepEqual(launchGeometry(p), {w0: ref.mfd/2, f: ref.f, delta: ref.offset, D: ref.distance});
  assert.equal(openLayout(new URL('https://x/beam-path/?stage=fiber')).stage, 'fiber');
  p.launch.travel = 0.25;
  const back = openLayout(shareLayout('https://x/beam-path/', {stage: 'fiber', path: p, gap: 10, fit: null}));
  assert.equal(back.stage, 'fiber'); assert.equal(back.path.launch.travel, 0.25);
  p.lenses.push({z: 300, f: 100, axis: 'both', locked: false});
  assert.throws(() => launchGeometry(p), /collimator alone/);
});

test('Waist-to-waist imaging matches a round target exactly', () => {
  const target = [waistQ(200, 1118, 60), waistQ(200, 1118, 60)];
  const seed = imagingSeed(target, 3.3, 1118, 11);
  close(launchOverlap(target, 3.3, 1118, 11, seed.delta, seed.D), 1, 1e-9);
  assert.equal(imagingSeed(target, 3.3, 1118, 1), null);  // f below f0 = π w0 wt / λ
});

test('The launch search finds the best focus and distance and their one-sided 1% tolerances', () => {
  const p = showcase('fiber'), target = targetMode(p).q;
  const found = solveLaunch(p, target, p.launch);
  let brute = 0;
  for (let i = 0; i <= 1000; i++) for (let j = 0; j <= 300; j++)
    brute = Math.max(brute, launchOverlap(target, 3.3, 1118, 11, -0.5 + i/1000, 30 + 1470*j/300));
  assert.ok(found.eta >= brute - 1e-9 && found.eta < 1, found.eta+' vs '+brute);
  assert.deepEqual(found.limits, []); close(found.ceiling, found.eta);
  const at = (d, D) => launchOverlap(target, 3.3, 1118, 11, d, D), {delta, D} = found.tolerance;
  for (const [x, sign] of [[delta.minus, -1], [delta.plus, 1]]) close(at(found.delta + sign*x, found.D), 0.99*found.eta, 1e-9);
  for (const [x, sign] of [[D.minus, -1], [D.plus, 1]]) close(at(found.delta, found.D + sign*x), 0.99*found.eta, 1e-9);
  applyLaunch(p, found.delta, found.D);
  close(overlap(propagate(sourceQ(p), lensOrder(p.lenses), p.length), target), found.eta);
});

test('A small fibre mode behind a short coupler reaches the imaging estimate (review regression)', () => {
  const p = showcase('fiber');
  p.source = {t: {w: 1.5, z: 0}, s: {w: 1.5, z: 0}}; p.lenses[0].f = 6; p.lenses[0].z = 6; p.length = 606;
  const target = targetMode(p).q, launch = {...p.launch, travel: 2};
  const found = solveLaunch(p, target, launch), seed = imagingSeed(target, 1.5, 1118, 6);
  assert.ok(found.eta >= launchOverlap(target, 1.5, 1118, 6, seed.delta, seed.D) - 1e-12);
  assert.ok(found.eta > 0.999 && Math.abs(found.D - seed.D) < 2, found.eta+' at '+found.D);
});

test('Stops are named, the unconstrained overlap is reported, and tolerances end at a stop', () => {
  const p = showcase('fiber'), target = targetMode(p).q;
  const free = solveLaunch(p, target, p.launch);
  const tight = solveLaunch(p, target, {...p.launch, travel: 0.05});
  assert.deepEqual(tight.limits, ['focus travel', 'longest distance']);
  assert.ok(tight.eta < 0.5); close(tight.ceiling, free.eta, 1e-6);
  close(tight.delta, 0.05); assert.equal(tight.tolerance.delta.plus, null); assert.equal(tight.tolerance.D.plus, null);
  assert.ok(tight.tolerance.delta.minus > 0 && tight.tolerance.D.minus > 0);
});

test('A locked coupler keeps its focus; only the distance is optimised', () => {
  const p = showcase('fiber'), target = targetMode(p).q;
  p.lenses[0].locked = true; p.lenses[0].z = 11.1;
  const found = solveLaunch(p, target, p.launch, {lockFocus: true});
  close(found.delta, 0.1); assert.equal(found.tolerance.delta, null);
  assert.deepEqual(found.limits.slice(0, 1), ['locked focus']); assert.ok(found.ceiling > found.eta);
  assert.throws(() => solveLaunch(p, target, {...p.launch, travel: 0.05}, {lockFocus: true}), /outside the focus travel/);
});

test('Invalid launch inputs are refused, and datasheet confirmations survive a shared link', () => {
  const p = showcase('fiber'), target = targetMode(p).q;
  assert.throws(() => apertureTransmission(p, -0.2), /positive clear aperture/);
  assert.throws(() => solveLaunch(p, target, {...p.launch, travel: NaN}), /focus travel/);
  assert.throws(() => solveLaunch(p, target, {...p.launch, maxDistance: 10}), /distance range/);
  assert.deepEqual(p.launch.confirmed, {mfd: false, f: false, travel: false, aperture: false});
  p.launch.confirmed.f = true;
  const back = openLayout(shareLayout('https://x/beam-path/', {stage: 'fiber', path: p, gap: 10, fit: null}));
  assert.deepEqual(back.path.launch.confirmed, {mfd: false, f: true, travel: false, aperture: false});
  assert.deepEqual(showcase('fiber').launch.confirmed.f, false);
});

test('Clear-aperture transmission follows the Gaussian power inside a circle', () => {
  const p = showcase('fiber');
  const {w, transmission} = apertureTransmission(p, 2);
  close(w, spot(drift(waistQ(3.3, 1118), 11), 1118).w / 1000);
  close(transmission, 1 - Math.exp(-2 / (w*w)));
});

test('Astigmatism is credited only when the target modes differ (review regression)', () => {
  const p = showcase('fiber');
  assert.ok(roundBound(targetMode(p).q) < 0.9995);                    // LBO: tangential and sagittal differ
  close(roundBound(targetMode(p).q), solveLaunch(p, targetMode(p).q, p.launch).eta, 1e-6);
  p.targetType = 'waists'; p.target = {t: {w: 200, z: 0}, s: {w: 200, z: 0}};
  p.source = {t: {w: 5, z: 0}, s: {w: 5, z: 0}}; p.lenses[0].f = 2; p.lenses[0].z = 2; p.length = 602;
  const target = targetMode(p).q, found = solveLaunch(p, target, p.launch);
  close(found.round, 1, 1e-9);                                           // a round target: no astigmatic loss
  assert.ok(found.f0 > 2 && found.eta < 0.95, found.f0+' '+found.eta); // the lens is too short to image the mode
});

test('Aperture transmission bounds the clipped coupling; the product with the ideal overlap does not', () => {
  // Integrate the clipped input field against the cavity mode traced back to the lens.
  const p = showcase('fiber'), target = targetMode(p).q, delta = 0.05, D = 1500, aperture = 3, lam = 1118;
  const k = 2*Math.PI/(lam*1e-6), qin = lensQ(drift(waistQ(3.3, lam), 11 + delta), 11), qt = target.map(q => drift(q, -D));
  const expo = (q, x) => { const d = q.re*q.re + q.im*q.im, c = -k*x*x/2; return [c*q.im/d, c*q.re/d]; };
  const field = (qx, qy, x, y) => { const [a, b] = expo(qx, x), [c, e] = expo(qy, y), m = Math.exp(a + c); return [m*Math.cos(b + e), m*Math.sin(b + e)]; };
  const n = 600, R = 12, h = 2*R/n; let pin = 0, pt = 0, sr = 0, si = 0;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    const x = -R + (i + .5)*h, y = -R + (j + .5)*h, [ar, ai] = field(qin, qin, x, y), [br, bi] = field(qt[0], qt[1], x, y);
    pin += ar*ar + ai*ai; pt += br*br + bi*bi;
    if (x*x + y*y <= aperture*aperture/4) { sr += ar*br + ai*bi; si += ai*br - ar*bi; }
  }
  const coupled = (sr*sr + si*si)/(pin*pt), ideal = launchOverlap(target, 3.3, lam, 11, delta, D);
  applyLaunch(p, delta, D);
  const {transmission} = apertureTransmission(p, aperture);
  assert.ok(coupled <= transmission, coupled+' > '+transmission);
  assert.ok(coupled > ideal*transmission && coupled > ideal, coupled+' vs '+ideal*transmission);
});

test('A fibre link saved before datasheet confirmations keeps its optics', () => {
  const p = showcase('fiber');
  p.source = {t: {w: 3.7, z: 0}, s: {w: 3.7, z: 0}}; p.lenses[0].f = 8; p.lenses[0].z = 8.1; p.length = 500;
  delete p.launch.confirmed; p.launch.travel = 0.3;
  const link = 'https://x/beam-path/?stage=fiber#layout=' + encodeURIComponent(JSON.stringify({v: 1, path: p, gap: 10}));
  const opened = openLayout(new URL(link));
  assert.equal(opened.notice, ''); assert.equal(opened.path.source.t.w, 3.7); assert.equal(opened.path.lenses[0].f, 8);
  assert.equal(opened.path.launch.travel, 0.3);
  assert.deepEqual(opened.path.launch.confirmed, {mfd: false, f: false, travel: false, aperture: false});
});

test('A bench range narrower than the 1% tolerance has no edge on either side', () => {
  const p = showcase('fiber'), target = targetMode(p).q;
  const found = solveLaunch(p, target, {...p.launch, minDistance: 735.37, maxDistance: 735.39});
  for (const side of [found.tolerance.D.minus, found.tolerance.D.plus]) assert.ok(side === null || side === Infinity);
});
