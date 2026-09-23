import {useEffect, useMemo, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {AlertTriangle, ArrowUpDown, Braces, Check, LoaderCircle, Compass, Copy, Cpu, Eye, FolderPlus, GripVertical, HardDrive, Layers, MessageSquare, Pencil, Play, Plus, RefreshCw, Search, Settings2, Sparkles, Trash2, Zap} from 'lucide-react';
import type {Model, Profile, Settings} from '../types';
import {Hint, formatNumber, hueStyle} from '../ui';
import {correctedRam} from '../memory';
import {reasoningLabel} from '../reasoning';
import {shortTokens} from './context-slider';
import {sameSettings, type LiveSetup} from '../profiles';
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
  {key:'max_output',label:'Maximum output',value:s=>s.max_output?`${shortTokens(s.max_output)} tokens`:'Auto',short:s=>s.max_output?`${shortTokens(s.max_output)} output`:'auto output'},
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
  models:Model[]; profiles:Profile[]; hardware:Hardware; session:any; ready:boolean; busy:boolean; live:LiveSetup|null;
  focusId:string|null; onFocus:(id:string)=>void; defaults:(m:Model)=>Settings; locations:string[];
  onChat:(m:Model,profile?:Profile)=>void; onConfigure:(m:Model,profile?:Profile)=>void;
  onRename:(p:Profile)=>void; onDuplicate:(p:Profile)=>void; onDelete:(p:Profile)=>void; onCopy:(p:Profile)=>void; copiedId:string|null;
  onAdd:()=>void; onDiscover:()=>void; onRefresh:()=>void; request:(path:string,body?:unknown,method?:string)=>Promise<any>;
  tab:'library'|'profiles'|'deleted'; onTab:(tab:'library'|'profiles'|'deleted')=>void; deletedCount:number; allProfiles:ReactNode;
};
type Filter = 'all'|'fits'|'GGUF'|'EXL3'|'profiles';
type Sort = 'custom'|'name'|'size'|'fit'|'recent';
const SORTS:[Sort,string][] = [['custom','Your order'],['recent','Recently used'],['name','Name'],['size','Largest first'],['fit','Runs best here']];
/** Stable key for a model family, e.g. "Qwen 3.5 0.8B" and "Qwen3.5 0.8B" share one. */
export const familyKey = (m:Model) => familyOf(m).toLowerCase().replace(/(^|\s)([a-z]+) (\d+(?:\.\d+)*)(?=\s|$)/g,'$1$2$3');
type Family = {key:string;name:string;models:Model[];formats:string[];small:number;large:number;best:number;setups:number;used:number};

export function ModelsPage(props:Props) {
  const {models,profiles,hardware:hw,session,ready}=props;
  const [query,setQuery]=useState(''),[filter,setFilter]=useState<Filter>('all'),[onlyDiff,setOnlyDiff]=useState(false);
  const [sort,setSort]=useState<Sort>('custom'),[order,setOrder]=useState<string[]>([]),[drag,setDrag]=useState<{key:string;over?:string;after?:boolean}|null>(null),[announce,setAnnounce]=useState('');
  const picks=useRef(new Map<string,string>());
  useEffect(()=>{props.request('/api/library/order').then(d=>{setOrder(d.order);setSort(d.sort)}).catch(()=>{})},[]);
  const persist=(nextOrder:string[],nextSort:Sort)=>{setOrder(nextOrder);setSort(nextSort);props.request('/api/library/order',{order:nextOrder,sort:nextSort},'PUT').catch(()=>{})};
  const fits=useMemo(()=>new Map(models.map(m=>[m.id,modelFit(m,hw)])),[models,hw.vram,hw.ram]);
  const profileCount=(id:string)=>profiles.filter(p=>p.settings.model_id===id).length;
  const loadedId=ready?session?.model?.id:null;
  const norm=(t:string)=>t.toLowerCase().replace(/[-_]+/g,' ');
  const matches=(m:Model)=>norm(`${m.name} ${m.title} ${m.family} ${m.variant} ${m.quant} ${m.format}`).includes(norm(query).trim())&&(
    filter==='all'||(filter==='fits'?fitRank[fits.get(m.id)!.level]<=1:filter==='profiles'?profileCount(m.id)>0:m.format===filter));
  const families=useMemo(()=>{
    const map=new Map<string,Model[]>();
    for(const m of models)map.set(familyKey(m),[...(map.get(familyKey(m))||[]),m]);
    return [...map].map(([key,list]):Family=>{list.sort((a,b)=>b.bytes-a.bytes);const used=profiles.filter(p=>list.some(m=>m.id===p.settings.model_id)).map(p=>Math.max(p.loaded_memory?.measured_at||0,p.updated_at||0));
      return {key,name:familyOf(list[0]),models:list,formats:[...new Set(list.map(m=>m.format))],small:list[list.length-1].bytes,large:list[0].bytes,
        best:Math.min(...list.map(m=>fitRank[fits.get(m.id)!.level])),setups:list.reduce((n,m)=>n+profileCount(m.id),0),used:list.some(m=>m.id===loadedId)?Infinity:Math.max(0,...used)}});
  },[models,profiles,fits,loadedId]);
  const byName=(a:Family,b:Family)=>a.name.localeCompare(b.name,undefined,{numeric:true,sensitivity:'base'});
  const sorted=useMemo(()=>{const list=[...families];const rank=new Map(order.map((k,i)=>[k,i]));
    const compare:Record<Sort,(a:Family,b:Family)=>number>={
      custom:(a,b)=>!order.length?((b.setups>0?1:0)-(a.setups>0?1:0)||byName(a,b)):((rank.get(a.key)??-1)-(rank.get(b.key)??-1)||byName(a,b)),
      name:byName,size:(a,b)=>b.large-a.large||byName(a,b),fit:(a,b)=>a.best-b.best||b.large-a.large,recent:(a,b)=>b.used-a.used||byName(a,b)};
    return list.sort(compare[sort]);
  },[families,order,sort]);
  const visible=sorted.filter(f=>f.models.some(matches));
  const model=models.find(m=>m.id===props.focusId)||visible[0]?.models[0]||models[0];
  const current=model?familyKey(model):null;
  useEffect(()=>{if(model){picks.current.set(familyKey(model),model.id);if(model.id!==props.focusId)props.onFocus(model.id)}},[model?.id]);
  const open=(f:Family)=>{const remembered=f.models.find(m=>m.id===picks.current.get(f.key));
    props.onFocus((remembered||f.models.find(m=>m.id===loadedId)||[...f.models].sort((a,b)=>profileCount(b.id)-profileCount(a.id)||fitRank[fits.get(a.id)!.level]-fitRank[fits.get(b.id)!.level])[0]).id)};
  /** Moving a model always switches to "Your order", starting from what is on screen. */
  const move=(key:string,target:string,after:boolean)=>{if(key===target)return;
    const base=sorted.map(f=>f.key).filter(k=>k!==key);const index=base.indexOf(target)+(after?1:0);base.splice(index,0,key);persist(base,'custom');
    setAnnounce(`${families.find(f=>f.key===key)?.name} moved to position ${index+1}`)};
  const nudge=(f:Family,step:number)=>{const i=visible.indexOf(f),target=visible[i+step];if(target)move(f.key,target.key,step>0);requestAnimationFrame(()=>document.querySelector<HTMLElement>(`[data-family="${CSS.escape(f.key)}"] .family-open`)?.focus())};
  const counts:Record<Filter,number>={all:models.length,fits:models.filter(m=>fitRank[fits.get(m.id)!.level]<=1).length,GGUF:models.filter(m=>m.format==='GGUF').length,EXL3:models.filter(m=>m.format==='EXL3').length,profiles:models.filter(m=>profileCount(m.id)).length};
  const size=(f:Family)=>f.models.length===1?gb(f.large):`${gb(f.small).replace(' GiB','')}–${gb(f.large)}`;

  return <main className="page models-page">
    <div className="page-heading"><div><h1>Models</h1><p>Everything on this computer. Pick a model to compare its saved setups, tune it and start a chat.</p></div>
      <div className="page-actions"><button className="secondary" onClick={props.onAdd}><FolderPlus size={15}/>Add from this computer</button><button className="primary" onClick={props.onDiscover}><Compass size={15}/>Discover models</button></div></div>
    <div className="profile-toolbar models-tabs" role="tablist">
      <button role="tab" aria-selected={props.tab==='library'} className={props.tab==='library'?'chosen':''} onClick={()=>props.onTab('library')}>Library <span>{families.length}</span></button>
      <button role="tab" aria-selected={props.tab==='profiles'} className={props.tab==='profiles'?'chosen':''} onClick={()=>props.onTab('profiles')}>All saved setups <span>{profiles.length}</span></button>
      <button role="tab" aria-selected={props.tab==='deleted'} className={props.tab==='deleted'?'chosen':''} onClick={()=>props.onTab('deleted')}><Trash2 size={13}/>Recently deleted <span>{props.deletedCount}</span></button>
    </div>
    {props.tab!=='library'?props.allProfiles:<div className="models-layout">
      <aside className="models-list" aria-label="Your models">
        <label className="models-search"><Search size={15}/><input aria-label="Search models" placeholder="Find a model, quant or format…" value={query} onChange={e=>setQuery(e.target.value)}/><kbd>/</kbd></label>
        <div className="models-list-tools">
          <label><span className="sr-only">Show</span><select aria-label="Show" value={filter} onChange={e=>setFilter(e.target.value as Filter)}>{([['all','All models'],['fits','Runs well here'],['profiles','With saved setups'],['GGUF','GGUF only'],['EXL3','EXL3 only']] as [Filter,string][]).filter(([id])=>id==='all'||counts[id]>0).map(([id,label])=><option key={id} value={id}>{label} ({counts[id]})</option>)}</select></label>
          <label><ArrowUpDown size={13}/><span className="sr-only">Sort</span><select aria-label="Sort models" value={sort} onChange={e=>persist(e.target.value==='custom'&&!order.length?sorted.map(f=>f.key):order,e.target.value as Sort)}>{SORTS.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
        </div>
        <ul className="family-list" onDragEnd={()=>setDrag(null)}>
          {visible.map(f=>{const selected=f.key===current,loaded=f.models.some(m=>m.id===loadedId),issue=f.models.every(m=>m.issues.length>0);
            const over=drag?.over===f.key&&drag.key!==f.key?(drag.after?'drop-after':'drop-before'):'';
            return <li key={f.key} data-family={f.key} className={`family-row ${selected?'selected':''} ${drag?.key===f.key?'dragging':''} ${over}`} draggable={!query}
              onDragStart={e=>{e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',f.name);setDrag({key:f.key})}}
              onDragOver={e=>{if(!drag)return;e.preventDefault();const r=e.currentTarget.getBoundingClientRect();const after=e.clientY>r.top+r.height/2;if(drag.over!==f.key||drag.after!==after)setDrag({...drag,over:f.key,after})}}
              onDrop={e=>{e.preventDefault();if(drag)move(drag.key,f.key,!!drag.after);setDrag(null)}}>
              <span className="family-grip" aria-hidden="true" title={query?'Clear the search to reorder':'Drag to reorder'}><GripVertical size={14}/></span>
              <button className="family-open" aria-current={selected?'true':undefined} onClick={()=>open(f)}
                onKeyDown={e=>{if(e.altKey&&(e.key==='ArrowUp'||e.key==='ArrowDown')){e.preventDefault();nudge(f,e.key==='ArrowUp'?-1:1)}}}
>
                <strong>{f.name}</strong>
                <small>{f.models.length===1?[f.models[0].quant,f.models[0].format].filter(Boolean).join(' · '):`${f.models.length} versions · ${f.formats.join(' · ')}`} · {size(f)}{issue?' · incomplete':''}</small>
              </button>
              {loaded&&<span className="family-loaded" title="Loaded now">Loaded</span>}
            </li>})}
        </ul>
        {!visible.length&&<div className="models-empty"><p>{models.length?'No models match. Try another search or filter.':'No models found yet. Add a folder from this computer or discover a model to download.'}</p>{!models.length&&<button className="primary" onClick={props.onDiscover}><Compass size={15}/>Discover models</button>}</div>}
        <p className="sr-only" aria-live="polite">{announce}</p>
        <footer className="models-list-footer"><button className="icon-link" onClick={props.onRefresh}><RefreshCw size={13}/>Rescan folders</button>{visible.length>1&&!query&&<span title="Or focus a model and press Alt+↑ / Alt+↓">Drag to reorder</span>}{props.locations.length>0&&<span title={props.locations.join('\n')}>+ {props.locations.length} added location{props.locations.length===1?'':'s'}</span>}</footer>
      </aside>
      {model?<ModelDetail key={model.id} {...props} model={model} fit={fits.get(model.id)!} versions={families.find(f=>f.key===current)?.models||[model]} fits={fits} profileCount={profileCount} loaded={loadedId===model.id} onlyDiff={onlyDiff} setOnlyDiff={setOnlyDiff}/>:<section className="model-detail empty"><Sparkles size={30}/><h2>Your library is empty</h2><p>Discover a model to download, or add a folder that already contains models.</p></section>}
    </div>}
  </main>;
}

function ModelDetail(props:Props&{model:Model;fit:Fit;versions:Model[];fits:Map<string,Fit>;profileCount:(id:string)=>number;loaded:boolean;onlyDiff:boolean;setOnlyDiff:(v:boolean)=>void}) {
  const {model,fit,hardware:hw,loaded,busy}=props;
  const base=props.defaults(model);
  const setups=props.profiles.filter(p=>p.settings.model_id===model.id);
  const columns:{id:string;title:string;subtitle?:string;settings:Settings;profile?:Profile}[]=[{id:'default',title:'Inflect defaults',subtitle:'Not saved · starting point',settings:base},...setups.map((p,i)=>({id:p.id,title:setupName(p,model,base),subtitle:p.auto_name?`Setup ${i+1}`:differences(p.settings,model,base).slice(0,3).join(' · ')||'Same as defaults',settings:p.settings,profile:p}))];
  const rows=rowsFor(model);
  const liveState=(c:{settings:Settings;profile?:Profile})=>!props.live?null:(c.profile?props.live.profileId===c.profile.id:!props.live.profileId&&sameSettings(c.settings,props.live.settings))?props.live.state:null;
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
    {props.versions.length>1&&<div className="version-picker" role="group" aria-label="Versions on this computer"><span>Versions on this computer <b>{props.versions.length}</b></span><div>{props.versions.map(m=>{const f=props.fits.get(m.id)!,count=props.profileCount(m.id),chosen=m.id===model.id;
      return <button key={m.id} className={chosen?'chosen':''} aria-pressed={chosen} onClick={()=>props.onFocus(m.id)} title={`${m.name} · ${f.label}`}>
        <span className="version-top"><FitDot fit={f}/><b>{m.quant}</b><small>{m.format}</small></span>
        <small>{[m.variant,gb(m.bytes),count?`${count} setup${count===1?'':'s'}`:''].filter(Boolean).join(' · ')}</small></button>})}</div></div>}
    {model.issues.length>0&&<div className="model-issues" role="alert"><AlertTriangle size={16}/><span><strong>This model is incomplete and cannot load.</strong> {model.issues.slice(0,3).join(' · ')}</span></div>}
    <FitCard fit={fit} hw={hw}/>
    <section className="setup-compare">
      <header><div><h3>Saved setups <span>{setups.length}</span><Hint label="saved setups" text="A setup (profile) remembers this model together with its settings. Each column is one setup; values that differ from Inflect’s defaults are highlighted."/></h3><p>{setups.length?'Compare side by side. Highlighted values differ from the defaults.':'No saved setups yet. Adjust the settings, then choose Save as profile to keep them.'}</p></div>
        <div className="setup-compare-actions">{setups.length>0&&<label className="only-diff"><input type="checkbox" checked={props.onlyDiff} onChange={e=>props.setOnlyDiff(e.target.checked)}/>Only differences</label>}<button className="secondary" disabled={busy} onClick={()=>props.onConfigure(model)}><Plus size={14}/>New setup</button></div></header>
      <div className="compare-scroll"><table className="compare-table">
        <thead><tr><th scope="col"><span className="sr-only">Setting</span></th>{columns.map(c=>{const state=liveState(c);return <th scope="col" key={c.id} className={`${c.profile?'':'default-col'} ${state?`live-col ${state}`:''}`}>
          <div className="compare-head"><strong title={c.profile?.name||c.title}>{c.title}</strong>{c.subtitle&&<small>{c.subtitle}</small>}
            <div className="compare-actions">{state?<span className={`setup-live ${state}`}>{state==='loading'?<LoaderCircle size={12} className="spin"/>:<Check size={12}/>}{state==='loading'?'Loading':'Loaded'}</span>:null}{c.profile?<>{!state&&<button className="primary small" disabled={busy||!!model.issues.length} onClick={()=>props.onChat(model,c.profile)} aria-label={`Load ${c.profile.name}`}><Play size={12}/>Load</button>}<button className="icon-btn" disabled={busy} title="Edit settings" aria-label={`Edit ${c.profile.name}`} onClick={()=>props.onConfigure(model,c.profile)}><Settings2 size={14}/></button><button className="icon-btn" disabled={busy} title="Rename" aria-label={`Rename ${c.profile.name}`} onClick={()=>props.onRename(c.profile!)}><Pencil size={14}/></button><button className="icon-btn" disabled={busy} title="Duplicate" aria-label={`Duplicate ${c.profile.name}`} onClick={()=>props.onDuplicate(c.profile!)}><Copy size={14}/></button><button className="icon-btn" title={props.copiedId===c.profile.id?'Copied':'Copy configuration'} aria-label={`Copy configuration of ${c.profile.name}`} onClick={()=>props.onCopy(c.profile!)}>{props.copiedId===c.profile.id?<Check size={14}/>:<Braces size={14}/>}</button><button className="icon-btn danger" disabled={busy} title="Move to Recently deleted" aria-label={`Delete ${c.profile.name}`} onClick={()=>props.onDelete(c.profile!)}><Trash2 size={14}/></button></>:
              !state&&<button className="secondary small" disabled={busy||!!model.issues.length} onClick={()=>props.onChat(model)}><Play size={12}/>Load defaults</button>}</div></div></th>})}</tr></thead>
        <tbody>
          {shown.map(r=><tr key={r.key}><th scope="row">{r.label}</th>{columns.map((c,i)=>{const live=liveState(c),changed=i>0&&(!same(c.settings,base,r.key as keyof Settings)||(r.key==='engine'&&c.settings.engine!==base.engine));return <td key={c.id} className={`${changed?'changed':''} ${live?`live-col ${live}`:''}`}>{r.value(c.settings,model)}</td>})}</tr>)}
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
