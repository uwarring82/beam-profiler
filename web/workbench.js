'use strict';
const $ = id => document.getElementById(id);
const modes = [
  ['gaussian','Gaussian','Fundamental'], ['super_gaussian','Super-Gaussian','Flat top'],
  ['hg10','HG₁₀','Two lobes'], ['hg11','HG₁₁','Four lobes'], ['lg01','LG₀¹','Annular']
];
const names = {hg10:'Hermite–Gaussian · HG₁₀', hg11:'Hermite–Gaussian · HG₁₁', lg01:'Laguerre–Gaussian'};
const number = (v, digits=1) => v == null ? '—' : v.toLocaleString(undefined, {maximumFractionDigits:digits});
let worker, source, packet, fitResult, residualTIFF, image, residualImage, view = 'intensity', pending = false;
let requestVersion = 0, regionMode = false, drag, bounds;
for (const [id, label, description] of modes) {
  const btn = document.createElement('button');
  btn.className = 'sample'; btn.dataset.mode = id; btn.setAttribute('aria-pressed','false');
  const thumb = new Image(); thumb.src = './web/samples/' + id + '-thumb.png'; thumb.alt = '';
  const text = document.createElement('span'), title = document.createElement('strong'), detail = document.createElement('small');
  title.textContent = label; detail.textContent = description; text.append(title,detail); btn.append(thumb,text);
  btn.onclick = () => sample(id); $('samples').append(btn); $('model').add(new Option(label,id));
}
function status(text='', cancellable=false) {
  $('progress').hidden = !text; $('progress-text').textContent = text; $('cancel').hidden = !cancellable;
}
function setBusy(value) {
  pending = value;
  for (const id of ['apply','select-roi','reset-roi','export']) $(id).disabled = value || !packet;
  $('fit').disabled = value || !packet?.metrics.valid || !!packet?.metrics.warnings.length;
  $('save-residual').disabled = value || !fitResult;
  $('model').disabled = value;
}
function stopWorker() {
  requestVersion++;
  worker?.terminate(); worker = null;
  setBusy(false); status();
}
$('cancel').onclick = () => { stopWorker(); status('Operation cancelled.'); };
function rawWorker() {
  if (!worker) worker = new Worker(new URL('./web/worker.js',document.baseURI));
  return worker;
}
async function run(action, target=source, settings=packet?.settings) {
  if (!target || pending) return;
  const version = ++requestVersion;
  setBusy(true); status('Preparing local analysis…',true);
  try {
    const w = rawWorker();
    const reply = await new Promise((resolve,reject) => {
      w.onerror = () => { if (worker === w) { w.terminate(); worker = null; } reject(new Error('The analysis engine could not start. Check the connection and retry.')); };
      w.onmessage = ({data}) => {
        if (data.status) {status(data.status,true); return;}
        if (data.error) reject(new Error(data.error)); else resolve(data);
      };
      w.postMessage({action, id:target.id, sample:target.sample, file:target.file, settings, model:$('model').value});
    });
    if (version !== requestVersion) return;
    if (reply.packet) await showFrame(target, reply.packet, version);
    if (version !== requestVersion) return;
    if (reply.fit) {
      const img = await decode(reply.fit.preview);
      if (version !== requestVersion) return;
      fitResult = reply.fit; residualImage = img; residualTIFF = reply.tiff; renderFit();
      setView('residual');
    }
    status();
  } catch (error) {
    if (version === requestVersion) status(error.message + (action === 'fit' ? ' Try a different model or ROI.' : ''));
  } finally { if (version === requestVersion) setBusy(false); }
}
async function sample(id) {
  stopWorker();
  const version = requestVersion;
  setBusy(true);
  try {
    const res = await fetch('./web/samples/' + id + '.json');
    if (!res.ok) throw new Error('Could not open the sample. Retry the sample selection.');
    const label = names[id] || modes.find(m=>m[0]===id)[1];
    await showFrame({id:'sample:'+id, sample:id, name:label}, await res.json(), version);
  } catch (err) { if (version === requestVersion) status(err.message); }
  finally { if (version === requestVersion) setBusy(false); }
}
function openFile(file) {
  if (!file) return;
  stopWorker();
  if (file.size > 48 * 1024 * 1024) {status('Maximum file size is 48 MiB.'); return;}
  run('measure',{id:'file:'+requestVersion, file, name:file.name},null);
}
$('upload').onchange = e => {openFile(e.target.files[0]);e.target.value='';};
document.addEventListener('dragover', e => {if(e.dataTransfer?.types.includes('Files'))e.preventDefault();});
document.addEventListener('drop', e => {if(e.dataTransfer?.files.length){e.preventDefault();openFile(e.dataTransfer.files[0]);}});
function decode(base64) {
  const img = new Image(); img.src='data:image/png;base64,'+base64; return img.decode().then(()=>img);
}
async function showFrame(target, data, version) {
  const img = await decode(data.preview);
  if (version !== requestVersion) return;
  packet=data;source=target;image=img;fitResult=null;residualImage=null;residualTIFF=null;drag=null;regionMode=false;
  $('stage').classList.remove('selecting');$('fit-output').hidden=true;
  const m=data.metrics,s=data.settings,u=m.unit;
  for(const btn of $('samples').children)btn.setAttribute('aria-pressed',String(btn.dataset.mode===target.sample));
  $('source-name').textContent=target.name;
  $('source-kind').textContent=target.sample?'Simulated frame':data.source.kind==='snapshot'?'Beam Lab snapshot':'Imported frame';
  $('geometry').textContent=data.width+' × '+data.height+' px';$('unit').textContent=u;
  $('dx').textContent=number(m.diameter_x);$('dy').textContent=number(m.diameter_y);
  $('center').textContent=number(m.centroid_x_px)+' / '+number(m.centroid_y_px)+' px';
  $('ellip').textContent=number(m.ellipticity,3);
  $('energy').textContent=number(m.encircled_energy?.d50)+' / '+number(m.encircled_energy?.d86_5)+' '+u;
  $('peak').textContent=number(m.peak_percent)+'% · '+number(m.peak_dn,0)+' DN';
  const [x0,y0,x1,y1]=m.roi;
  $('roi-caption').textContent=s.roi?`ROI (${x0}, ${y0}) · ${x1-x0} × ${y1-y0}`:'Full frame';
  $('scale').value=s.scale??'';$('maximum').value=s.maximum;$('threshold').value=s.noise_sigma;$('border').checked=s.subtract_border;
  const warnings=[...m.warnings,data.sensitivity_warning].filter(Boolean);
  $('notice').textContent=warnings.join(' ');$('notice').hidden=!warnings.length;
  setView('intensity');drawProfiles();
}
function setView(choice) {
  view=choice;
  $('intensity').setAttribute('aria-pressed',String(choice==='intensity'));
  $('residual').disabled=!fitResult;$('residual').setAttribute('aria-pressed',String(choice==='residual'));
  $('ramp').classList.toggle('signed',choice==='residual');
  $('low').textContent=choice==='residual'?'−'+number(fitResult.limit):'0';
  $('high').textContent=(choice==='residual'?'+'+number(fitResult.limit):number(packet?.settings.maximum,0))+' DN';
  draw();
}
function canvasSize(canvas) {
  const box=canvas.getBoundingClientRect(),ratio=Math.min(devicePixelRatio||1,2);
  canvas.width=Math.round(box.width*ratio);canvas.height=Math.round(box.height*ratio);
  const c=canvas.getContext('2d');c.setTransform(ratio,0,0,ratio,0,0);return [c,box.width,box.height];
}
function draw() {
  const [c,w,h]=canvasSize($('frame'));
  if (!packet||!image||!w||!h)return;
  const m=packet.metrics,signed=view==='residual'&&fitResult;
  const [x0,y0,x1,y1]=signed?m.roi:[0,0,packet.width,packet.height];
  const scale=Math.min((w-66)/(x1-x0),(h-34)/(y1-y0));
  const iw=(x1-x0)*scale,ih=(y1-y0)*scale,left=(w-iw)/2+8,top=(h-ih)/2-3;
  bounds={left,top,scale,x0,y0};
  c.drawImage(signed?residualImage:image,left,top,iw,ih);
  c.lineWidth=1;c.strokeStyle='#314052';c.strokeRect(left,top,iw,ih);
  c.font='11px ui-monospace, monospace';c.fillStyle='#9bafbf';
  for(let i=0;i<=4;i++){
    const f=i/4,x=left+iw*f,y=top+ih*f;
    c.beginPath();c.moveTo(x,top+ih);c.lineTo(x,top+ih+4);c.moveTo(left-4,y);c.lineTo(left,y);c.stroke();
    c.textAlign='center';c.fillText(String(Math.round(x0+(x1-x0-1)*f)),x,top+ih+16);
    c.textAlign='right';c.fillText(String(Math.round(y0+(y1-y0-1)*f)),left-7,y+4);
  }
  c.save();c.beginPath();c.rect(left,top,iw,ih);c.clip();
  if ($('cross').checked && m.valid) {
    const x=left+(m.centroid_x_px-x0+.5)*scale,y=top+(m.centroid_y_px-y0+.5)*scale;
    c.strokeStyle=signed?'#404d6399':'#b4cce08c';c.setLineDash([3,6]);c.beginPath();c.moveTo(x,top);c.lineTo(x,top+ih);c.moveTo(left,y);c.lineTo(left+iw,y);c.stroke();
  }
  const box=drag?[drag.start[0],drag.start[1],drag.end[0],drag.end[1]]:packet.settings.roi;
  if(box&&!signed){c.strokeStyle='#a8edce';c.setLineDash([5,3]);c.strokeRect(left+box[0]*scale,top+box[1]*scale,(box[2]-box[0])*scale,(box[3]-box[1])*scale);}
  c.restore();
}
function drawProfiles(){
  const [c,w,h]=canvasSize($('profiles'));if(!packet||!w)return;
  const left=21,top=8,bottom=h-18,right=w-9;
  c.strokeStyle='#dae2e6';c.font='10px system-ui';c.fillStyle='#526875';
  for (const f of [0,.5,1]) {const y=bottom-f*(bottom-top);c.beginPath();c.moveTo(left,y);c.lineTo(right,y);c.stroke();c.fillText(String(f),0,y+3);}
  c.fillText('ROI start',left,bottom+14);c.textAlign='right';c.fillText('ROI end',right,bottom+14);
  for(const [axis,color] of [['x','#087d72'],['y','#9053b3']]){
    const {position,dn}=packet.profiles[axis], max=Math.max(...dn,1);
    c.strokeStyle=color;c.lineWidth=1.8;c.beginPath();
    dn.forEach((v,i)=>{const x=left+(right-left)*(position[i]-position[0])/(position.at(-1)-position[0]),y=bottom-v/max*(bottom-top);if(!i)c.moveTo(x,y);else c.lineTo(x,y);});c.stroke();
  }
}
function renderFit() {
  const rows=fitResult.summary.candidates,best=rows[0],scale=packet.metrics.scale;
  $('fit-output').hidden=false;$('fit-name').textContent='Lowest RMS · '+best.label;
  const parts=[`Envelope radii ${number(best.radius_u_px*scale,2)} / ${number(best.radius_v_px*scale,2)} ${packet.metrics.unit}`,
    `center (${number(best.center_x_px)}, ${number(best.center_y_px)}) px`];
  if(best.order!=null)parts.push('order '+number(best.order,3));
  if(best.angle_resolved){
    const square=best.model==='hg11'&&Math.abs(best.radius_u_px-best.radius_v_px)<.01*best.radius_u_px;
    parts.push(number(square?(best.angle_deg+90)%90:best.angle_deg)+'°'+(square?' modulo 90°':''));
  }
  $('fit-info').textContent=parts.join(' · ');
  const notes=['Residual = measured − fitted intensity. Blue indicates a deficit and red an excess.'];
  const gaussian=rows.find(r=>r.model==='gaussian'),flat=rows.find(r=>r.model==='super_gaussian');
  if(gaussian && flat && flat.order<1.01 && Math.abs(flat.rmse_dn-gaussian.rmse_dn)<=.001*gaussian.rmse_dn)
    notes.push('Gaussian and super-Gaussian RMS agree within 0.1% at order ≈ 1.');
  if(fitResult.summary.failed_models.length)notes.push('Unresolved fits: '+fitResult.summary.failed_models.join(', ')+'.');
  $('fit-note').textContent=notes.join(' ');
  $('fit-rows').replaceChildren(...rows.map(row=>{
    const tr=document.createElement('tr');for(const v of [row.label,number(row.rmse_dn,4),number(row.r_squared,6)]){
      const td=document.createElement('td');td.textContent=v;tr.append(td);
    }return tr;
  }));
}
function point(e) {
  const r=$('frame').getBoundingClientRect(),b=bounds;
  return [Math.max(0,Math.min(packet.width,Math.round((e.clientX-r.left-b.left)/b.scale+b.x0))),
          Math.max(0,Math.min(packet.height,Math.round((e.clientY-r.top-b.top)/b.scale+b.y0)))];
}
$('select-roi').onclick=()=>{regionMode=!regionMode;drag=null;setView('intensity');$('stage').classList.toggle('selecting',regionMode);
  if(regionMode){$('stage').scrollIntoView({block:'center'});status('Drag across the intensity image to set the region.');}else status();};
$('frame').onpointerdown=e=>{if(regionMode&&!pending&&bounds){drag={start:point(e),end:point(e)};$('frame').setPointerCapture(e.pointerId);draw();}};
$('frame').onpointermove=e=>{if(drag){drag.end=point(e);draw();}};
$('frame').onpointercancel=()=>{drag=null;draw();};
$('frame').onpointerup=e=>{if(!drag)return;const [a,b]=drag.start,[x,y]=point(e);drag=null;const roi=[Math.min(a,x),Math.min(b,y),Math.max(a,x),Math.max(b,y)];
  if(roi[2]-roi[0]<8||roi[3]-roi[1]<8){status('Select at least 8 × 8 pixels.');draw();return;}
  regionMode=false;$('stage').classList.remove('selecting');run('measure',source,{...packet.settings,roi});};
$('reset-roi').onclick=()=>run('measure',source,{...packet.settings,roi:null});
$('settings').onsubmit=e=>{e.preventDefault();run('measure',source,{...packet.settings,scale:$('scale').value===''?null:Number($('scale').value),
  maximum:Number($('maximum').value),noise_sigma:Number($('threshold').value),subtract_border:$('border').checked});};
$('cross').onchange=draw;$('intensity').onclick=()=>setView('intensity');$('residual').onclick=()=>setView('residual');
$('fit').onclick=()=>run('fit');$('save-residual').onclick=()=>download(new Blob([residualTIFF],{type:'image/tiff'}),'residual.tiff');
function download(blob,suffix){
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=(source.name.replace(/[^\w.-]+/g,'-')||'beam')+'-'+suffix;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('export').onclick=()=>{const {preview,...fields}=packet;
  download(new Blob([JSON.stringify({...fields,filename:source.name,fit:fitResult?.summary??null},null,2)],{type:'application/json'}),'analysis.json');};
new ResizeObserver(()=>{draw();drawProfiles();}).observe($('stage'));
sample('lg01');
