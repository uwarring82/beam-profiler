const $ = id => document.getElementById(id);
let state = null, packet = null, busy = false, roiMode = false, drag = null, image = null;
let frameKey = '', drawBounds = null, pollFailures = 0, logSeq = 0, logBusy = false;
const offscreen = document.createElement('canvas');
const ctx = $('beam-canvas').getContext('2d');
const fmt = (value, digits = 1) => value == null ? '—' : Number(value).toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits});
// Two significant digits in u; round the displayed value to the same place.
function uncertaintyNumber(value, u) {
  if (value == null) return '—';
  if (!(u > 0)) return fmt(value, 3);
  const firstStep = 10 ** (Math.floor(Math.log10(u)) - 1);
  const roundedU = Math.round(u / firstStep) * firstStep;
  const place = 1 - Math.floor(Math.log10(roundedU));
  if (place > 6) return Number(value).toExponential(2);
  const step = 10 ** (-place);
  return fmt(Math.round(value / step) * step, Math.max(0, place));
}

async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}

function message(text) { $('message').textContent = text || ''; $('message').hidden = !text; }

async function action(name, data = {}) {
  if (busy) return;
  busy = true;
  message(name === 'dark' && !data.clear ? 'Acquiring dark reference. Keep the beam blocked until this message disappears…' : '');
  renderState();
  try {
    state = await api(name, data);
    syncFields();
    message(state.error || '');
    const latest = await api('frame');
    if (latest) await acceptFrame(latest);
    else clearFrame();
  } catch (error) { message(error.message); }
  finally { busy = false; renderState(); }
}

function syncFields() {
  if (!state) return;
  const camera = state.camera;
  if (camera) {
    $('exposure').value = camera.exposure_us;
    $('gain').value = camera.gain_db;
    for (const [id, key] of [['exposure', 'exposure_us'], ['gain', 'gain_db']]) {
      $(id).min = camera[key + '_range'][0]; $(id).max = camera[key + '_range'][1];
    }
    $('exposure-range').textContent = `${fmt(camera.exposure_us_range[0], 0)} – ${fmt(camera.exposure_us_range[1], 0)} µs`;
  }
  $('pitch').value = state.settings.pixel_pitch_um ?? '';
  $('magnification').value = state.settings.magnification;
  $('border').checked = state.settings.subtract_border;
  $('noise').value = String(state.settings.noise_sigma);
  const uncertainty = state.uncertainty_settings || {};
  $('u-window').value = uncertainty.window_frames ?? 60;
  $('u-pitch').value = uncertainty.pixel_pitch_u_um ?? '';
  $('u-mag').value = uncertainty.magnification_u ?? '';
  $('u-correlation').value = uncertainty.scale_correlation ?? 0;
  const options = state.devices.map(d => ({value: d.id, text: d.id === 'demo' ? 'Demo · Gaussian beam' : `${d.vendor} · ${d.serial}`}));
  const previous = $('camera-select').value;
  $('camera-select').replaceChildren(...options.map(d => new Option(d.text, d.value)));
  if (options.some(d => d.value === previous)) $('camera-select').value = previous;
  if (camera) $('camera-select').value = camera.id;
  updateDeviceCard();
}

function updateDeviceCard() {
  const d = state?.camera || state?.devices.find(d => d.id === $('camera-select').value);
  $('device-name').textContent = d?.model || 'No camera selected';
  $('device-serial').textContent = d ? `${d.serial} · ${d.transport || (d.driver === 'replay' ? 'Recorded session' : d.simulated ? 'Synthetic' : 'USB3 Vision')}` : 'Rescan to discover cameras';
}

function renderState() {
  const connected = !!state?.connected, paused = !!state?.paused;
  $('connect').textContent = connected ? 'Disconnect camera' : 'Connect camera ↗';
  $('connect').disabled = busy || !state?.devices.length;
  $('camera-select').disabled = connected || busy;
  $('scan').disabled = connected || busy;
  const replay = state?.camera?.driver === 'replay';
  for (const id of ['pause', 'apply-camera', 'exposure', 'gain', 'dark', 'roi-tool', 'full-roi']) $(id).disabled = !connected || busy;
  // A replay keeps its recorded exposure, gain and dark references.
  for (const id of ['apply-camera', 'exposure', 'gain', 'dark']) if (replay) $(id).disabled = true;
  const recording = state?.recording;
  $('record').disabled = !connected || busy || replay || !state?.session;
  $('record').classList.toggle('recording', !!recording);
  $('record').textContent = recording ? '■ Stop recording' : '● Record raw frames';
  $('record-note').textContent = replay ? 'Replaying recorded frames with their original timestamps.' : !state?.session ? 'No session folder: recording unavailable.' : recording ? `Recording ${recording.name}: ${recording.frames} frames (limit ${recording.max_frames}).` : 'Saves each analyzed live frame as native TIFF for later replay.';
  $('rec-state').hidden = !recording; if (recording) $('rec-state').textContent = `● REC ${recording.frames}`;
  $('clear-dark').disabled = !state?.dark_active || busy;
  $('reset-statistics').disabled = !connected || busy;
  $('uncertainty-form').querySelector('button').disabled = busy;
  $('export').disabled = $('save-png').disabled = !packet || busy;
  $('pause').textContent = paused ? '▶ Resume' : 'Ⅱ Freeze';
  $('dark-state').textContent = state?.dark_active ? 'ACTIVE · 8 FRAMES' : 'NONE';
  $('format').textContent = state?.camera?.pixel_format || '—';
  $('live-state').textContent = !connected ? 'OFFLINE' : paused ? 'FROZEN' : replay ? 'REPLAY' : state.camera.simulated ? 'DEMO' : 'LIVE';
  $('live-dot').classList.toggle('live', connected && !paused);
  $('frozen-badge').hidden = !paused || !packet;
  $('demo-badge').hidden = !packet?.simulated && !packet?.replay;
  $('demo-badge').textContent = packet?.replay ? (packet.simulated ? 'REPLAY · SIMULATED DATA' : 'REPLAY · RECORDED DATA') : 'SIMULATED DATA';
  $('connection-label').textContent = !connected ? '● Disconnected' : replay ? '● Replay' : state.camera.simulated ? '● Simulator' : '● Camera connected';
  $('fps').textContent = `${paused ? 'Frozen' : fmt(state?.fps || 0) + ' fps'}`;
  $('frames').textContent = `${(state?.frame_count || 0).toLocaleString()} frames`;
  $('camera-description').textContent = !connected ? 'Connect your camera to start measuring.' : replay ? `Replay of ${state.camera.replay.session}/${state.camera.replay.recording} · ${state.camera.model} · S/N ${state.camera.serial}` : `${state.camera.model} · S/N ${state.camera.serial}`;
  $('calibration-note').textContent = state?.settings.pixel_pitch_um ?
    `Scale: ${fmt(state.settings.pixel_pitch_um / state.settings.magnification, 3)} µm / px ${state.settings.magnification === 1 ? 'at the sensor.' : 'in the object plane.'}` : 'Leave pitch blank to measure in pixels.';
}

function clearFrame() {
  packet = image = null; frameKey = ''; $('empty-state').hidden = false;
  $('resolution').textContent = 'No frame'; $('timestamp').textContent = 'No acquisition yet';
  $('diameter-x').textContent = $('diameter-y').textContent = $('centroid').textContent = $('ellipticity').textContent = '—';
  $('angle').textContent = 'Principal-axis orientation —';
  for (const id of ['peak', 'saturation', 'background']) $(id).textContent = '—';
  $('peak-bar').style.width = '0%'; $('quality-title').textContent = 'Waiting for a frame';
  $('quality-detail').textContent = 'Measurements use native camera pixels. The preview is scaled for display.';
  document.querySelector('.quality').classList.remove('warn');
  renderUncertainty(null);
  draw(); drawProfile('x', null); drawProfile('y', null);
}

async function acceptFrame(next) {
  const key = `${next.id}:${next.timestamp}:${JSON.stringify(next.metrics)}:${JSON.stringify(next.uncertainty)}`;
  if (key === frameKey) return;
  const img = new Image(); img.src = 'data:image/png;base64,' + next.image;
  await img.decode();
  packet = next; image = img; frameKey = key;
  $('empty-state').hidden = true;
  const m = packet.metrics;
  $('diameter-x').textContent = fmt(m.diameter_x); $('diameter-y').textContent = fmt(m.diameter_y);
  $('unit-x').textContent = $('unit-y').textContent = m.unit;
  $('centroid').replaceChildren(document.createTextNode(fmt(m.centroid_x_px)), Object.assign(document.createElement('em'), {textContent: '/'}), document.createTextNode(fmt(m.centroid_y_px)));
  $('ellipticity').textContent = fmt(m.ellipticity, 3);
  $('angle').textContent = `Principal-axis orientation ${fmt(m.angle_deg)}°`;
  $('resolution').textContent = `${packet.width} × ${packet.height} px`;
  $('peak').textContent = `${fmt(m.peak_percent)}%`;
  $('peak-bar').style.width = Math.min(100, m.peak_percent) + '%';
  $('saturation').textContent = `${fmt(m.saturated_percent, 3)}%`;
  $('background').textContent = `${fmt(m.background_dn)} DN`;
  const [x0, y0, x1, y1] = m.roi;
  $('roi-label').textContent = state?.settings.roi ? `ROI: ${x1-x0} × ${y1-y0} px · (${x0}, ${y0})` : 'Analysis region: full sensor';
  $('x-span').textContent = `${x0} – ${x1 - 1} px`; $('y-span').textContent = `${y0} – ${y1 - 1} px`;
  $('scale-max').textContent = `${m.maximum_dn.toLocaleString()} DN`;
  $('timestamp').textContent = new Date(packet.timestamp).toLocaleTimeString() + ' · ' + (packet.replay ? `Recorded frame ${packet.replay.frame} of ${packet.replay.frames}` : packet.simulated ? 'Simulated frame' : 'Camera frame');
  const warnings = m.warnings;
  document.querySelector('.quality').classList.toggle('warn', warnings.length > 0);
  $('quality-title').textContent = !m.valid ? 'No clear beam detected' : warnings.length ? 'Check measurement conditions' : 'Intensity signal detected';
  $('quality-detail').textContent = warnings.length ? warnings.join(' ') :
    'D4σ from background-corrected intensity moments. Threshold and ROI affect the measured beam width.';
  renderUncertainty(packet.uncertainty);
  draw(); drawProfile('x', packet.profile_x); drawProfile('y', packet.profile_y); renderState();
}

const uncertaintyFields = [
  ['diameter_x','X diameter'],['diameter_y','Y diameter'],['centroid_x_px','Centroid X'],
  ['centroid_y_px','Centroid Y'],['major','Major diameter'],['minor','Minor diameter'],
  ['ellipticity','Ellipticity'],['angle_deg','Axis angle'],['peak_percent','Peak intensity'],['background_dn','Background']
];
function renderUncertainty(u) {
  const fields = u?.fields || {};
  const pending = u?.status === 'blocked' ? 'Uncertainty unavailable' : `Collecting ${u?.sample_count || 0} / ${u?.minimum_samples || 20} frames`;
  function errorLabel(key) {
    const f=fields[key];
    if(!f) return pending;
    if(f.known_standard_u == null) return 'Uncertainty unresolved';
    return `± ${uncertaintyNumber(f.known_standard_u,f.known_standard_u)} ${f.unit === '1' ? '' : f.unit} · partial u`;
  }
  for(const [id,key] of [['diameter-x','diameter_x'],['diameter-y','diameter_y'],['ellipticity','ellipticity']]) {
    $('u-'+id).textContent=errorLabel(key);
    const f=fields[key];
    if(f?.known_standard_u != null) $(id).textContent=uncertaintyNumber(f.value,f.known_standard_u);
  }
  const cx=fields.centroid_x_px,cy=fields.centroid_y_px;
  $('u-centroid').textContent=cx?.known_standard_u != null && cy?.known_standard_u != null ?
    `± ${uncertaintyNumber(cx.known_standard_u,cx.known_standard_u)} / ± ${uncertaintyNumber(cy.known_standard_u,cy.known_standard_u)} px · partial u` : pending;
  if(cx?.known_standard_u != null && cy?.known_standard_u != null) {
    $('centroid').replaceChildren(document.createTextNode(uncertaintyNumber(cx.value,cx.known_standard_u)),Object.assign(document.createElement('em'),{textContent:'/'}),document.createTextNode(uncertaintyNumber(cy.value,cy.known_standard_u)));
  }
  if(fields.angle_deg) {
    const f=fields.angle_deg;
    $('angle').textContent=f.known_standard_u == null ? 'Axis angle unresolved' : `Angle ${uncertaintyNumber(f.value,f.known_standard_u)}° ± ${uncertaintyNumber(f.known_standard_u,f.known_standard_u)}°`;
  }
  for(const [id,key] of [['peak','peak_percent'],['background','background_dn']]) {
    const f=fields[key];
    $(id).title=f ? `${errorLabel(key)}; temporal spread ${uncertaintyNumber(f.repeatability_sd,f.repeatability_sd)}` : pending;
    if(f?.known_standard_u != null) $(id).textContent=`${uncertaintyNumber(f.value,f.known_standard_u)} ± ${uncertaintyNumber(f.known_standard_u,f.known_standard_u)} ${key==='peak_percent'?'%':'DN'}`;
  }
  $('uncertainty-count').textContent=`${u?.sample_count || 0} / ${u?.window_frames || state?.uncertainty_settings?.window_frames || 60} FRAMES`;
  $('uncertainty-status').textContent=!u ? 'Waiting for frames' : u.status==='blocked' ? 'Estimate withheld · check image quality' : u.status==='warming_up' ? 'Collecting repeatability data' : u.status==='changing' ? 'Signal changing · spread includes motion/drift' : 'Repeatability estimated · partial uncertainty budget';
  const text=!u || u.status==='warming_up' ? 'At least 20 consecutive valid frames are needed. Camera, ROI, analysis and calibration changes reset the window.' :
    u.status==='blocked' ? 'Saturated, truncated or unclear beam images cannot provide reliable uncertainty estimates.' :
    `${u.sample_count} consecutive frames over ${fmt(u.duration_s,1)} s. ${u.warnings.join(' ')}`;
  $('uncertainty-detail').textContent=text;
  $('uncertainty-budget').textContent=u?.calibration.complete ?
    'Partial budget · scale inputs specified or not applicable; residual systematic effects remain unquantified.' :
    'Partial budget · pixel-pitch and/or magnification uncertainty not specified; residual systematic effects remain unquantified.';
  const rows=uncertaintyFields.map(([key,label])=>{
    const f=fields[key],row=document.createElement('tr');
    const number=(value)=>value == null ? '—' : uncertaintyNumber(value, f?.known_standard_u || f?.repeatability_sd);
    const scale=f ? (['diameter_x','diameter_y','major','minor'].includes(key) ?
      (u.calibration.missing?.length===2?'Not specified':`${number(f.calibration_u)}${f.calibration_complete?'':' + unknown'}`) : 'n/a') : '—';
    const cells=[label+(f && f.unit!=='1'?` (${f.unit})`:''),number(f?.mean),f && f.repeatability_sd===0?'No resolved variation':number(f?.repeatability_sd),scale,number(f?.known_standard_u)];
    cells.forEach(text=>{const cell=document.createElement('td');cell.textContent=text;row.append(cell);});
    return row;
  });
  $('uncertainty-rows').replaceChildren(...rows);
}

const anchors = [[0,7,8,23],[.18,40,24,90],[.38,111,39,110],[.6,193,62,101],[.8,246,144,82],[1,255,245,185]];
const lut = Array.from({length:256}, (_, i) => {
  const x = i / 255; let j = 1; while (j < anchors.length - 1 && x > anchors[j][0]) j++;
  const a = anchors[j-1], b = anchors[j], t = (x-a[0])/(b[0]-a[0]);
  return [1,2,3].map(k => Math.round(a[k] + t*(b[k]-a[k])));
});

function resizeCanvas(canvas) {
  const r = canvas.getBoundingClientRect(), ratio = devicePixelRatio || 1;
  canvas.width = Math.round(r.width * ratio); canvas.height = Math.round(r.height * ratio);
  const c = canvas.getContext('2d'); c.setTransform(ratio, 0, 0, ratio, 0, 0);
  return [c, r.width, r.height];
}

function draw() {
  const [c, cw, ch] = resizeCanvas($('beam-canvas')); c.clearRect(0,0,cw,ch);
  if (!packet || !image) return;
  const scale = Math.min(cw / packet.width, ch / packet.height);
  const w = packet.width * scale, h = packet.height * scale, ox = (cw-w)/2, oy = (ch-h)/2;
  drawBounds = {scale, ox, oy, w, h};
  offscreen.width = image.width; offscreen.height = image.height;
  const o = offscreen.getContext('2d', {willReadFrequently:true}); o.drawImage(image,0,0);
  if ($('palette').value === 'thermal') {
    const data = o.getImageData(0,0,image.width,image.height);
    for (let i=0; i<data.data.length; i+=4) { const color=lut[data.data[i]]; data.data[i]=color[0]; data.data[i+1]=color[1]; data.data[i+2]=color[2]; }
    o.putImageData(data,0,0);
  }
  c.drawImage(offscreen,ox,oy,w,h);
  const m=packet.metrics;
  if (state?.settings.roi || drag) {
    const r = drag ? [Math.min(drag.start[0],drag.end[0]),Math.min(drag.start[1],drag.end[1]),Math.max(drag.start[0],drag.end[0]),Math.max(drag.start[1],drag.end[1])] : m.roi;
    c.strokeStyle='#b9ecd0'; c.lineWidth=1; c.setLineDash([5,4]);
    c.strokeRect(ox+r[0]*scale,oy+r[1]*scale,(r[2]-r[0])*scale,(r[3]-r[1])*scale); c.setLineDash([]);
  }
  if ($('overlay').checked && m.valid) {
    const x=ox+m.centroid_x_px*scale,y=oy+m.centroid_y_px*scale;
    c.save(); c.beginPath(); c.rect(ox,oy,w,h); c.clip();
    c.lineWidth=.8;c.strokeStyle='#d1e5e8aa';c.setLineDash([4,5]);
    c.beginPath();c.moveTo(ox,y);c.lineTo(ox+w,y);c.moveTo(x,oy);c.lineTo(x,oy+h);c.stroke();c.setLineDash([]);
    c.strokeStyle='#e4f7dcdd';c.beginPath();c.ellipse(x,y,m.major/m.scale/2*scale,m.minor/m.scale/2*scale,m.angle_deg*Math.PI/180,0,2*Math.PI);c.stroke();
    c.beginPath();c.arc(x,y,4,0,2*Math.PI);c.stroke();c.restore();
  }
  // Corner marks locate the sensor boundary within the fitted viewport.
  c.strokeStyle='#627b8e';c.lineWidth=1;
  for (const [x,y,sx,sy] of [[ox,oy,1,1],[ox+w,oy,-1,1],[ox,oy+h,1,-1],[ox+w,oy+h,-1,-1]]) {
    c.beginPath();c.moveTo(x+sx*12,y);c.lineTo(x,y);c.lineTo(x,y+sy*12);c.stroke();
  }
}

function drawProfile(axis, profile) {
  const [c,w,h] = resizeCanvas($('profile-'+axis)), left=25, right=w-7, top=8, bottom=h-20;
  c.clearRect(0,0,w,h); c.font='9px -apple-system, sans-serif';c.fillStyle='#597085';
  c.lineWidth=.6;c.strokeStyle='#2b3948';
  for(let i=0;i<=2;i++){const y=bottom-(bottom-top)*i/2;c.beginPath();c.moveTo(left,y);c.lineTo(right,y);c.stroke();c.fillText(i===0?'0':i===1?'.5':'1',2,y+3);}
  if(!profile) return;
  const values=profile.intensity, max=Math.max(...values,1), color=axis==='x'?'#68c6eb':'#bba0ff';
  c.beginPath();values.forEach((v,i)=>{const x=left+i/(values.length-1)*(right-left),y=bottom-v/max*(bottom-top);if(i===0)c.moveTo(x,y);else c.lineTo(x,y);});
  c.strokeStyle=color;c.lineWidth=1.6;c.stroke();c.lineTo(right,bottom);c.lineTo(left,bottom);c.closePath();
  const gradient=c.createLinearGradient(0,top,0,bottom);gradient.addColorStop(0,color+'30');gradient.addColorStop(1,color+'03');c.fillStyle=gradient;c.fill();
  c.fillStyle='#688095';c.fillText(profile.position[0],left,bottom+16);c.textAlign='right';c.fillText(profile.position.at(-1),right,bottom+16);c.textAlign='left';
}

function sensorPoint(event) {
  if(!drawBounds||!packet)return null;
  const r=$('beam-canvas').getBoundingClientRect(), {ox,oy,scale}=drawBounds;
  return [Math.round(Math.max(0,Math.min(packet.width,(event.clientX-r.left-ox)/scale))),Math.round(Math.max(0,Math.min(packet.height,(event.clientY-r.top-oy)/scale)))];
}
$('beam-canvas').addEventListener('pointerdown',e=>{if(!roiMode||!packet||busy)return;const p=sensorPoint(e);drag={start:p,end:p};$('beam-canvas').setPointerCapture(e.pointerId);draw();});
$('beam-canvas').addEventListener('pointermove',e=>{if(drag){drag.end=sensorPoint(e);draw();}});
$('beam-canvas').addEventListener('pointerup',async e=>{if(!drag)return;drag.end=sensorPoint(e);const d=drag;drag=null;const roi=[Math.min(d.start[0],d.end[0]),Math.min(d.start[1],d.end[1]),Math.max(d.start[0],d.end[0]),Math.max(d.start[1],d.end[1])];roiMode=false;setRoiMode();await action('analysis',{roi});draw();});
$('beam-canvas').addEventListener('pointercancel',()=>{drag=null;draw();});
function setRoiMode(){$('roi-tool').classList.toggle('active',roiMode);$('stage').classList.toggle('selecting',roiMode);$('roi-tool').textContent=roiMode?'⌖ Drag a region':'⌖ Select ROI';}
$('roi-tool').onclick=()=>{roiMode=!roiMode;setRoiMode();};
$('full-roi').onclick=()=>action('analysis',{roi:null});
$('camera-select').onchange=updateDeviceCard;
$('scan').onclick=()=>action('scan');
$('connect').onclick=()=>action(state?.connected?'disconnect':'connect',{id:$('camera-select').value});
$('pause').onclick=()=>action('pause',{paused:!state.paused});
$('camera-form').onsubmit=e=>{e.preventDefault();action('configure',{exposure_us:Number($('exposure').value),gain_db:Number($('gain').value)});};
$('analysis-form').onsubmit=e=>{e.preventDefault();action('analysis',{pixel_pitch_um:$('pitch').value?Number($('pitch').value):null,magnification:Number($('magnification').value),subtract_border:$('border').checked,noise_sigma:Number($('noise').value)});};
$('uncertainty-form').onsubmit=e=>{e.preventDefault();action('uncertainty',{window_frames:Number($('u-window').value),pixel_pitch_u_um:$('u-pitch').value===''?null:Number($('u-pitch').value),magnification_u:$('u-mag').value===''?null:Number($('u-mag').value),scale_correlation:Number($('u-correlation').value)});};
$('reset-statistics').onclick=()=>action('reset_statistics');
$('record').onclick=()=>action('record',{recording:!state?.recording});
$('dark').onclick=()=>action('dark');$('clear-dark').onclick=()=>action('dark',{clear:true});
$('palette').onchange=draw;$('overlay').onchange=draw;
// Both downloads use the server's current snapshot: freeze first to keep a particular frame.
async function download(path,name,extension){try{const response=await fetch(`/api/${path}?palette=${$('palette').value}`);if(!response.ok)throw new Error((await response.json()).error);const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=`${name}-${new Date().toISOString().replaceAll(':','-')}.${extension}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){message(error.message);}}
$('export').onclick=()=>download('export','beam-snapshot','zip');
$('save-png').onclick=()=>download('png','beam-inspection','png');

function dialog(title,html){$('dialog-title').textContent=title;$('dialog-content').innerHTML=html;$('info-dialog').showModal();}
const measurementNotes=`<p><strong>D4σ diameter</strong> is four times the intensity-weighted standard deviation. For an ideal Gaussian it equals the 1/e² diameter. The ellipse shows principal-axis D4σ diameters; X and Y cards show sensor-axis widths.</p><p><strong>Background and noise.</strong> The median of the ROI border is subtracted when enabled. Signal below the selected multiple of border noise is excluded; noise uses a robust deviation estimate that also handles black-clipped pixels. A dark reference averages 8 blocked-beam frames and is cleared when camera settings change. Thresholds can exclude weak beam tails and change second moments.</p><p><strong>Calibration.</strong> Physical scale = pixel pitch ÷ optical magnification. At 1× the result is in the sensor plane. Unknown cameras use pixels until a pitch is entered. Centroids use the delivered image’s pixel coordinates.</p><p><strong>Profiles</strong> sum corrected intensity along each axis and are independently normalized for display. The preview uses a fixed full-scale intensity mapping.</p><p><strong>Session log and replay.</strong> Every server run writes a timestamped event log (connections, settings, dark references, recordings, exports, errors and image-quality changes) to <code>sessions/session-…/log.jsonl</code>. <strong>Record raw frames</strong> saves each analyzed live frame as native TIFF with a SHA-256 manifest. Recordings appear in the camera list after recording or rescanning; replay loops them with their original timestamps, exposure, gain and dark references through the same analysis.</p><p><strong>Export raw data</strong> saves a ZIP with the native-bit-depth TIFF, measurement metadata, full-resolution X/Y profile CSVs, a plain-text details file and the inspection PNG. <strong>Save PNG</strong> saves only the inspection sheet: image with overlays, profiles and all settings and results as plain text, also embedded as PNG text metadata. Measure from the raw TIFF, not the PNG. Freeze before saving to retain a particular frame. These are practical beam estimates, not certified ISO 11146 results.</p>`;
$('help').onclick=$('method-help').onclick=()=>dialog('About the measurements',measurementNotes+`<p><strong>Uncertainty.</strong> Live values are single-frame measurements. The rolling sample standard deviation s (N−1 denominator) estimates their temporal spread, including beam motion. It is not divided by √N. Known calibration contributions are combined in quadrature, including a common scale covariance with input correlation ρ. Unknown contributions are visibly omitted from a partial budget. Near-circular axis angles and unresolved variation receive no error bar. Export includes the exact window samples and covariance matrices.</p><p>Standard uncertainties use k = 1; no coverage probability or confidence interval is implied. The <a href="https://physics.nist.gov/cuu/Uncertainty/combination.html" target="_blank" rel="noopener">NIST uncertainty guidance</a> describes covariance propagation and its assumptions.</p>`);
$('models').onclick=()=>dialog('Camera support',`<p>Camera acquisition is separate from beam analysis, so additional drivers and model calibrations can be added independently.</p><div class="model-row"><strong>FLIR Firefly FFY-U3-16S2M-DL</strong><span>Verified on this computer · USB3 Vision</span><p>1440 × 1080 monochrome · 3.45 µm pixel pitch · exposure and gain control · Mono16 acquisition.</p></div><div class="model-row"><strong>Other GenICam cameras</strong><span>Aravis adapter · device-dependent support</span><p>Discovery supports USB3 Vision and GigE Vision. This version requires Mono8, Mono10, Mono12 or Mono16. Other models need hardware validation and pixel-pitch calibration.</p></div><div class="model-row"><strong>Gaussian beam simulator</strong><span>Included · no hardware needed</span><p>A moving elliptical beam for exploring the UI and testing the analysis.</p></div>`);
$('close-dialog').onclick=()=>$('info-dialog').close();
$('info-dialog').addEventListener('click',e=>{if(e.target===$('info-dialog'))$('info-dialog').close();});
new ResizeObserver(()=>{draw();drawProfile('x',packet?.profile_x);drawProfile('y',packet?.profile_y);}).observe($('stage'));

async function updateLog(){
  if(logBusy)return; logBusy=true;
  try{
    const result=await api('log?after='+logSeq);
    $('log-folder').textContent=result.folder ? result.folder+'/log.jsonl' : 'In memory · no session folder';
    const rows=result.entries.map(e=>{const li=document.createElement('li');li.className='log-'+e.event;
      const time=document.createElement('time');time.dateTime=e.time;time.textContent=new Date(e.time).toLocaleTimeString();
      li.append(time,document.createTextNode(e.message));return li;});
    $('log-list').prepend(...rows.reverse());
    while($('log-list').children.length>200)$('log-list').lastChild.remove();
    logSeq=result.seq;
  }catch{}finally{logBusy=false;}
}

async function poll(){
  if(!busy){
    try{
      const [nextState,nextFrame]=await Promise.all([api('status'),api('frame')]);
      state=nextState;
      if(nextFrame)await acceptFrame(nextFrame);else if(packet)clearFrame();
      renderState();
      if(pollFailures){message('');pollFailures=0;}
      if(state.error)message(state.error);
      if(state.log_seq!==logSeq)updateLog();
    }catch(error){pollFailures++;if(pollFailures>2){message('Connection to the local server was lost. Restart python3 -m beam_profiler.server.');$('live-state').textContent='SERVER OFFLINE';$('live-dot').classList.remove('live');}}
  }
  setTimeout(poll,120);
}
(async()=>{try{state=await api('status');syncFields();renderState();if(state.driver_error)message(state.driver_error);}catch(error){message(error.message);}poll();})();
