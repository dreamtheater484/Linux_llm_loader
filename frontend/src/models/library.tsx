import {useEffect, useMemo, useState} from 'react';
import type {ReactNode} from 'react';
import {AlertTriangle, Braces, Check, Compass, Copy, Cpu, Eye, FolderPlus, HardDrive, Layers, MessageSquare, Pencil, Play, Plus, RefreshCw, Search, Settings2, Sparkles, Trash2, Zap} from 'lucide-react';
import type {Model, Profile, Settings} from '../types';
import {Hint, formatNumber, hueStyle} from '../ui';
import {correctedRam} from '../memory';
import {reasoningLabel} from '../reasoning';
import {shortTokens} from './context-slider';
import {GPU_WORKING, assessFit, expertFraction, fitRank, gb, modelFit} from './fit';
import type {Fit, Hardware} from './fit';

export const engineName = (id?:string) => id==='gguf'?'llama.cpp':id==='exl3'?'ExLlamaV3':id==='vllm'?'vLLM':id||'Auto';
export const familyOf = (m:Model) => (m.family||m.title).replace(/[-_]+/g,' ');

type Row = {key:keyof Settings|'measure';label:string;value:(s:Settings,m:Model)=>string;short?:(s:Settings,m:Model)=>string;only?:(m:Model)=>boolean};
const ROWS:Row[] = [
  {key:'context',label:'Context window',value:s=>`${shortTokens(s.context)} tokens`,short:s=>`${shortTokens(s.context)} context`},
  {key:'kv',label:'KV cache precision',value:s=>s.kv,short:s=>`${s.kv} cache`},
  {key:'vision',label:'Vision tower',value:(s,m)=>m.vision?(s.vision?'On':'Off'):'Not available',short:s=>s.vision?'vision':'no vision'},
  {key:'prediction',label:'Prediction acceleration',value:(s,m)=>!m.mtp?'Not available':s.prediction==='mtp'?`MTP · ${s.draft_tokens} draft`:'Off',short:s=>s.prediction==='mtp'?`MTP ${s.draft_tokens}`:'no MTP'},
  {key:'draft_tokens',label:'',value:()=>'',only:()=>false},
  {key:'max_output',label:'Maximum output',value:s=>`${shortTokens(s.max_output)} tokens`,short:s=>`${shortTokens(s.max_output)} output`},
  {key:'reasoning_effort',label:'Thinking',value:(s,m)=>reasoningLabel(s.reasoning_effort,m),short:(s,m)=>`thinking ${reasoningLabel(s.reasoning_effort,m).toLowerCase()}`},
  {key:'cpu_percent',label:'CPU share',value:s=>`${s.cpu_percent}%`,short:s=>`CPU ${s.cpu_percent}%`},
  {key:'gguf_offload',label:'CPU placement',value:s=>s.gguf_offload==='experts'?'Experts':'Whole layers',short:s=>s.gguf_offload==='experts'?'expert offload':'layer offload',only:m=>m.format==='GGUF'&&!!m.experts},
  {key:'ngram_ram',label:'PLE n-gram table',value:s=>s.ngram_ram?'In RAM':'From disk',short:s=>s.ngram_ram?'n-gram in RAM':'n-gram on disk',only:m=>!!m.ngram},
  {key:'chunk_size',label:'Prompt chunk size',value:s=>`${s.chunk_size}`,short:s=>`chunk ${s.chunk_size}`},
  {key:'temperature',label:'Temperature',value:s=>s.temperature.toFixed(1),short:s=>`temp ${s.temperature.toFixed(1)}`},
  {key:'cpu_threads',label:'CPU threads',value:s=>`${s.cpu_threads}`,short:s=>`${s.cpu_threads} threads`},
  {key:'engine',label:'Engine',value:(s,m)=>engineName(s.engine==='auto'?m.engines[0]:s.engine),short:(s,m)=>engineName(s.engine==='auto'?m.engines[0]:s.engine)},
];
const rowsFor = (m:Model) => ROWS.filter(r=>r.label&&(!r.only||r.only(m)));
const same = (a:Settings,b:Settings,key:keyof Settings) => key==='prediction'?(a.prediction===b.prediction&&(a.prediction!=='mtp'||a.draft_tokens===b.draft_tokens)):key==='engine'?true:a[key]===b[key];

/** What makes this setup different from Inflect's defaults, in a few words. */
export function differences(settings:Settings, model:Model, base:Settings) {
  const changed=rowsFor(model).filter(r=>r.short&&!same(settings,base,r.key as keyof Settings)&&!(r.key==='engine'&&settings.engine===base.engine));
  return changed.map(r=>r.short!(settings,model));
}
export function setupName(profile:Profile, model:Model|undefined, base:Settings|null) {
  if(!profile.auto_name)return profile.name;
  const diff=model&&base?differences(profile.settings,model,base):[];
  return (diff.length?diff.slice(0,3).join(' · '):'Default settings')+(profile.copy_number?` · copy ${profile.copy_number}`:'');
}

export function FitDot({fit}:{fit:Fit}) {return <span className={`fit-dot ${fit.level}`} title={`${fit.label} · ${fit.detail}`}/>}

function MemoryBar({label,icon:Icon,weights,reserve=0,total,warn}:{label:string;icon:any;weights:number;reserve?:number;total:number|null;warn?:boolean}) {
  const pct=(n:number)=>total?Math.min(100,n/total*100):0;
  return <div className={`memory-plan-bar ${warn?'warn':''}`}><div><Icon size={14}/><span>{label}</span><b>{gb(weights)}</b><small>of {gb(total)}</small></div><span className="plan-track" title={reserve?`Weights ${gb(weights)} · kept free for the conversation and engine ${gb(reserve)}`:undefined}><i style={{width:`${pct(weights)}%`}}/><i className="reserve" style={{width:`${pct(reserve)}%`}}/></span>{reserve>0&&<small>Plus about {gb(reserve)} kept free for the conversation</small>}</div>;
}

export function FitCard({fit,hw,note}:{fit:Fit;hw:Hardware;note?:string}) {
  return <div className={`fit-card ${fit.level}`}>
    <div className="fit-head"><FitDot fit={fit}/><strong>{fit.label}</strong><span>{fit.detail}</span></div>
    {fit.level!=='unknown'&&<div className="fit-bars"><MemoryBar icon={Layers} label="Graphics memory" weights={fit.gpu} reserve={Math.min(GPU_WORKING,Math.max(0,(hw.vram||0)-fit.gpu))} total={hw.vram} warn={fit.level==='no'}/><MemoryBar icon={HardDrive} label="System memory" weights={fit.cpu} total={hw.ram} warn={fit.level==='no'}/></div>}
    <small>{note||'Estimate for the weights plus room for the conversation. After a load, Inflect shows what the model really used.'}</small>
  </div>;
}

type Props = {
  models:Model[]; profiles:Profile[]; hardware:Hardware; session:any; ready:boolean; busy:boolean;
  focusId:string|null; onFocus:(id:string)=>void; defaults:(m:Model)=>Settings; locations:string[];
  onChat:(m:Model,profile?:Profile)=>void; onConfigure:(m:Model,profile?:Profile)=>void;
  onRename:(p:Profile)=>void; onDuplicate:(p:Profile)=>void; onDelete:(p:Profile)=>void; onCopy:(p:Profile)=>void; copiedId:string|null;
  onAdd:()=>void; onDiscover:()=>void; onRefresh:()=>void;
  tab:'library'|'profiles'|'deleted'; onTab:(tab:'library'|'profiles'|'deleted')=>void; deletedCount:number; allProfiles:ReactNode;
};
type Filter = 'all'|'fits'|'GGUF'|'EXL3'|'profiles';

export function ModelsPage(props:Props) {
  const {models,profiles,hardware:hw,session,ready}=props;
  const [query,setQuery]=useState(''),[filter,setFilter]=useState<Filter>('all'),[onlyDiff,setOnlyDiff]=useState(false);
  const fits=useMemo(()=>new Map(models.map(m=>[m.id,modelFit(m,hw)])),[models,hw.vram,hw.ram]);
  const profileCount=(id:string)=>profiles.filter(p=>p.settings.model_id===id).length;
  const norm=(t:string)=>t.toLowerCase().replace(/[-_]+/g,' ');
  const matches=(m:Model)=>norm(`${m.name} ${m.title} ${m.family} ${m.variant} ${m.quant} ${m.format}`).includes(norm(query).trim())&&(
    filter==='all'||(filter==='fits'?fitRank[fits.get(m.id)!.level]<=1:filter==='profiles'?profileCount(m.id)>0:m.format===filter));
  const groups=useMemo(()=>{
    const map=new Map<string,Model[]>();
    for(const m of models.filter(matches)){const key=familyOf(m).toLowerCase().replace(/(^|\s)([a-z]+) (\d+(?:\.\d+)*)(?=\s|$)/g,'$1$2$3');map.set(key,[...(map.get(key)||[]),m])}
    return [...map.values()].map(list=>list.sort((a,b)=>b.bytes-a.bytes)).sort((a,b)=>{
      const pa=a.some(m=>profileCount(m.id)),pb=b.some(m=>profileCount(m.id));
      return pa!==pb?(pa?-1:1):familyOf(a[0]).localeCompare(familyOf(b[0]));
    });
  },[models,profiles,query,filter,fits]);
  const model=models.find(m=>m.id===props.focusId)||groups[0]?.[0]||models[0];
  useEffect(()=>{if(model&&model.id!==props.focusId)props.onFocus(model.id)},[model?.id]);
  const counts:Record<Filter,number>={all:models.length,fits:models.filter(m=>fitRank[fits.get(m.id)!.level]<=1).length,GGUF:models.filter(m=>m.format==='GGUF').length,EXL3:models.filter(m=>m.format==='EXL3').length,profiles:models.filter(m=>profileCount(m.id)).length};
  const loadedId=ready?session?.model?.id:null;

  return <main className="page models-page">
    <div className="page-heading"><div><h1>Models</h1><p>Everything on this computer. Pick a model to compare its saved setups, tune it and start a chat.</p></div>
      <div className="page-actions"><button className="secondary" onClick={props.onAdd}><FolderPlus size={15}/>Add from this computer</button><button className="primary" onClick={props.onDiscover}><Compass size={15}/>Discover models</button></div></div>
    <div className="profile-toolbar models-tabs" role="tablist">
      <button role="tab" aria-selected={props.tab==='library'} className={props.tab==='library'?'chosen':''} onClick={()=>props.onTab('library')}>Library <span>{models.length}</span></button>
      <button role="tab" aria-selected={props.tab==='profiles'} className={props.tab==='profiles'?'chosen':''} onClick={()=>props.onTab('profiles')}>All saved setups <span>{profiles.length}</span></button>
      <button role="tab" aria-selected={props.tab==='deleted'} className={props.tab==='deleted'?'chosen':''} onClick={()=>props.onTab('deleted')}><Trash2 size={13}/>Recently deleted <span>{props.deletedCount}</span></button>
    </div>
    {props.tab!=='library'?props.allProfiles:<div className="models-layout">
      <aside className="models-list" aria-label="Your models">
        <label className="models-search"><Search size={15}/><input aria-label="Search models" placeholder="Find a model, quant or format…" value={query} onChange={e=>setQuery(e.target.value)}/><kbd>/</kbd></label>
        <div className="models-filters" role="group" aria-label="Filter models">{([['all','All'],['fits','Runs well here'],['profiles','With setups'],['GGUF','GGUF'],['EXL3','EXL3']] as [Filter,string][]).filter(([id])=>id==='all'||counts[id]>0).map(([id,label])=><button key={id} aria-pressed={filter===id} className={filter===id?'chosen':''} onClick={()=>setFilter(id)}>{label}<span>{counts[id]}</span></button>)}</div>
        <div className="models-groups">
          {groups.map(list=>{const first=list[0];return <section key={familyOf(first)} className="model-group" style={hueStyle(familyOf(first))}>
            <header><span className="model-symbol">{familyOf(first).charAt(0).toUpperCase()}</span><strong title={familyOf(first)}>{familyOf(first)}</strong><small>{list.length===1?'1 version':`${list.length} versions`}</small></header>
            {list.map(m=>{const fit=fits.get(m.id)!,count=profileCount(m.id);return <button key={m.id} className={`variant-row ${model?.id===m.id?'selected':''}`} aria-current={model?.id===m.id?'true':undefined} onClick={()=>props.onFocus(m.id)}>
              <span className="variant-main"><b>{m.quant}</b><span className={`format-tag ${m.format.toLowerCase()}`}>{m.format}</span>{m.variant&&<span className="variant-name" title={m.variant}>{m.variant}</span>}</span>
              <span className="variant-meta"><FitDot fit={fit}/>{gb(m.bytes)}{m.vision&&<Eye size={12} aria-label="Vision"/>}{count>0&&<span className="setup-count" title={`${count} saved setup${count===1?'':'s'}`}>{count} setup{count===1?'':'s'}</span>}{m.issues.length>0&&<AlertTriangle size={12} className="issue-icon" aria-label="Incomplete"/>}{loadedId===m.id&&<span className="loaded-pill">Loaded</span>}</span>
            </button>})}
          </section>})}
          {!groups.length&&<div className="models-empty"><p>{models.length?'No models match. Try another search or filter.':'No models found yet. Add a folder from this computer or discover a model to download.'}</p>{!models.length&&<button className="primary" onClick={props.onDiscover}><Compass size={15}/>Discover models</button>}</div>}
        </div>
        <footer className="models-list-footer"><button className="icon-link" onClick={props.onRefresh}><RefreshCw size={13}/>Rescan folders</button>{props.locations.length>0&&<span title={props.locations.join('\n')}>+ {props.locations.length} added location{props.locations.length===1?'':'s'}</span>}</footer>
      </aside>
      {model?<ModelDetail key={model.id} {...props} model={model} fit={fits.get(model.id)!} siblings={models.filter(m=>familyOf(m).toLowerCase()===familyOf(model).toLowerCase()&&m.id!==model.id)} loaded={loadedId===model.id} onlyDiff={onlyDiff} setOnlyDiff={setOnlyDiff}/>:<section className="model-detail empty"><Sparkles size={30}/><h2>Your library is empty</h2><p>Discover a model to download, or add a folder that already contains models.</p></section>}
    </div>}
  </main>;
}

function ModelDetail(props:Props&{model:Model;fit:Fit;siblings:Model[];loaded:boolean;onlyDiff:boolean;setOnlyDiff:(v:boolean)=>void}) {
  const {model,fit,hardware:hw,loaded,busy}=props;
  const base=props.defaults(model);
  const setups=props.profiles.filter(p=>p.settings.model_id===model.id);
  const columns:{id:string;title:string;subtitle?:string;settings:Settings;profile?:Profile}[]=[{id:'default',title:'Inflect defaults',subtitle:'Not saved · starting point',settings:base},...setups.map((p,i)=>({id:p.id,title:setupName(p,model,base),subtitle:p.auto_name?`Setup ${i+1}`:differences(p.settings,model,base).slice(0,3).join(' · ')||'Same as defaults',settings:p.settings,profile:p}))];
  const rows=rowsFor(model);
  const varies=(r:Row)=>columns.some(c=>!same(c.settings,columns[0].settings,r.key as keyof Settings)||(r.key==='engine'&&c.settings.engine!==columns[0].settings.engine));
  const shown=props.onlyDiff&&setups.length?rows.filter(varies):rows;
  const best=(pick:(p:Profile)=>number|null|undefined,low=false)=>{const values=setups.map(pick).filter((v):v is number=>v!=null&&Number.isFinite(v));return values.length>1?(low?Math.min(...values):Math.max(...values)):null};
  const bestDecode=best(p=>p.performance?.decode_tps),bestPrefill=best(p=>p.performance?.prefill_tps);
  const moeNote=model.experts?`Mixture of experts · ${model.experts} experts${model.active_b?` · ${model.active_b}B active`:''}`:'Dense model';
  return <section className="model-detail" style={hueStyle(familyOf(model))}>
    <header className="model-hero">
      <span className="model-symbol big">{familyOf(model).charAt(0).toUpperCase()}</span>
      <div className="model-hero-copy"><h2>{familyOf(model)}</h2><p>{[model.variant,model.quant,model.format,gb(model.bytes)].filter(Boolean).join(' · ')}</p>
        <div className="fact-chips">
          <span className="fact"><Cpu size={12}/>Runs with {engineName(model.engines[0])}</span>
          <span className="fact" title={model.experts?'Only a few experts work on each word, so most of the model can wait in system RAM.':'Every weight is used for every word.'}>{moeNote}</span>
          <span className="fact">Up to {shortTokens(model.context)} context</span>
          {model.vision&&<span className="fact good"><Eye size={12}/>Understands images</span>}
          {model.mtp&&<span className="fact good"><Zap size={12}/>MTP prediction</span>}
          {model.source&&<span className="fact" title={`Downloaded by Inflect from huggingface.co/${model.source}`}>From {model.source}</span>}
          {model.location==='added'&&<span className="fact" title={model.path}>Added location</span>}
        </div></div>
      <div className="model-hero-actions">
        <button className="primary" disabled={busy||!!model.issues.length} onClick={()=>props.onChat(model)}>{loaded?<><MessageSquare size={15}/>Open chat</>:<><Play size={15}/>Load &amp; chat</>}</button>
        <button className="secondary" disabled={busy} onClick={()=>props.onConfigure(model)}><Settings2 size={15}/>Adjust settings</button>
        {loaded&&<span className="loaded-note"><Check size={13}/>Loaded now</span>}
      </div>
    </header>
    {model.issues.length>0&&<div className="model-issues" role="alert"><AlertTriangle size={16}/><span><strong>This model is incomplete and cannot load.</strong> {model.issues.slice(0,3).join(' · ')}</span></div>}
    <FitCard fit={fit} hw={hw}/>
    {props.siblings.length>0&&<div className="sibling-versions"><span>Other versions on this computer</span>{props.siblings.map(m=>{const f=assessFit(m.bytes,expertFraction(m),hw);return <button key={m.id} onClick={()=>props.onFocus(m.id)} title={`${m.name} · ${f.label}`}><FitDot fit={f}/><b>{m.quant}</b><small>{m.format}{m.variant?` · ${m.variant}`:''} · {gb(m.bytes)}</small></button>})}</div>}
    <section className="setup-compare">
      <header><div><h3>Saved setups <span>{setups.length}</span><Hint label="saved setups" text="A setup (profile) remembers this model together with its settings. Each column is one setup; values that differ from Inflect’s defaults are highlighted."/></h3><p>{setups.length?'Compare side by side. Highlighted values differ from the defaults.':'No saved setups yet. Adjust the settings, then choose Save as profile to keep them.'}</p></div>
        <div className="setup-compare-actions">{setups.length>0&&<label className="only-diff"><input type="checkbox" checked={props.onlyDiff} onChange={e=>props.setOnlyDiff(e.target.checked)}/>Only differences</label>}<button className="secondary" disabled={busy} onClick={()=>props.onConfigure(model)}><Plus size={14}/>New setup</button></div></header>
      <div className="compare-scroll"><table className="compare-table">
        <thead><tr><th scope="col"><span className="sr-only">Setting</span></th>{columns.map(c=><th scope="col" key={c.id} className={c.profile?'':'default-col'}>
          <div className="compare-head"><strong title={c.profile?.name||c.title}>{c.title}</strong>{c.subtitle&&<small>{c.subtitle}</small>}
            <div className="compare-actions">{c.profile?<><button className="primary small" disabled={busy||!!model.issues.length} onClick={()=>props.onChat(model,c.profile)} aria-label={`Load ${c.profile.name}`}><Play size={12}/>Load</button><button className="icon-btn" disabled={busy} title="Edit settings" aria-label={`Edit ${c.profile.name}`} onClick={()=>props.onConfigure(model,c.profile)}><Settings2 size={14}/></button><button className="icon-btn" disabled={busy} title="Rename" aria-label={`Rename ${c.profile.name}`} onClick={()=>props.onRename(c.profile!)}><Pencil size={14}/></button><button className="icon-btn" disabled={busy} title="Duplicate" aria-label={`Duplicate ${c.profile.name}`} onClick={()=>props.onDuplicate(c.profile!)}><Copy size={14}/></button><button className="icon-btn" title={props.copiedId===c.profile.id?'Copied':'Copy configuration'} aria-label={`Copy configuration of ${c.profile.name}`} onClick={()=>props.onCopy(c.profile!)}>{props.copiedId===c.profile.id?<Check size={14}/>:<Braces size={14}/>}</button><button className="icon-btn danger" disabled={busy} title="Move to Recently deleted" aria-label={`Delete ${c.profile.name}`} onClick={()=>props.onDelete(c.profile!)}><Trash2 size={14}/></button></>:
              <button className="secondary small" disabled={busy||!!model.issues.length} onClick={()=>props.onChat(model)}><Play size={12}/>Load defaults</button>}</div></div></th>)}</tr></thead>
        <tbody>
          {shown.map(r=><tr key={r.key}><th scope="row">{r.label}</th>{columns.map((c,i)=>{const changed=i>0&&(!same(c.settings,base,r.key as keyof Settings)||(r.key==='engine'&&c.settings.engine!==base.engine));return <td key={c.id} className={changed?'changed':''}>{r.value(c.settings,model)}</td>})}</tr>)}
          {setups.length>0&&<>
            <tr className="measure-heading"><th scope="rowgroup" colSpan={columns.length+1}>Measured on this computer</th></tr>
            <tr><th scope="row">Writing speed</th><td className="muted">—</td>{setups.map(p=><td key={p.id} className={p.performance?.decode_tps!=null&&p.performance.decode_tps===bestDecode?'best':''}>{p.performance?.decode_tps!=null?<>{formatNumber(p.performance.decode_tps)} <small>tok/s</small></>:<span className="muted" title="Run a speed benchmark with this setup to measure it">Not measured</span>}</td>)}</tr>
            <tr><th scope="row">Reading speed</th><td className="muted">—</td>{setups.map(p=><td key={p.id} className={p.performance?.prefill_tps!=null&&p.performance.prefill_tps===bestPrefill?'best':''}>{p.performance?.prefill_tps!=null?<>{formatNumber(p.performance.prefill_tps,0)} <small>tok/s</small></>:<span className="muted">Not measured</span>}</td>)}</tr>
            <tr><th scope="row">GPU / RAM after load</th><td className="muted">—</td>{setups.map(p=><td key={p.id}>{p.loaded_memory?<>{gb(p.loaded_memory.vram_bytes)} / {gb(correctedRam(p.loaded_memory))}</>:<span className="muted" title="Load this setup once to record its memory use">Load to measure</span>}</td>)}</tr>
          </>}
        </tbody>
      </table></div>
    </section>
  </section>;
}
