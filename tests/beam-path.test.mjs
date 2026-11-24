import test from 'node:test';
import assert from 'node:assert/strict';
import {axisOverlap, drift, lensQ, lensOrder, overlap, propagate, ringMode, sourceQ, spot, targetMode, validatePath, waistQ} from '../beam-path/optics.mjs';
import {showcase} from '../beam-path/showcases.mjs';
import {optimize} from '../beam-path/optimize.mjs';

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
