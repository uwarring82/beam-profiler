// Apply the same clearance readout to a proposed, unchanged, or applied layout.
export function renderSearchResult(result, ui, applied = false) {
  const improved = result.after > result.before+1e-12;
  const percent = n => (100*n).toFixed(2)+'%';
  ui.panel.hidden = false;
  ui.apply.hidden = applied || !improved;
  if (applied) ui.status.textContent = 'Layout applied.';
  else if (improved) ui.status.textContent = 'Proposed spatial overlap   '+percent(result.before)+' → '+percent(result.after);
  else ui.status.textContent = 'No improvement found for this layout.';

  ui.moves.textContent = ui.apply.hidden ? '' : result.lenses.map((l,i) =>
    'L'+(i+1)+' at '+Number(l.z.toFixed(3))+' mm (f = '+Number(l.f.toFixed(3))+' mm)'
  ).join(' · ');

  ui.limits.hidden = result.limits.length === 0;
  ui.limits.textContent = result.limits.map(({lens, side, neighbor}) => {
    const boundary = side === 'start' ? 'the start of the rail'
      : side === 'coupler' ? 'the input coupler' : 'L'+(neighbor+1);
    return 'L'+(lens+1)+' is at minimum clearance from '+boundary+'.';
  }).join(' ');
}
