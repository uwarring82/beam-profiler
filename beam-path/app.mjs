import {axes, lensOrder, maxLenses, overlap, propagate, sourceQ, spot, targetMode, validatePath} from './optics.mjs';
import {showcases} from './showcases.mjs';
import {fitProjection, openLayout, shareLayout} from './layout.mjs';
import {renderSearchResult} from './search-result.mjs';
import {draw, lensMarkerAt, positionAt} from './plot.mjs';
import {applyLaunch, apertureTransmission, launchGeometry, solveLaunch} from './fiber.mjs';

const $ = id => document.getElementById(id);
const fmt = (number, digits=2) => Number(number.toFixed(digits)).toLocaleString('en-US', {maximumFractionDigits:digits});
const percent = n => (100*n).toFixed(2)+'%';
const {stage, path, gap, fit, notice} = openLayout(new URL(location.href));
const ref = showcases[stage], cavityRef = showcases[ref.cavity ?? stage];
const projected = fit && fitProjection(fit);
const approximate = projected?.fitOverlap < 0.99;
$('gap').value = gap;
let beamValid=false, target, worker, proposal, dragging=null;
const fieldInputs = new Map();
// Inputs that show a path value; refreshed when another control changes it.
const bound = [];
function refreshFields() {
  for (let i = bound.length - 1; i >= 0; i--) {
    const {field, get, digits = 6} = bound[i];
    if (!field.isConnected) { bound.splice(i, 1); continue; }
    if (document.activeElement === field) continue;
    const value = get();
    field.value = Number.isFinite(value) ? Number(value.toFixed(digits)) : '';
  }
}

function labelFor(parent, text, input) {
  const label = document.createElement('label');
  label.append(document.createTextNode(text),input); parent.append(label); return label;
}
function numeric(parent, label, object, key) {
  const field=document.createElement('input');
  field.type='number'; field.step='any'; field.value=object[key];
  field.oninput=()=>{object[key]=field.valueAsNumber; edited();};
  labelFor(parent,label,field);
  bound.push({field, get: () => object[key]});
  return field;
}
function derived(parent, label, get, set, digits=6) {
  const field=document.createElement('input');
  field.type='number'; field.step='any';
  field.oninput=()=>{ if (Number.isFinite(field.valueAsNumber)) { set(field.valueAsNumber); renderLenses(); edited(); } };
  labelFor(parent,label,field);
  bound.push({field, get, digits});
  return field;
}
function selectField(parent, label, choices, current, change) {
  const field=document.createElement('select');
  for (const [value, name] of choices) field.add(new Option(name,value));
  field.value=current; field.onchange=()=>change(field.value);
  labelFor(parent,label,field);
}
function addWaistFields(parent, pair) {
  for (const [axis,name] of [['t','Tangential'],['s','Sagittal']]) {
    numeric(parent,name+' waist radius (µm)',pair[axis],'w');
    numeric(parent,name+' waist position (mm)',pair[axis],'z');
  }
}
function showImportNote() {
  const note = $('import-note');
  if (!fit) {
    note.hidden = !notice;
    note.textContent = notice;
    return;
  }
  const parts = [];
  if (projected.fitOverlap === undefined) {
    parts.push('Legacy Gaussian fit imported with T/S radii. The original orientation is unknown.');
  } else {
    parts.push('The imported Gaussian fit uses camera x/y marginal radii for T/S. Align camera x with the cavity plane.');
    parts.push('At the assumed waist, the fitted field and its separable approximation have '+percent(projected.fitOverlap)+' power overlap.');
    if (approximate) parts.push('The model discards x–y correlation. Coupler predictions use the separable input.');
  }
  const source = path.source;
  if (Math.abs(source.t.w-projected.x) > 1e-7*projected.x || Math.abs(source.s.w-projected.y) > 1e-7*projected.y || source.t.z !== 0 || source.s.z !== 0) {
    parts.push('The current waist settings differ from the original import.');
  }
  parts.push('A single image gives no wavefront curvature. Confirm the waist locations and pump wavelength.');
  note.textContent = parts.join(' ');
  note.hidden = false;
}
document.querySelector('[data-stage="'+stage+'"]').setAttribute('aria-current','true');
$('reference').textContent='From Friedenauer et al. (2006), under review. '+(ref.cavity?'Cavity: ':'')+(ref.cavity??stage).toUpperCase()+' stage · '+cavityRef.pump+' nm pump, '+cavityRef.short+' mm between focusing mirrors, '+cavityRef.total+' mm total cavity length and '+cavityRef.fold+'° full fold. The paper gives a '+cavityRef.designWaist+' µm design waist. Table 1 reports '+cavityRef.inputPower.toFixed(2)+' W incident, '+cavityRef.outputPower+' W output and '+cavityRef.efficiency+'% doubling efficiency.';
if (ref.cavity) $('assumptions').textContent='Fibre launch assumptions · The fibre mode is a Gaussian waist at the fibre tip with a radius of half the mode field diameter. Use the diameter at the pump wavelength: the fibre\'s quoted numerical aperture is not the beam divergence. The output coupler is a thin lens, so the focus offset counts from its effective focal length; a real collimator\'s mechanical scale has a different zero, but changes in offset carry over. The mode field diameter, focal length, focus travel and clear aperture shown first are illustrative: enter your fibre and collimator. The cavity is the LBO showcase, with its assumptions.';
else $('assumptions').textContent='Showcase assumptions · The '+ref.rail+' mm external injection rail, input waist, injection lens focal lengths and placements, and distance from the coupler to the first curved mirror are illustrative. We interpret the tabulated cavity length as a geometric round trip and the crystal length along the central ray. The scalar pump index is set to '+ref.index.toFixed(2)+'. '+(stage==='bbo'?'The Brewster plane lies in the cavity plane. The model carries the 25 mm mirror focal length over from the first stage.':'The 25 mm focusing mirrors have a 50 mm radius of curvature.');
numeric($('source'),'Pump wavelength (nm)',path,'wavelength');
numeric($('source'),'Rail length (mm)',path,'length');
addWaistFields($('source'),path.source);
for(const [key,label] of [['roc','Mirror radius of curvature (mm)'],['angle','Mirror incidence angle (°)'],['short','Short arm (mm)'],['long','Return arm (mm)'],['toMirror','Coupler to first curved mirror (mm)'],['crystal','Crystal length (mm)'],['index','Pump index in crystal']])
  numeric($('ring-fields'),label,path.ring,key);
selectField($('ring-fields'),'Crystal faces',[['normal','Normal incidence'],['brewster','Coplanar Brewster cut']],path.ring.cut,value=>{path.ring.cut=value;edited();});
addWaistFields($('target-fields'),path.target);
$('target-kind').value=path.targetType;
$('ring').hidden=path.targetType!=='ring';$('waists').hidden=path.targetType!=='waists';
$('target-kind').onchange=()=>{
  path.targetType=$('target-kind').value;
  $('ring').hidden=path.targetType!=='ring';$('waists').hidden=path.targetType!=='waists';edited();
};

function renderLenses() {
  $('lenses').replaceChildren();fieldInputs.clear();
  for (const [i,lens] of path.lenses.entries()) {
    const row=document.createElement('div');row.className='lens';const name='L'+(i+1);
    const title=document.createElement('strong');title.textContent=name;row.append(title);
    const position=numeric(row,'Position (mm)',lens,'z');
    position.setAttribute('aria-label',name+' position (mm)');fieldInputs.set(lens,position);
    numeric(row,'Focal length (mm)',lens,'f').setAttribute('aria-label',name+' focal length (mm)');
    selectField(row,'Lens type',[['both','Spherical'],['t','Cylindrical · T'],['s','Cylindrical · S']],lens.axis,value=>{lens.axis=value;edited();});
    const lock=document.createElement('input');lock.type='checkbox';lock.checked=lens.locked;
    const label=labelFor(row,'Lock',lock);label.className='check';lock.setAttribute('aria-label','Lock '+name+' during drag and optimization');
    lock.onchange=()=>{lens.locked=lock.checked;edited();};
    const remove=document.createElement('button'); remove.className='button quiet';remove.textContent='Remove';
    remove.setAttribute('aria-label','Remove '+name);remove.onclick=()=>{path.lenses.splice(i,1);renderLenses();edited();};
    row.append(remove);$('lenses').append(row);
  }
  if (!path.lenses.length) $('lenses').textContent='Free propagation. Add a lens at the probe to focus the beam.';
}
function resetSearch(){
  worker?.terminate();worker=null;proposal=null;$('proposal').hidden=true;
  $('apply-layout').hidden=true;$('moves').textContent='';$('limits').hidden=true;
}
function edited(){
  resetSearch();$('share-status').hidden=true;$('launch-result').hidden=true;analyze();
}
function probeValue(){return Number($('probe').value);}
function updateProbe(){
  const z=probeValue();$('probe-z').textContent=fmt(z,1)+' mm';
  if (beamValid) {
    const q=propagate(sourceQ(path),lensOrder(path.lenses),z);
    const beam=q.map(v=>spot(v,path.wavelength));
    $('probe-w').textContent='T / S   '+beam.map(v=>fmt(v.w,1)).join(' / ')+' µm   ·   1/R   '+beam.map(v=>fmt(1000*v.curvature,2)).join(' / ')+' m⁻¹';
  } else $('probe-w').textContent='';
  draw($('plot'),beamValid?path:null,target,z);
}
function setModeValues(prefix, q) {
  axes.forEach((axis,i)=>{
    const value=q && spot(q[i],path.wavelength);
    $(prefix+'w'+axis).textContent=value?fmt(value.w,1):'—';
    $(prefix+'c'+axis).textContent=value?fmt(value.curvature*1000,3):'—';
  });
}
function analyze(){
  showImportNote();
  $('overlap-label').textContent = approximate ? 'Spatial mode overlap · separable input model' : 'Spatial mode overlap';
  let error='', output;beamValid=false;target=null;
  try {
    validatePath(path);beamValid=true;
    $('probe').max=path.length;
    output=propagate(sourceQ(path),lensOrder(path.lenses),path.length);
    target=targetMode(path);
  } catch(e){error=e.message;}
  $('invalid').hidden=!error;$('invalid').textContent=error;
  setModeValues('',output);setModeValues('t',target?.q);
  $('overlap').textContent=target?percent(overlap(output,target.q)):'—';
  $('ring-result').textContent=target?.trace?'Crystal-center radii (T / S)   '+target.crystalRadius.map(w=>fmt(w,2)).join(' / ')+' µm. Stability (A+D)/2   '+target.trace.map(g=>fmt(g,3)).join(' / ')+'.':'';
  $('share').disabled=!target;
  $('wavelengths').textContent=Number.isFinite(path.wavelength)&&path.wavelength>0?fmt(path.wavelength,3)+' → '+fmt(path.wavelength/2,3)+' nm':'—';
  $('add').disabled=!Number.isFinite(path.length)||path.length<1||path.lenses.length>=maxLenses;
  $('search').disabled=!target||!!worker||!path.lenses.some(l=>!l.locked);
  if (ref.cavity) {
    let state = '';
    try {
      const {w, transmission} = apertureTransmission(path, path.launch.aperture);
      state = 'Beam radius at the coupler lens '+fmt(w, 3)+' mm. The '+fmt(path.launch.aperture, 2)+' mm clear aperture passes '+(100*transmission).toFixed(transmission > 0.9999 ? 4 : 2)+'% of the power.';
      $('solve-launch').disabled = !target;
    } catch (e) { state = e.message; $('solve-launch').disabled = true; }
    $('launch-state').textContent = state;
  }
  refreshFields();
  updateProbe();
}
$('probe').oninput=updateProbe;
$('plot').onpointerdown=e=>{
  if (!beamValid) return;
  dragging={lens:lensMarkerAt($('plot'),e,path)};
  if (dragging.lens?.locked) {dragging=null;return;}
  $('plot').setPointerCapture(e.pointerId);if (!dragging.lens) {$('probe').value=positionAt($('plot'),e,path.length);updateProbe();}
};
$('plot').onpointermove=e=>{
  if (!dragging) return;
  const z=Number(positionAt($('plot'),e,path.length).toFixed(3));
  if(dragging.lens){dragging.lens.z=z;fieldInputs.get(dragging.lens).value=z;edited();}
  else {$('probe').value=z;updateProbe();}
};
$('plot').onpointerup=$('plot').onpointercancel=()=>{dragging=null;};
$('add').onclick=()=>{path.lenses.push({z:probeValue(),f:100,axis:'both',locked:false});renderLenses();edited();};
$('dismiss').onclick=()=>{resetSearch();analyze();};
$('switch-f').onchange=()=>{$('inventory').hidden=!$('switch-f').checked;edited();};
$('gap').oninput=$('catalog').oninput=edited;
const resultUI = {
  panel: $('proposal'), apply: $('apply-layout'), status: $('search-status'),
  limits: $('limits'), moves: $('moves')
};
function searchMessage(text){$('proposal').hidden=false;$('search-status').textContent=text;}
$('search').onclick=()=>{
  resetSearch();
  const catalog=$('switch-f').checked?$('catalog').value.split(',').map(s=>s.trim()?Number(s):NaN):[];
  try{
    const job=new Worker(new URL('./search-worker.mjs',import.meta.url),{type:'module'});
    worker=job;$('search').disabled=true;searchMessage('Searching lens positions…');
    const finish=()=>{job.terminate();worker=null;analyze();};
    job.onmessage=({data})=>{
      if(worker!==job)return;
      finish();
      if(data.error){searchMessage(data.error);return;}
      proposal=data.result;
      renderSearchResult(proposal, resultUI);
    };
    job.onerror=()=>{if(worker===job){finish();searchMessage('Could not run the layout search. Reload the page and retry.');}};
    job.postMessage({path,gap:$('gap').valueAsNumber,catalog});
  } catch(e){resetSearch();analyze();searchMessage('The layout search could not start. '+e.message);}
};
$('apply-layout').onclick=()=>{
  if (!proposal) return;
  const applied = proposal;
  path.lenses = applied.lenses;
  renderLenses();
  edited();
  renderSearchResult(applied, resultUI, true);
};
$('share').onclick=async()=>{
  const link = shareLayout(location.href, {stage, path, gap: $('gap').valueAsNumber, fit});
  const status=$('share-status');status.replaceChildren();status.hidden=false;
  try {await navigator.clipboard.writeText(link.href);status.textContent='Layout link copied.';}
  catch {
    const input=document.createElement('input');input.readOnly=true;input.value=link.href;
    labelFor(status,'Copy this layout link',input);input.select();
  }
};
if (ref.cavity) {
  $('fiber-launch').hidden = false;
  const lens = () => path.lenses[0];
  const offset = () => lens() ? lens().z - lens().f : NaN, distance = () => lens() ? path.length - lens().z : NaN;
  const fields = $('fiber-fields');
  derived(fields, 'Mode field diameter (µm)', () => 2*path.source.t.w, v => { path.source.t.w = path.source.s.w = v/2; });
  derived(fields, 'Coupler focal length (mm)', () => lens()?.f ?? NaN, v => {
    if (!lens()) return; const d = offset(), D = distance(); lens().f = v; applyLaunch(path, d, D); });
  derived(fields, 'Focus offset from focal length (µm)', () => 1000*offset(), v => { if (lens()) applyLaunch(path, v/1000, distance()); }, 1);
  derived(fields, 'Coupler lens to input coupler (mm)', () => distance(), v => { if (lens()) applyLaunch(path, offset(), v); }, 2);
  numeric(fields, 'Focus travel, ± (mm)', path.launch, 'travel');
  numeric(fields, 'Clear aperture (mm)', path.launch, 'aperture');
  numeric(fields, 'Shortest distance on the bench (mm)', path.launch, 'minDistance');
  numeric(fields, 'Longest distance on the bench (mm)', path.launch, 'maxDistance');
  $('solve-launch').onclick = () => {
    const result = $('launch-result');
    try {
      const target = targetMode(path).q;
      const before = overlap(propagate(sourceQ(path), lensOrder(path.lenses), path.length), target);
      const found = solveLaunch(path, target, path.launch);
      applyLaunch(path, found.delta, found.D);
      renderLenses(); edited();
      const parts = ['Focus offset '+(found.delta >= 0 ? '+' : '')+fmt(1000*found.delta, 1)+' µm and '+fmt(found.D, 1)+' mm to the input coupler give '+percent(found.eta)+' overlap (previously '+percent(before)+').'];
      if (1 - found.eta > 1e-4) parts.push('The rest is lost because the cavity mode differs between the tangential and sagittal planes, which a round fibre beam cannot follow.');
      parts.push('A focus error of ±'+fmt(1000*found.tolerance.delta, 1)+' µm, or a distance error of ±'+fmt(found.tolerance.D, 1)+' mm, costs 1% of that overlap.');
      if (found.limits.length) parts.push('The result sits at the '+found.limits.join(' and ')+'; widen it if the bench allows.');
      result.textContent = parts.join(' ');
    } catch (e) { result.textContent = e.message; }
    result.hidden = false;
  };
}
renderLenses();analyze();
new ResizeObserver(updateProbe).observe($('plot'));
// Opening a second shared design in this tab can be a fragment-only navigation.
window.addEventListener('hashchange', ({oldURL, newURL}) => {
  if ([oldURL, newURL].some(url => new URL(url).hash.startsWith('#layout='))) location.reload();
});
