import {axes, lensOrder, maxLenses, overlap, propagate, sourceQ, spot, targetMode, validatePath} from './optics.mjs';
import {showcase, showcases} from './showcases.mjs';
import {draw, positionAt} from './plot.mjs';

const $ = id => document.getElementById(id);
const fmt = (number, digits=2) => Number(number.toFixed(digits)).toLocaleString('en-US', {maximumFractionDigits:digits});
const percent = n => (100*n).toFixed(2)+'%';
const params = new URLSearchParams(location.search);
const stage = params.get('stage')==='bbo'?'bbo':'lbo';
const ref = showcases[stage];
let path = showcase(stage);
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
function importFit(){
  if (!params.has('wt') && !params.has('ws')) return;
  const values=['wt','ws'].map(key=>Number(params.get(key)));
  $('import-note').hidden=false;
  if (!values.every(v=>Number.isFinite(v)&&v>=.1&&v<=100000)) {
    $('import-note').textContent='The fit radii are outside the supported range. The showcase launch is loaded.';return;
  }
  axes.forEach((a,i)=>{path.source[a].w=values[i];});
  $('import-note').textContent='Gaussian fit imported as a waist at the rail origin, with fitted u/v assigned to T/S. A single image gives no wavefront curvature. Confirm the waist locations, axis alignment and pump wavelength.';
}
// Copy only known fields of a versioned design from an untrusted link.
function readShape(shape, input) {
  if (Array.isArray(shape)) {
    if (!Array.isArray(input) || input.length>maxLenses) throw Error();
    return input.map(item=>readShape(shape[0],item));
  }
  if (typeof shape==='object') {
    if (!input || typeof input!=='object') throw Error();
    return Object.fromEntries(Object.entries(shape).map(([k,v])=>[k,readShape(v,input[k])]));
  }
  if (typeof input!==typeof shape || (typeof input==='number' && !Number.isFinite(input))) throw Error();
  return input;
}
if (location.hash.startsWith('#layout=')) {
  try {
    if (location.hash.length>20000) throw Error();
    const data=JSON.parse(decodeURIComponent(location.hash.slice(8)));
    if (data.v!==1) throw Error();
    const incoming=readShape(path,data.path);
    validatePath(incoming); targetMode(incoming); path=incoming;
  } catch {
    $('import-note').hidden=false;$('import-note').textContent='This layout link could not be read. The original showcase is loaded.';
  }
} else importFit();
document.querySelector('[data-stage="'+stage+'"]').setAttribute('aria-current','true');
$('reference').textContent='Published '+stage.toUpperCase()+' stage · '+ref.pump+' nm pump, '+ref.short+' mm between focusing mirrors, '+ref.total+' mm total cavity length and '+ref.fold+'° full fold. The paper gives a '+ref.designWaist+' µm design waist. Table 1 reports '+ref.inputPower.toFixed(2)+' W incident, '+ref.outputPower+' W output and '+ref.efficiency+'% doubling efficiency.';
$('assumptions').textContent='Showcase assumptions · The input waist, injection lens focal lengths and placements, and distance from the coupler to the first curved mirror are illustrative. We interpret the tabulated cavity length as a geometric round trip and the crystal length along the central ray. The scalar pump index is set to '+ref.index.toFixed(2)+'. '+(stage==='bbo'?'The Brewster plane lies in the cavity plane. The model carries the 25 mm mirror focal length over from the first stage.':'The 25 mm focusing mirrors have a 50 mm radius of curvature.');
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
  $('apply-layout').hidden=true;$('moves').textContent='';
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
      if(proposal.after<=proposal.before+1e-12){searchMessage('No improvement found for this layout.');return;}
      searchMessage('Proposed spatial overlap   '+percent(proposal.before)+' → '+percent(proposal.after));
      $('moves').textContent=proposal.lenses.map((l,i)=>'L'+(i+1)+' at '+fmt(l.z,3)+' mm (f = '+fmt(l.f,3)+' mm)').join(' · ');
      $('apply-layout').hidden=false;
    };
    job.onerror=()=>{if(worker===job){finish();searchMessage('Could not run the layout search. Reload the page and retry.');}};
    job.postMessage({path,gap:$('gap').valueAsNumber,catalog});
  } catch(e){resetSearch();analyze();searchMessage('The layout search could not start. '+e.message);}
};
$('apply-layout').onclick=()=>{if(proposal){path.lenses=proposal.lenses;renderLenses();edited();}};
$('share').onclick=async()=>{
  const link=new URL(location.href);
  link.search=new URLSearchParams({stage}).toString();
  const stored={...path};
  // An unfinished edit in a hidden target form cannot affect this design.
  const unused=path.targetType==='waists'?'ring':'target';
  const defaults=showcase(stage);
  try {readShape(defaults[unused],stored[unused]);}
  catch {stored[unused]=defaults[unused];}
  link.hash='layout='+encodeURIComponent(JSON.stringify({v:1,path:stored}));
  const status=$('share-status');status.replaceChildren();status.hidden=false;
  try {await navigator.clipboard.writeText(link.href);status.textContent='Layout link copied.';}
  catch {
    const input=document.createElement('input');input.readOnly=true;input.value=link.href;
    labelFor(status,'Copy this layout link',input);input.select();
  }
};
renderLenses();analyze();
new ResizeObserver(updateProbe).observe($('plot'));
