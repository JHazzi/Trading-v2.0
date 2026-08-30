const $ = id => document.getElementById(id);
const clamp = (x,a,b)=>Math.max(a,Math.min(b,x));
const pct = x => x===null||x===undefined||Number.isNaN(Number(x)) ? '—' : `${Number(x)>=0?'+':''}${Number(x).toFixed(2)}%`;
const num = x => x===null||x===undefined||Number.isNaN(Number(x)) ? '—' : Number(x).toFixed(2);
const badge = x => `<span class="badge ${x}">${String(x||'UNAVAILABLE').replaceAll('_',' ')}</span>`;
const esc = value => String(value??'').replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[c]));

let APP_STATE=null;
let SNAPSHOT_HASH='';
let ACTIVE_HORIZON=null;

function horizonSessions(h){
  const m=String(h||'').match(/^H(\d+)$/i);
  return m?Number(m[1]):null;
}

function setupTabs(){
  document.querySelectorAll('.tab-btn').forEach(btn=>btn.addEventListener('click',()=>{
    document.querySelectorAll('.tab-btn').forEach(x=>x.classList.toggle('active',x===btn));
    document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.dataset.panel===btn.dataset.tab));
  }));
}

function renderHorizonSwitch(targetId, forecasts){
  const host=$(targetId); if(!host)return;
  host.innerHTML=forecasts.map(f=>`<button type="button" class="horizon-btn ${f.horizon===ACTIVE_HORIZON?'active':''}" data-horizon="${esc(f.horizon)}">${esc(f.horizon)}</button>`).join('');
  host.querySelectorAll('.horizon-btn').forEach(btn=>btn.addEventListener('click',()=>{
    ACTIVE_HORIZON=btn.dataset.horizon;
    renderForecastSurfaces(APP_STATE);
  }));
}

function selectedForecast(state){
  const list=state.forecasts||[];
  return list.find(f=>f.horizon===ACTIVE_HORIZON)||list[0]||null;
}

function linePath(points,x,y,key){
  return points.map((p,i)=>`${i?'L':'M'} ${x(i).toFixed(2)} ${y(Number(p[key])).toFixed(2)}`).join(' ');
}
function polygonPath(top,bottom,x,y,topKey,bottomKey){
  const a=top.map((p,i)=>`${i?'L':'M'} ${x(i).toFixed(2)} ${y(Number(p[topKey])).toFixed(2)}`).join(' ');
  const b=[...bottom].reverse().map((p,ri)=>{const i=bottom.length-1-ri;return `L ${x(i).toFixed(2)} ${y(Number(p[bottomKey])).toFixed(2)}`}).join(' ');
  return `${a} ${b} Z`;
}

function getChartData(state,forecast){
  if(!forecast?.trajectory?.points?.length)return null;
  const history=state.history?.points||[];
  const traj=forecast.trajectory.points;
  const historyMode=state.history?.mode||'NORMALIZED_INDEX';
  let base;
  if(historyMode==='PRICE' && state.observation?.price!=null) base=Number(state.observation.price);
  else if(history.length) base=Number(history[history.length-1].value);
  else base=100;
  const future=traj.map(p=>{
    const q={};
    ['q05','q25','q50','q75','q95'].forEach(k=>q[k]=base*(1+Number(p.quantiles[k])/100));
    return {...p,...q};
  });
  return {history,future,base,historyMode};
}

function eventMarkers(state,forecast){
  const dates=(forecast?.trajectory?.points||[]).map(p=>p.date).filter(Boolean);
  if(!dates.length)return [];
  return (state.events||[]).filter(e=>e.scheduled_for).map(e=>{
    let best=0,bestDist=Infinity;
    const t=Date.parse(e.scheduled_for);
    dates.forEach((d,i)=>{const dist=Math.abs(Date.parse(d)-t);if(dist<bestDist){bestDist=dist;best=i}});
    return {index:best,label:e.title||e.event_type,date:e.scheduled_for};
  });
}

function renderPriceChart(hostId,state,forecast){
  const host=$(hostId); if(!host)return;
  const data=getChartData(state,forecast);
  if(!data){host.innerHTML='<div class="empty-state">Este forecast publica cuantiles al horizonte, pero todavía no publica una trayectoria temporal. El Workbench no inventa una curva intermedia.</div>';return;}
  const W=1100,H=420,P={l:62,r:35,t:30,b:48};
  const history=data.history;
  const future=data.future;
  const gap=1;
  const total=Math.max(2,history.length+future.length+gap);
  const allY=[];
  history.forEach(p=>allY.push(Number(p.value)));
  future.forEach(p=>['q05','q25','q50','q75','q95'].forEach(k=>allY.push(Number(p[k]))));
  let ymin=Math.min(...allY),ymax=Math.max(...allY); const pad=Math.max((ymax-ymin)*.12,1); ymin-=pad;ymax+=pad;
  const plotW=W-P.l-P.r,plotH=H-P.t-P.b;
  const xHist=i=>P.l+(i/(Math.max(1,total-1)))*plotW;
  const startFutureIndex=history.length+gap-1;
  const xFuture=i=>P.l+((startFutureIndex+i)/(Math.max(1,total-1)))*plotW;
  const y=v=>P.t+(ymax-v)/(ymax-ymin)*plotH;
  const histPath=history.map((p,i)=>`${i?'L':'M'} ${xHist(i).toFixed(2)} ${y(Number(p.value)).toFixed(2)}`).join(' ');
  const band=polygonPath(future,future,xFuture,y,'q95','q05');
  const inner=polygonPath(future,future,xFuture,y,'q75','q25');
  const q50=linePath(future,xFuture,y,'q50'),q75=linePath(future,xFuture,y,'q75'),q25=linePath(future,xFuture,y,'q25');
  const nowX=xHist(Math.max(0,history.length-1));
  const yTicks=5;
  let grid='';
  for(let i=0;i<=yTicks;i++){const value=ymin+(ymax-ymin)*(i/yTicks);const yy=y(value);grid+=`<line x1="${P.l}" y1="${yy}" x2="${W-P.r}" y2="${yy}" class="svg-grid"/><text x="${P.l-10}" y="${yy+4}" text-anchor="end" class="svg-label">${data.historyMode==='PRICE'?num(value):num(value)}</text>`}
  const histLabels=history.length?[0,Math.floor((history.length-1)/2),history.length-1]:[];
  let labels=histLabels.map(i=>`<text x="${xHist(i)}" y="${H-17}" text-anchor="middle" class="svg-small">${esc(history[i].label||'')}</text>`).join('');
  const futLabelIdx=[0,Math.floor((future.length-1)/2),future.length-1].filter((v,i,a)=>a.indexOf(v)===i);
  labels+=futLabelIdx.map(i=>`<text x="${xFuture(i)}" y="${H-17}" text-anchor="middle" class="svg-small">${esc(future[i].label||`+${future[i].offset_sessions||i}`)}</text>`).join('');
  const markers=eventMarkers(state,forecast).map(m=>{const xx=xFuture(m.index);return `<line x1="${xx}" y1="${P.t}" x2="${xx}" y2="${H-P.b}" class="svg-event"/><circle cx="${xx}" cy="${P.t+18}" r="4" class="svg-event-dot"/><text x="${xx+5}" y="${P.t+12}" class="svg-small">${esc(m.label)}</text>`}).join('');
  const last=future[future.length-1]; const lx=xFuture(future.length-1)+5;
  const unit=data.historyMode==='PRICE'?(state.observation?.currency||'$'):'Índice';
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Histórico y escenarios futuros del forecast ${esc(forecast.horizon)}">
    ${grid}
    <text x="${P.l}" y="17" class="svg-small">${esc(unit)} · ${data.historyMode==='PRICE'?'precio':'normalizado, ahora=100'}</text>
    <text x="${P.l+plotW*.22}" y="22" class="svg-title">Histórico</text>
    <text x="${P.l+plotW*.68}" y="22" class="svg-title">Forecast ${esc(forecast.horizon)}</text>
    <path d="${band}" class="svg-band"/><path d="${inner}" class="svg-band-inner"/>
    <path d="${histPath}" class="svg-history"/><path d="${q75}" class="svg-bull"/><path d="${q50}" class="svg-central"/><path d="${q25}" class="svg-bear"/>
    <line x1="${nowX}" y1="${P.t}" x2="${nowX}" y2="${H-P.b}" class="svg-now"/><text x="${nowX}" y="${H-P.b+17}" text-anchor="middle" class="svg-title">Ahora</text>
    ${markers}${labels}
    <text x="${lx}" y="${y(last.q75)-6}" class="svg-small" style="fill:#43d16d">q75 alcista</text>
    <text x="${lx}" y="${y(last.q50)-6}" class="svg-small" style="fill:#4e98ff">q50 central</text>
    <text x="${lx}" y="${y(last.q25)+12}" class="svg-small" style="fill:#ff625f">q25 bajista</text>
  </svg>`;
}

function renderConfidenceChart(state,forecast){
  const host=$('confidence-chart');
  const points=forecast?.confidence?.points||[];
  $('confidence-horizon-label').textContent=forecast?.horizon||'—';
  if(!points.length){host.innerHTML='<div class="empty-state">No hay una serie de confianza publicada para este horizonte. El Workbench no asume que la confianza disminuye con el tiempo.</div>';return;}
  const W=1100,H=245,P={l:52,r:35,t:25,b:42},plotW=W-P.l-P.r,plotH=H-P.t-P.b;
  const x=i=>P.l+(i/Math.max(1,points.length-1))*plotW, y=v=>P.t+(100-clamp(Number(v),0,100))/100*plotH;
  const path=points.map((p,i)=>`${i?'L':'M'} ${x(i)} ${y(p.score)}`).join(' ');
  const area=`${path} L ${x(points.length-1)} ${H-P.b} L ${x(0)} ${H-P.b} Z`;
  let grid=''; [0,25,50,75,100].forEach(v=>{grid+=`<line x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}" class="svg-grid"/><text x="${P.l-8}" y="${y(v)+4}" text-anchor="end" class="svg-label">${v}%</text>`});
  const markers=eventMarkers(state,forecast).map(m=>{const xx=x(clamp(m.index,0,points.length-1));return `<line x1="${xx}" y1="${P.t}" x2="${xx}" y2="${H-P.b}" class="svg-event"/><circle cx="${xx}" cy="${y(points[clamp(m.index,0,points.length-1)].score)}" r="4" class="svg-event-dot"/><text x="${xx+5}" y="${P.t+11}" class="svg-small">${esc(m.label)}</text>`}).join('');
  const labelIdx=[0,Math.floor((points.length-1)/2),points.length-1].filter((v,i,a)=>a.indexOf(v)===i);
  const labels=labelIdx.map(i=>`<text x="${x(i)}" y="${H-14}" text-anchor="middle" class="svg-small">${esc(points[i].label||`+${points[i].offset_sessions||i}`)}</text>`).join('');
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Confianza no monótona del forecast ${esc(forecast.horizon)}">${grid}<path d="${area}" class="svg-confidence-area"/><path d="${path}" class="svg-confidence"/>${markers}${labels}</svg>`;
}

function renderDistribution(forecast){
  $('dist-horizon-label').textContent=forecast?.horizon||'—';
  const host=$('distribution-summary');
  if(!forecast){host.innerHTML='<div class="empty-state">Sin forecast.</div>';return;}
  const q=forecast.quantiles;
  const keys=['q05','q25','q50','q75','q95'];
  const values=keys.map(k=>Number(q[k])); let mn=Math.min(...values),mx=Math.max(...values); if(mx===mn)mx=mn+1;
  const nodes=keys.map(k=>{const pos=(Number(q[k])-mn)/(mx-mn)*100;return `<div class="dist-node ${k}" style="left:${pos}%"><i></i><b>${k}</b><span>${pct(q[k])}</span></div>`}).join('');
  const iqr=Number(q.q75)-Number(q.q25), outer=Number(q.q95)-Number(q.q05);
  host.innerHTML=`<div class="distribution-track">${nodes}</div><div class="dist-meta"><div class="metric"><span>Rango central q25–q75</span><b>${iqr.toFixed(2)} pp</b></div><div class="metric"><span>Rango q05–q95</span><b>${outer.toFixed(2)} pp</b></div></div><p class="help-copy">Estos números son <b>retornos acumulados al final de ${esc(forecast.horizon)}</b>, no precios y no probabilidades de subir.</p>`;
}

function renderForecastSurfaces(state){
  const forecasts=state.forecasts||[];
  if(!ACTIVE_HORIZON && forecasts.length){ACTIVE_HORIZON=(forecasts.find(f=>f.horizon==='H5')||forecasts[0]).horizon;}
  renderHorizonSwitch('overview-horizon-switch',forecasts);
  renderHorizonSwitch('forecast-horizon-switch',forecasts);
  const f=selectedForecast(state);
  renderPriceChart('overview-price-chart',state,f);
  renderPriceChart('price-chart',state,f);
  renderConfidenceChart(state,f);
  renderDistribution(f);
  renderRisk(state,f);
}

function renderCapabilities(state){
  const caps=state.capabilities||{};
  $('capabilities').innerHTML=Object.entries(caps).map(([k,v])=>`<div class="cap-row"><span>${esc(k.replaceAll('_',' '))}</span><strong class="${v?'cap-yes':'cap-no'}">${v?'SUPPORTED':'NOT ESTABLISHED'}</strong></div>`).join('')||'<div class="empty-state">Sin capabilities publicadas.</div>';
}

function renderEvents(state){
  $('events').innerHTML=(state.events||[]).map(e=>`<div class="event"><div class="event-main"><strong>${esc(e.title||e.event_type)}</strong><span>${esc(e.source||'unknown source')} · disponible ${esc(e.available_at)}</span>${e.scheduled_for?`<div class="event-time">Programado: ${esc(e.scheduled_for)}</div>`:''}</div>${badge(e.evidence_level)}</div>`).join('')||'<div class="empty-state">No hay eventos causales publicados.</div>';
}

function renderRisk(state,forecast){
  const host=$('risk-summary'); if(!host)return;
  if(!forecast){host.innerHTML='<div class="empty-state">Sin forecast para interpretar riesgo.</div>';return;}
  const q=forecast.quantiles;
  const downside=Math.abs(Math.min(0,Number(q.q05)));
  const upside=Math.max(0,Number(q.q95));
  host.innerHTML=`
    <div class="risk-row"><b>Cola bajista q05</b><span>${pct(q.q05)} al horizonte ${esc(forecast.horizon)}. No es una pérdida máxima; es el percentil 5 del modelo.</span></div>
    <div class="risk-row"><b>Cola alcista q95</b><span>${pct(q.q95)} al horizonte ${esc(forecast.horizon)}.</span></div>
    <div class="risk-row"><b>Asimetría descriptiva</b><span>Magnitud q05: ${downside.toFixed(2)} pp · q95: ${upside.toFixed(2)} pp. Esto describe la distribución publicada, no una recomendación.</span></div>
    <div class="risk-row"><b>Confianza ≠ dirección</b><span>Un forecast puede estar bien calibrado respecto de volatilidad/colas y seguir sin tener alpha direccional.</span></div>`;
}

function renderOverview(state){
  const f=selectedForecast(state);
  const caps=state.capabilities||{};
  const currentConfidence=f?.confidence?.points?.length?f.confidence.points[f.confidence.points.length-1].score:null;
  $('overview-status').innerHTML=`
    <div class="status-card"><span>Distribución</span><b>${caps.distribution_shape?'Modelada':'No establecida'}</b></div>
    <div class="status-card"><span>Alpha direccional</span><b>${caps.directional_alpha?'Soportado':'No establecido'}</b></div>
    <div class="status-card"><span>Confianza ${esc(f?.horizon||'')}</span><b>${currentConfidence==null?'No publicada':`${Number(currentConfidence).toFixed(0)}%`}</b></div>`;
}

function renderBase(state,hash){
  APP_STATE=state;SNAPSHOT_HASH=hash;
  $('ticker').textContent=state.asset.ticker;
  $('name').textContent=state.asset.name||'';
  $('asof').textContent=`Datos: ${state.generated_at}`;
  $('price').textContent=state.observation?.price==null?'Precio no publicado':`${state.observation.currency||'$'}${Number(state.observation.price).toFixed(2)}`;
  $('market-summary').textContent=state.market_state?.summary||'No hay resumen de market state publicado.';
  const d=state.decision;
  $('decision').innerHTML=`<strong>${esc(d.status.replaceAll('_',' '))}</strong>${badge(d.evidence_level)}<div class="muted" style="margin-top:7px">${esc(d.reason||'')}</div>`;
  const p=state.provenance||{};
  $('provenance').innerHTML=[['Publisher',p.publisher_version],['Source',p.source_description],['Snapshot SHA256',hash],['Notas',p.notes]].filter(x=>x[1]).map(([k,v])=>`<div class="prov-row"><span>${esc(k)}</span><span class="muted">${esc(v)}</span></div>`).join('');
  if(p.sample_mode){const w=$('sample-warning');w.textContent='MODO DEMO: las trayectorias y valores de este sample son ilustrativos para validar la UI. No representan una predicción real de este activo.';w.classList.remove('hidden');}
  renderCapabilities(state);renderEvents(state);renderForecastSurfaces(state);renderOverview(state);
}

async function boot(){
  setupTabs();
  try{
    const r=await fetch('/api/state',{cache:'no-store'});const x=await r.json();if(!r.ok)throw new Error(x.error||'State load failed');renderBase(x.state,x.snapshot_sha256);
  }catch(e){$('error').textContent=e.message;$('error').classList.remove('hidden');}
}

$('save-journal').addEventListener('click',async()=>{const out=$('journal-status');try{const r=await fetch('/api/journal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stance:$('stance').value,note:$('note').value})});const x=await r.json();if(!r.ok)throw new Error(x.error);out.textContent=`Guardado: ${x.record.recorded_at}`;$('note').value=''}catch(e){out.textContent=`Journal: ${e.message}`}});
boot();
