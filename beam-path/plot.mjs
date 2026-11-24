import {lensOrder, propagate, sourceQ, spot} from './optics.mjs';

const left = 49, right = 22;
export function positionAt(canvas, event, length) {
  const box = canvas.getBoundingClientRect();
  const u = (event.clientX-box.left-left)/(box.width-left-right);
  return Math.max(0, Math.min(1, u))*length;
}

export function draw(canvas, path, target, probe) {
  const {width, height} = canvas.getBoundingClientRect();
  const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(width*ratio);
  canvas.height = Math.round(height*ratio);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (!path || width<=left+right) return;
  const input = sourceQ(path), ordered = lensOrder(path.lenses);
  const x = z => left + z/path.length*(width-left-right);
  const mid = (height-7)/2, heightScale = mid-36;
  const zPoints = new Set([0, path.length]);
  for (let i=1; i<300; i++) zPoints.add(i*path.length/300);
  // Sample each exact waist too, even if its Rayleigh range is sub-pixel.
  const ends = [...new Set([0, ...ordered.map(l=>l.z), path.length])];
  for (let i=0; i+1<ends.length; i++) {
    const start=ends[i], end=ends[i+1];
    zPoints.add(start);
    for (const q of propagate(input, ordered, start)) {
      for (const dz of [0, q.im, -q.im]) {
        const z = start-q.re+dz;
        if (z>start && z<end) zPoints.add(z);
      }
    }
  }
  const samples = [...zPoints].sort((a,b)=>a-b).map(z=>({
    x:x(z), w:propagate(input,ordered,z).map(q=>spot(q,path.wavelength).w)
  }));
  const goal = target?.q.map(q=>spot(q,path.wavelength).w) || [];
  const ymax = Math.max(...samples.flatMap(s=>s.w), ...goal)*1.04;
  const y = w => mid - w/ymax*heightScale;
  const line = (x1,y1,x2,y2) => {
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
  };
  ctx.font='11px system-ui'; ctx.fillStyle='#aabfcd'; ctx.textAlign='left';
  ctx.fillText('±w (µm)',8,15);
  for (const factor of [-1,-.5,0,.5,1]) {
    const yy=y(ymax*factor);
    ctx.strokeStyle=factor===0?'#4c6472':'#253447';
    line(left,yy,width-right,yy);
    ctx.textAlign='right'; ctx.fillText(String(Math.round(ymax*factor)),left-6,yy+4);
  }
  for (let i=0;i<=4;i++) {
    const z=path.length*i/4, xx=x(z);
    ctx.strokeStyle='#233347'; line(xx,31,xx,height-32);
    ctx.textAlign='center'; ctx.fillText(Number(z.toFixed(2)).toString(),xx,height-17);
  }
  ctx.fillText('Rail position (mm)',(width+left-right)/2,height-2);
  const colors=['#83dfb3','#c7a2ed'];
  samples[0].w.forEach((_,a)=>{
    ctx.beginPath();
    for (const [i,s] of samples.entries()) {
      if (i===0) ctx.moveTo(s.x,y(s.w[a])); else ctx.lineTo(s.x,y(s.w[a]));
    }
    for (const s of [...samples].reverse()) ctx.lineTo(s.x,y(-s.w[a]));
    ctx.closePath(); ctx.fillStyle=a?'#b491dc0d':'#84d8ac18'; ctx.fill();
    ctx.lineWidth=a?1.6:2; ctx.strokeStyle=colors[a]; ctx.setLineDash(a?[5,4]:[]);
    for (const sign of [-1,1]) {
      ctx.beginPath(); samples.forEach((s,i)=> i ? ctx.lineTo(s.x,y(sign*s.w[a])) : ctx.moveTo(s.x,y(sign*s.w[a])));
      ctx.stroke();
    }
  });
  ctx.lineWidth=1; ctx.setLineDash([2,4]); ctx.strokeStyle='#cfdddf';
  line(x(probe),29,x(probe),height-32); ctx.setLineDash([]);
  for (const [i,lens] of path.lenses.entries()) {
    const xx=x(lens.z); ctx.strokeStyle='#7691a3'; line(xx,28,xx,height-34);
    ctx.fillStyle=lens.locked?'#405262':'#295347'; ctx.fillRect(xx-12,6,24,21);
    ctx.fillStyle='#eaf6ee';ctx.textAlign='center';ctx.fillText('L'+(i+1),xx,21);
  }
  goal.forEach((w,a)=>{
    ctx.fillStyle=colors[a]; for(const sign of [-1,1]){
      ctx.beginPath();ctx.arc(x(path.length),y(sign*w),a?2:3.6,0,Math.PI*2);ctx.fill();
    }
  });
}
