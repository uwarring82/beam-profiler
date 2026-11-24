import {checkFocus, overlap, propagate, sourceQ, targetMode, validatePath} from './optics.mjs';

export function optimize(p, gap, catalog = []) {
  validatePath(p);
  const target = targetMode(p).q, input = sourceQ(p);
  if (!Number.isFinite(gap) || gap < 0 || gap > p.length) throw Error('Enter a clearance from 0 to the path length.');
  if (catalog.length > 16) throw Error('Use up to 16 catalog focal lengths.');
  catalog.forEach(checkFocus);
  const initial = p.lenses.map((l, index) => ({...l, index})).sort((a,b)=>a.z-b.z);
  if (!initial.some(l => !l.locked)) throw Error('Unlock or add a lens to optimize.');
  const planes = [0, ...initial.map(l=>l.z), p.length];
  if (planes.some((z,i)=>i && z-planes[i-1] < gap-1e-8))
    throw Error('The starting layout violates the requested clearance. Reduce clearance or move the lenses.');
  const clone = ls => ls.map(l=>({...l}));
  const score = ls => overlap(propagate(input, ls, p.length), target);
  let state = 137449;
  const rand = () => { state = (Math.imul(state,1664525) + 1013904223) >>> 0; return state/4294967296; };
  const free = initial.map((l,i)=>l.locked?-1:i).filter(i=>i>=0);
  const choices = initial.map(l=>[...new Set([l.f,...catalog])]);
  const low = (ls,i) => (i ? ls[i-1].z : 0) + gap;
  const high = (ls,i) => (i+1 < ls.length ? ls[i+1].z : p.length) - gap;
  const before = score(initial);
  let best = {ls: clone(initial), eta: before};

  // Sample ordered positions separately between each pair of locked anchors.
  const seeds = [];
  for (let n=0; n<1500; n++) {
    const ls = clone(initial);
    for (let i=0; i<ls.length;) {
      if (ls[i].locked) { i++; continue; }
      let end=i+1;
      while (end<ls.length && !ls[end].locked) end++;
      const start=low(ls,i), slack=Math.max(0, high(ls,end-1)-start-(end-i-1)*gap);
      const offsets = Array.from({length:end-i},rand).sort((a,b)=>a-b);
      for (let k=i; k<end; k++) {
        ls[k].z = start+(k-i)*gap+slack*offsets[k-i];
        if (catalog.length && n%3) ls[k].f=choices[k][Math.floor(rand()*choices[k].length)];
      }
      i=end;
    }
    seeds.push({ls, eta:score(ls)});
  }
  seeds.sort((a,b)=>b.eta-a.eta);
  const starts=[best], seen=new Set();
  for (const trial of seeds) {
    const key=free.map(i=>[trial.ls[i].f, Math.round(10*trial.ls[i].z/p.length)].join(':')).join('/');
    if (seen.has(key)) continue;
    seen.add(key); starts.push(trial);
    if (starts.length===13) break;
  }
  for (const start of starts) {
    const ls=clone(start.ls);
    let eta=start.eta, step=p.length/12;
    for (let pass=0; pass<240 && step>0.0005; pass++) {
      const previous=eta;
      for (const i of free) {
        const original={...ls[i]};
        let z=original.z, f=original.f;
        for (const focal of choices[i]) {
          for (const delta of [-step,0,step]) {
            ls[i].z=Math.max(low(ls,i),Math.min(high(ls,i),original.z+delta));
            ls[i].f=focal;
            const value=score(ls);
            if (value>eta+1e-14) { eta=value; z=ls[i].z; f=focal; }
          }
        }
        Object.assign(ls[i],{z,f});
      }
      // A lens pair often has a long, narrow optimum along a joint translation.
      for (let k=1; k<free.length; k++) {
        const i=free[k-1], j=free[k];
        if (j!==i+1) continue;
        const zi=ls[i].z, zj=ls[j].z;
        let shift=0;
        for (const delta of [-step,step]) {
          ls[i].z=zi+delta; ls[j].z=zj+delta;
          if (ls[i].z<low(ls,i) || ls[j].z>high(ls,j)) continue;
          const value=score(ls);
          if (value>eta+1e-14) {eta=value;shift=delta;}
        }
        ls[i].z=zi+shift;ls[j].z=zj+shift;
      }
      if (eta-previous<1e-12) step/=2;
    }
    if (eta>best.eta) best={ls,eta};
  }
  // Return mount positions at micrometre precision, if rounding preserves all constraints.
  const rounded=best.ls.map(l=>({...l,z:l.locked?l.z:Math.round(l.z*1000)/1000}));
  if (rounded.every((l,i)=> l.z>=low(rounded,i)-1e-9 && l.z<=high(rounded,i)+1e-9) && score(rounded)>=before)
    best={ls:rounded,eta:score(rounded)};
  // A free lens left at a clearance boundary means the optimum may lie beyond it.
  const limits=[];
  for (const i of free) {
    const l=best.ls[i];
    if (l.z-low(best.ls,i) <= 0.001) {
      const left = best.ls[i-1];
      limits.push(left ? {lens:l.index, side:'lens', neighbor:left.index} : {lens:l.index, side:'start'});
    }
    if (high(best.ls,i)-l.z <= 0.001) {
      const right = best.ls[i+1];
      limits.push(right ? {lens:l.index, side:'lens', neighbor:right.index} : {lens:l.index, side:'coupler'});
    }
  }
  return {before,after:best.eta,limits,lenses:best.ls.sort((a,b)=>a.index-b.index).map(({index,...l})=>l)};
}
