import {correctedRam} from './memory';
import {useRef,useState} from 'react';
import type {PointerEvent} from 'react';
import {ArrowDown, ArrowUp, Check, ChevronDown, Copy, GripVertical, Pencil, Play, RotateCcw, Settings2, Trash2} from 'lucide-react';
import type {Model, Settings, Profile, BenchmarkResult, ProfileSpeedSource} from './types';
import {formatNumber} from './ui';
import './profiles.css';
import {reasoningLabel} from './reasoning';

export function suggestedProfileName(model:Model, settings:Settings) {
  const name=model.name.replace(/-Uncensored-HauhauCS-Aggressive/i,' Agg.').replace(/[-_]?(EXL3|NVFP4|UD-|Q\d|IQ\d|MXFP\d).*$/,'').replace(/[-_]/g,' ');
  return `${name} · ${model.quant.replace(' bpw','bpw')} · ${settings.context/1024}K/${settings.kv} · V:${settings.vision?'on':'off'} · MTP:${settings.prediction==='mtp'?`on/${settings.draft_tokens}`:'off'} · CPU:${settings.cpu_percent}%`;
}

function Result({result}:{result:BenchmarkResult}) {
  return <div className="profile-result">
    <div><strong>{result.kind==='speed'?'Speed test':result.benchmark||'Coding benchmark'}</strong><span>{new Date(result.created*1000).toLocaleDateString()} · {result.state}</span></div>
    {result.kind==='speed'?<p><b>{formatNumber(result.median_tps)} tok/s</b> decode · {formatNumber(result.median_prompt_tps,0)} tok/s prompt</p>:
      <p><b>{result.summary?.passed??0}/{result.summary?.total??0}</b> passed · {result.summary?.graded??0} graded {result.summary?.score!=null&&`· ${result.summary.score}%`}</p>}
    {result.kind==='coding'&&(result.median_tps!=null||result.median_prompt_tps!=null)&&<p><b>{formatNumber(result.median_tps)} tok/s</b> decode · {formatNumber(result.median_prompt_tps,0)} tok/s prefill</p>}
    {result.kind==='coding'&&<a href={`/api/evaluations/${result.id}/report`} target="_blank" rel="noreferrer">Open report</a>}
  </div>;
}

const normalize=(s:string)=>s.toLowerCase().replace(/[-_]/g,' ');
const modelLabel=(model:Model|undefined)=>model?.name.replace(/[-_]?(EXL3|NVFP4|UD-|Q\d|IQ\d|MXFP\d).*$/,'').replace(/[-_]/g,' ')||'Unavailable model';
const sourceLabel=(source:ProfileSpeedSource|null|undefined)=>source?`${source.kind==='speed'?'Speed test':source.benchmark||'Coding benchmark'} · ${new Date(source.created*1000).toLocaleDateString()} · median`:'No matching measurement';
const gib=(bytes:number|null|undefined)=>bytes==null?'—':formatNumber(bytes/2**30,1);

type Props={profiles:Profile[];models:Model[];trash:boolean;busy:boolean;editingId:string|null;copiedId:string|null;
  onLoad:(p:Profile)=>void;onEdit:(p:Profile)=>void;onRename:(p:Profile)=>void;onDuplicate:(p:Profile)=>void;onDelete:(p:Profile)=>void;
  onRestore:(p:Profile)=>void;onCopy:(p:Profile)=>void;onReorder:(ids:string[])=>void;onBenchmarks:()=>void};

export function ProfileList(props:Props) {
  const {profiles,models,trash,busy}=props;
  const [query,setQuery]=useState(''),[modelFilter,setModelFilter]=useState(''),[sort,setSort]=useState('saved'),[context,setContext]=useState('');
  const [decode,setDecode]=useState(''),[prefill,setPrefill]=useState(''),[vram,setVram]=useState(''),[ram,setRam]=useState('');
  const filtered=!!(query||modelFilter||context||decode||prefill||vram||ram),canReorder=!filtered&&sort==='saved';
  const savedModels=models.filter(m=>profiles.some(p=>p.settings.model_id===m.id)).sort((a,b)=>a.name.localeCompare(b.name));
  const min=(value:number|null|undefined,bound:string)=>!bound||(value!=null&&value>=Number(bound));
  const max=(value:number|null|undefined,bound:string)=>!bound||(value!=null&&value/2**30<=Number(bound));
  const visible=profiles.filter(p=>{
    const model=models.find(m=>m.id===p.settings.model_id),text=normalize(`${model?.name} ${model?.quant} ${p.name}`);
    return normalize(query).split(/\s+/).filter(Boolean).every(term=>text.includes(term))&&(!modelFilter||modelFilter===p.settings.model_id)&&(!context||p.settings.context===Number(context))&&min(p.performance?.decode_tps,decode)&&min(p.performance?.prefill_tps,prefill)&&max(p.loaded_memory?.vram_bytes,vram)&&max(correctedRam(p.loaded_memory),ram);
  });
  const sortValue=(p:Profile)=>sort==='decode'?p.performance?.decode_tps:sort==='prefill'?p.performance?.prefill_tps:sort==='context'?p.settings.context:sort==='vram'?p.loaded_memory?.vram_bytes:correctedRam(p.loaded_memory);
  if(sort!=='saved')visible.sort((a,b)=>{
    if(sort==='model')return (models.find(m=>m.id===a.settings.model_id)?.name||a.name).localeCompare(models.find(m=>m.id===b.settings.model_id)?.name||b.name);
    const av=sortValue(a),bv=sortValue(b);return av==null?(bv==null?0:1):bv==null?-1:(av-bv)*(sort==='vram'||sort==='ram'?1:-1);
  });
  const reset=()=>{setQuery('');setModelFilter('');setContext('');setDecode('');setPrefill('');setVram('');setRam('');setSort('saved')};
  const [expanded,setExpanded]=useState<Set<string>>(new Set());
  const [dragged,setDragged]=useState<string|null>(null),[over,setOver]=useState<string|null>(null);
  const pointer=useRef<{id:string;x:number;y:number;moving:boolean;target:string|null}|null>(null);
  const move=(from:string,to:string)=>{
    if(from===to||busy||trash||!canReorder)return;
    const ids=profiles.map(p=>p.id),source=ids.indexOf(from),target=ids.indexOf(to);
    if(source<0||target<0)return;
    ids.splice(source,1);ids.splice(target,0,from);props.onReorder(ids);
  };
  const toggle=(id:string)=>setExpanded(old=>{const next=new Set(old);if(next.has(id))next.delete(id);else next.add(id);return next});
  const track=(e:PointerEvent<HTMLButtonElement>)=>{
    const current=pointer.current;if(!current)return;
    if(!current.moving&&Math.hypot(e.clientX-current.x,e.clientY-current.y)<5)return;
    current.moving=true;setDragged(current.id);
    current.target=document.elementFromPoint(e.clientX,e.clientY)?.closest<HTMLElement>('[data-profile-id]')?.dataset.profileId||null;
    setOver(current.target);
    const page=e.currentTarget.closest('main');if(page){const rect=page.getBoundingClientRect();if(e.clientY>rect.bottom-50)page.scrollBy(0,20);else if(e.clientY<rect.top+50)page.scrollBy(0,-20)}
  };
  const finish=(cancel=false)=>{const current=pointer.current;pointer.current=null;setDragged(null);setOver(null);if(!cancel&&current?.moving&&current.target)move(current.id,current.target)};
  return <>
    <div className="profile-filters">
      <label className="profile-search">Model / quant / profile<input aria-label="Find saved profiles" placeholder="e.g. Qwen 27B Q6" value={query} onChange={e=>setQuery(e.target.value)}/></label>
      <label className="profile-model-filter">Model &amp; quant<select value={modelFilter} onChange={e=>setModelFilter(e.target.value)}><option value="">All saved models</option>{savedModels.map(m=><option key={m.id} value={m.id}>{m.name} · {m.quant}</option>)}</select></label>
      <label>Sort by<select value={sort} onChange={e=>setSort(e.target.value)}><option value="saved">My saved order</option><option value="model">Model name</option><option value="decode">Fastest generation</option><option value="prefill">Fastest prefill</option><option value="context">Largest context</option><option value="vram">Least VRAM</option><option value="ram">Least total RAM</option></select></label>
      <label>Generation ≥ tok/s<input type="number" min="0" placeholder="Any" value={decode} onChange={e=>setDecode(e.target.value)}/></label>
      <label>Prefill ≥ tok/s<input type="number" min="0" placeholder="Any" value={prefill} onChange={e=>setPrefill(e.target.value)}/></label>
      <label>Context<select value={context} onChange={e=>setContext(e.target.value)}><option value="">Any context</option>{[...new Set(profiles.map(p=>p.settings.context))].sort((a,b)=>a-b).map(c=><option key={c} value={c}>{c/1024}K</option>)}</select></label>
      <label>VRAM ≤ GiB<input type="number" min="0" step="0.1" placeholder="Any" value={vram} onChange={e=>setVram(e.target.value)}/></label>
      <label>Total RAM ≤ GiB<input type="number" min="0" step="0.1" placeholder="Any" value={ram} onChange={e=>setRam(e.target.value)}/></label>
    </div>
    <div className="profile-filter-summary"><span>{visible.length} of {profiles.length} profiles · Benchmark data refreshes every 3 seconds</span>{!canReorder&&<button onClick={reset}>Reset filters &amp; saved order</button>}<span>{canReorder?'Drag to arrange':'Drag ordering paused while filtering or sorting'}</span></div>
    {!visible.length&&<div className="profile-no-matches">No profiles match these filters. Unknown measurements are excluded by speed and memory limits.<button className="secondary" onClick={reset}>Reset filters</button></div>}
    <div className="compact-profiles" aria-label={trash?'Deleted profiles':'Saved profile list'}>
    {visible.map(profile=>{
      const index=profiles.findIndex(p=>p.id===profile.id);
      const model=models.find(m=>m.id===profile.settings.model_id),open=expanded.has(profile.id),s=profile.settings;
      const results=profile.benchmark_results||[],related=profile.related_results||[];
      return <article key={profile.id} data-profile-id={profile.id} title={model?.name||profile.name} className={`compact-profile ${open?'expanded':''} ${props.editingId===profile.id?'selected':''} ${over===profile.id&&over!==dragged?'drop-target':''} ${dragged===profile.id?'dragging':''}`}>
        <div className="compact-profile-row">
          {!trash&&<button className="profile-drag icon-btn" title="Drag to reorder. Arrow keys also move this profile." aria-label={`Reorder ${profile.name}`} disabled={busy||!canReorder}
            onPointerDown={e=>{if(e.button!==0)return;e.currentTarget.setPointerCapture(e.pointerId);pointer.current={id:profile.id,x:e.clientX,y:e.clientY,moving:false,target:null}}}
            onPointerMove={track} onPointerUp={()=>finish()} onPointerCancel={()=>finish(true)}
            onKeyDown={e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();const target=profiles[index+(e.key==='ArrowUp'?-1:1)];if(target)move(profile.id,target.id)}}}><GripVertical size={18}/></button>}
          <button className="profile-expand" aria-expanded={open} aria-controls={`profile-${profile.id}`} onClick={()=>toggle(profile.id)}>
            <span className="profile-title">{modelLabel(model)}</span><span className="profile-quant">{model?.quant||'Unknown quant'} <small>{model?.format}</small></span>{(!profile.auto_name||profile.copy_number)&&<span className="profile-custom-name">{profile.auto_name?`Copy ${profile.copy_number}`:profile.name}</span>}
            {props.editingId===profile.id&&<span className="profile-subtitle">Editing this setup</span>}
          </button>
          <button className="icon-btn profile-chevron" aria-label={`${open?'Collapse':'Expand'} ${profile.name}`} aria-expanded={open} onClick={()=>toggle(profile.id)}><ChevronDown size={17}/></button>
        </div>
        <div className="profile-scan-stats">
          <span className="profile-scan-generation" title={`Generation · ${sourceLabel(profile.performance?.decode_source)}`}><span>Gen</span> <b>{formatNumber(profile.performance?.decode_tps)}</b><small>tok/s</small></span>
          <span className="profile-scan-prefill" title={`Prefill · ${sourceLabel(profile.performance?.prefill_source)}`}><span>Prefill</span> <b>{formatNumber(profile.performance?.prefill_tps,0)}</b><small>tok/s</small></span>
          <span title="Context window / KV cache precision"><b>{s.context/1024}K</b><small>/ {s.kv} KV</small></span>
          <span title={profile.loaded_memory?`Total system VRAM / occupied RAM, including cache · ${new Date(profile.loaded_memory.measured_at*1000).toLocaleString()}`:'Load this setup to record VRAM and occupied RAM, including cache'}><span>VRAM/RAM</span> <b>{gib(profile.loaded_memory?.vram_bytes)}/{gib(correctedRam(profile.loaded_memory))}</b><small>GiB{profile.loaded_memory&&correctedRam(profile.loaded_memory)==null?' · reload to measure RAM':''}</small></span>
        </div>
        <div className="profile-card-footer"><span className="profile-secondary-settings">Vision {s.vision?'on':'off'} · MTP {s.prediction==='mtp'?`on/${s.draft_tokens}`:'off'}{model?.ngram&&` · N-gram ${s.ngram_ram===false?'storage':'RAM'}`} · CPU {s.cpu_percent}% · Think: {reasoningLabel(s.reasoning_effort,model)}</span>          {!trash&&<button className="icon-btn" disabled={busy} title="Duplicate profile" aria-label={`Duplicate ${profile.name}`} onClick={()=>props.onDuplicate(profile)}><Copy size={16}/></button>}
          {trash?<button className="secondary" disabled={busy} onClick={()=>props.onRestore(profile)}><RotateCcw size={14}/>Restore</button>:
            <button className="primary profile-load" disabled={busy||!model} aria-label={`Load ${profile.name}`} onClick={()=>props.onLoad(profile)}><Play size={13}/>Load</button>}
        </div>
        {open&&<div className="compact-profile-details" id={`profile-${profile.id}`}>
          <p className="profile-measurement-detail">Generation: {sourceLabel(profile.performance?.decode_source)} · Prefill: {sourceLabel(profile.performance?.prefill_source)}<br/>{profile.loaded_memory?(correctedRam(profile.loaded_memory)!=null?`System totals after load · RAM includes ${gib(profile.loaded_memory.ram_cache_bytes)} GiB cache. Model resident: ${gib(profile.loaded_memory.model_memory?.resident_bytes)} GiB (included). Measured ${new Date(profile.loaded_memory.measured_at*1000).toLocaleString()}.`:'RAM needs a new measurement: the previous reading excluded reclaimable model pages. Load this profile to update it.'):'Load once to measure system VRAM, occupied RAM and model resident memory.'}</p>
          <div className="profile-detail-grid"><div><span>Context / cache</span><strong>{s.context/1024}K · {s.kv}</strong></div><div><span>Prediction</span><strong>{s.prediction==='mtp'?`MTP on · ${s.draft_tokens} draft tokens`:'MTP off'}</strong></div><div><span>Vision</span><strong>{s.vision?'On':'Off'}</strong></div>{model?.ngram&&<div><span>PLE n-gram table</span><strong>{s.ngram_ram===false?'Storage streaming':'System RAM'}</strong></div>}<div><span>CPU placement</span><strong>{s.cpu_percent}% · {s.cpu_threads} threads{s.engine==='gguf'||model?.format==='GGUF'?` · ${s.gguf_offload}`:''}</strong></div><div><span>Generation</span><strong>{s.max_output/1024}K output · temp {s.temperature}</strong></div><div><span>Thinking / chunk</span><strong>{reasoningLabel(s.reasoning_effort,model)} · {s.chunk_size} tokens</strong></div></div>
          {results.length>0&&<section className="profile-results"><h3>Benchmarks · matching settings</h3>{results.map(r=><Result key={r.id} result={r}/>)}</section>}
          {related.length>0&&<details className="related-benchmarks"><summary>{related.length} benchmark{related.length===1?'':'s'} for this model with different settings</summary><p>These results do not determine this profile’s speed. Open the benchmark archive to compare configurations.</p>{related.map(r=><Result key={r.id} result={r}/>)}</details>}
          <div className="profile-detail-actions">
            {!trash&&<><button className="secondary" disabled={busy||!model} onClick={()=>props.onEdit(profile)}><Settings2 size={14}/>Edit settings</button><button className="secondary" disabled={busy} onClick={()=>props.onRename(profile)}><Pencil size={14}/>Rename</button>
              <button className="icon-btn" disabled={busy||!canReorder||index===0} aria-label={`Move ${profile.name} up`} title="Move up" onClick={()=>move(profile.id,profiles[index-1].id)}><ArrowUp size={16}/></button><button className="icon-btn" disabled={busy||!canReorder||index===profiles.length-1} aria-label={`Move ${profile.name} down`} title="Move down" onClick={()=>move(profile.id,profiles[index+1].id)}><ArrowDown size={16}/></button></>}
            {(results.length>0||related.length>0)&&<button className="secondary" onClick={props.onBenchmarks}>Benchmark archive</button>}
            {!trash&&<button className="icon-btn profile-delete" disabled={busy} aria-label={`Delete ${profile.name}`} title="Move to Recently deleted" onClick={()=>props.onDelete(profile)}><Trash2 size={16}/></button>}
          </div>
          <details className="profile-json"><summary>Full configuration</summary><button className="copy-profile-config" onClick={()=>props.onCopy(profile)}>{props.copiedId===profile.id?<Check size={13}/>:<Copy size={13}/>} {props.copiedId===profile.id?'Copied':'Copy config'}</button><pre>{JSON.stringify({name:profile.name,settings:profile.settings},null,2)}</pre></details>
        </div>}
      </article>;
    })}
  </div></>;
}
