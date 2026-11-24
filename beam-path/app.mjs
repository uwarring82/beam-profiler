import {axes, lensOrder, maxLenses, overlap, propagate, sourceQ, spot, targetMode, validatePath} from './optics.mjs';
import {showcases} from './showcases.mjs';
import {fitProjection, openLayout, shareLayout} from './layout.mjs';
import {renderSearchResult} from './search-result.mjs';
import {draw, positionAt} from './plot.mjs';

const $ = id => document.getElementById(id);
const fmt = (number, digits=2) => Number(number.toFixed(digits)).toLocaleString('en-US', {maximumFractionDigits:digits});
const percent = n => (100*n).toFixed(2)+'%';
const {stage, path, gap, fit, notice} = openLayout(new URL(location.href));
const ref = showcases[stage];
const projected = fit && fitProjection(fit);
const approximate = projected?.fitOverlap < 0.99;
$('gap').value = gap;
let beamValid=false, target, worker, proposal, dragging=null;
const fieldInputs = new Map();

function labelFor(parent, text, input) {
  const label = document.createElement('label');
  label.append(document.createTextNode(text),input); parent.append(label); return label;
}
function numeric(parent, label, object, key) {
  const field=document.createElement('input');
  field.type='number'; field.step='any'; field.value=object[key];
  field.oninput=()=>{object[key]=field.valueAsNumber; edited();};
  labelFor(parent,label,field);
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
$('reference').textContent='Published '+stage.toUpperCase()+' stage · '+ref.pump+' nm pump, '+ref.short+' mm between focusing mirrors, '+ref.total+' mm total cavity length and '+ref.fold+'° full fold. The paper gives a '+ref.designWaist+' µm design waist. Table 1 reports '+ref.inputPower.toFixed(2)+' W incident, '+ref.outputPower+' W output and '+ref.efficiency+'% doubling efficiency.';
$('assumptions').textContent='Showcase assumptions · The '+ref.rail+' mm external injection rail, input waist, injection lens focal lengths and placements, and distance from the coupler to the first curved mirror are illustrative. We interpret the tabulated cavity length as a geometric round trip and the crystal length along the central ray. The scalar pump index is set to '+ref.index.toFixed(2)+'. '+(stage==='bbo'?'The Brewster plane lies in the cavity plane. The model carries the 25 mm mirror focal length over from the first stage.':'The 25 mm focusing mirrors have a 50 mm radius of curvature.');
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
  resetSearch();$('share-status').hidden=true;analyze();
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
  updateProbe();
}
$('probe').oninput=updateProbe;
$('plot').onpointerdown=e=>{
  if (!beamValid) return;
  const box=$('plot').getBoundingClientRect(),z=positionAt($('plot'),e,path.length);
  const pointIsHandle=e.clientY-box.top<=31;
  const lens=[...path.lenses].reverse().find(l=>Math.abs(l.z-z)<14*path.length/(box.width-71));
  dragging={lens:pointIsHandle?lens:null};
  if (dragging.lens?.locked) {dragging=null;return;}
  $('plot').setPointerCapture(e.pointerId);if (!dragging.lens) {$('probe').value=z;updateProbe();}
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
renderLenses();analyze();
new ResizeObserver(updateProbe).observe($('plot'));
