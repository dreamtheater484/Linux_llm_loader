import {useEffect, useMemo, useRef, useState} from 'react';
import {ArrowLeft, ArrowUpRight, Check, CheckCircle2, Cpu, Download, Eye, HardDrive, KeyRound, Layers, Lock, LoaderCircle, MessageSquare, Pause, Play, Search, Sparkles, Star, X, XCircle, Zap} from 'lucide-react';
import {Hint, hueStyle} from '../ui';
import {shortTokens} from './context-slider';
import {GIB, assessFit, fitRank, gb} from './fit';
import type {Fit, Hardware} from './fit';
import {FitDot, engineName} from './library';

type Request = (path:string, body?:unknown, method?:string) => Promise<any>;
type EngineInfo = {id:string;name:string;installed:boolean;discoverable:boolean;formats:string[];architectures:string[]|null};
type Family = {key:string;name:string;title:string;params:number|null;active_b:number|null;context:number|null;engines:string[];repos:number;downloads:number;owners:string[];variants:number};
type Option = {id:string;repo:string;owner:string;variant:string;engine:string;format:string;quant:string;revision:string;size:number;weight_bytes:number;vision:boolean;mtp:boolean;gated:boolean;downloads:number;file_count:number;folder:string;title:string};
type FamilyDetail = {key:string;name:string;title:string;base_model:string|null;params:number|null;active_b:number|null;expert_fraction:number|null;context:number|null;vision:boolean;engines:string[];options:Option[]};
export type Job = {id:string;title:string;family:string;repo:string;owner:string;variant:string;quant:string;engine:string;format:string;folder:string;total:number;done:number;verified:number;speed:number|null;eta:number|null;state:string;error:string|null;note:string|null;file_count:number;model_id?:string|null;vision?:boolean};

const compact = (n:number) => n>=1e6?`${+(n/1e6).toFixed(1)}M`:n>=1e3?`${+(n/1e3).toFixed(1)}K`:`${n}`;
const billions = (n:number|null) => n?`${n>=1e10?Math.round(n/1e9):+(n/1e9).toFixed(1)}B`:null;
const duration = (s:number|null) => s==null||!Number.isFinite(s)?'estimating…':s<60?`${Math.max(1,Math.round(s))} s left`:s<3600?`${Math.round(s/60)} min left`:`${Math.floor(s/3600)} h ${Math.round(s%3600/60)} min left`;
const speedText = (b:number|null) => b==null?'—':b>=1e9?`${(b/1e9).toFixed(2)} GB/s`:`${(b/1e6).toFixed(b<1e7?1:0)} MB/s`;
const bits = (o:Option, params:number|null) => params?o.weight_bytes*8/params:null;
const familyExperts = (f:{active_b:number|null;params:number|null;expert_fraction?:number|null}) => f.expert_fraction??(f.active_b&&f.params?Math.max(0,1-f.active_b*1e9/f.params):0);

export function DownloadsPanel({jobs,request,onChanged,onOpen,compactView=false}:{jobs:Job[];request:Request;onChanged:(jobs:Job[])=>void;onOpen:(job:Job,chat?:boolean)=>void;compactView?:boolean}) {
  const [working,setWorking]=useState<string|null>(null),[error,setError]=useState('');
  const act=async(job:Job,action:string)=>{setWorking(job.id+action);setError('');try{onChanged(await request(`/api/downloads/${job.id}/${action}`,{}))}catch(e){setError((e as Error).message)}finally{setWorking(null)}};
  if(!jobs.length)return null;
  return <section className={`downloads-panel ${compactView?'compact':''}`} aria-label="Downloads">
    <header><h3><Download size={16}/>Downloads</h3><span>Saved into their own folder in your model library</span></header>
    {error&&<p className="downloads-error" role="alert">{error}</p>}
    {jobs.map(job=>{const pct=job.total?job.done/job.total*100:0,verifying=job.state==='verifying',vpct=job.total?job.verified/job.total*100:0;
      return <article key={job.id} className={`download-job ${job.state}`} style={hueStyle(job.family?.replace(/[-_]+/g,' '))}>
        <div className="download-title"><strong>{job.title}</strong><small>{job.owner}{job.variant&&job.variant!=='Original'?` · ${job.variant}`:''} · {engineName(job.engine)} · {job.file_count} file{job.file_count===1?'':'s'}</small></div>
        {(job.state==='downloading'||job.state==='queued'||job.state==='paused'||verifying)&&<div className="download-progress" role="progressbar" aria-label={`${job.title} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(verifying?vpct:pct)}><i style={{width:`${verifying?vpct:pct}%`}}/></div>}
        <div className="download-stats">
          {job.state==='downloading'&&<><b>{Math.floor(pct)}%</b><span>{gb(job.done)} of {gb(job.total)}</span><span>{speedText(job.speed)}</span><span>{duration(job.eta)}</span></>}
          {job.state==='queued'&&<span>Waiting for the current download · {gb(job.total)}</span>}
          {job.state==='paused'&&<span>Paused at {Math.floor(pct)}% · {gb(job.done)} of {gb(job.total)}</span>}
          {verifying&&<span><LoaderCircle size={12} className="spin"/>Checking file integrity · {Math.floor(vpct)}%</span>}
          {job.state==='complete'&&<span className="done"><CheckCircle2 size={14}/>Ready · {gb(job.total)} in {job.folder}</span>}
          {job.state==='failed'&&<span className="failed"><XCircle size={14}/>{job.error}</span>}
          {job.state==='cancelled'&&<span>Cancelled · partial files removed</span>}
          {job.note&&<span className="note">{job.note}</span>}
        </div>
        <div className="download-actions">
          {job.state==='downloading'&&<button className="secondary small" disabled={!!working} onClick={()=>act(job,'pause')}><Pause size={13}/>Pause</button>}
          {(job.state==='paused'||job.state==='failed')&&<button className="secondary small" disabled={!!working} onClick={()=>act(job,'resume')}><Play size={13}/>{job.state==='failed'?'Retry':'Resume'}</button>}
          {['downloading','queued','paused','verifying','failed'].includes(job.state)&&<button className="icon-btn" disabled={!!working} title="Cancel and delete partial files" aria-label={`Cancel ${job.title}`} onClick={()=>act(job,'cancel')}><X size={15}/></button>}
          {job.state==='complete'&&job.model_id&&<><button className="primary small" onClick={()=>onOpen(job,true)}><MessageSquare size={13}/>Load &amp; chat</button><button className="secondary small" onClick={()=>onOpen(job)}>View in Models</button></>}
          {['complete','cancelled'].includes(job.state)&&<button className="icon-btn" title="Remove from this list" aria-label={`Remove ${job.title} from downloads`} onClick={()=>act(job,'dismiss')}><X size={15}/></button>}
        </div>
      </article>})}
  </section>;
}

export function DiscoverPage({request,hardware:hw,jobs,onJobs,onOpen,onHubSettings,localRepos}:{request:Request;hardware:Hardware;jobs:Job[];onJobs:(jobs:Job[])=>void;onOpen:(job:Job,chat?:boolean)=>void;onHubSettings:()=>void;localRepos:Set<string>}) {
  const [engines,setEngines]=useState<EngineInfo[]|null>(null),[diskFree,setDiskFree]=useState<number|null>(null),[hubToken,setHubToken]=useState<boolean|null>(null);
  const [query,setQuery]=useState(''),[results,setResults]=useState<Family[]|null>(null),[searching,setSearching]=useState(false),[error,setError]=useState('');
  const [family,setFamily]=useState<FamilyDetail|null>(null),[opening,setOpening]=useState<string|null>(null);
  const seq=useRef(0);
  useEffect(()=>{request('/api/discover/engines').then(d=>{setEngines(d.engines);setDiskFree(d.disk_free);setHubToken(!!d.hub_token)}).catch(e=>setError(e.message))},[jobs.filter(j=>j.state==='complete').length]);
  useEffect(()=>{const id=++seq.current;setSearching(true);const timer=setTimeout(()=>{
    request(`/api/discover/search?q=${encodeURIComponent(query)}`).then(d=>{if(id===seq.current){setResults(d.families);setError('')}}).catch(e=>{if(id===seq.current)setError(e.message)}).finally(()=>{if(id===seq.current)setSearching(false)});
  },query?350:0);return()=>clearTimeout(timer)},[query]);
  const open=async(f:Family)=>{setOpening(f.key);setError('');try{setFamily(await request(`/api/discover/family?key=${encodeURIComponent(f.key)}&name=${encodeURIComponent(f.name)}`));window.scrollTo?.(0,0)}catch(e){setError((e as Error).message)}finally{setOpening(null)}};
  const discoverable=engines?.filter(e=>e.discoverable)||[];
  return <main className="page discover-page">
    <div className="page-heading"><div><h1>Discover models</h1><p>Download models that your installed engines can run. Each download is checked against your hardware and saved in its own folder.</p></div></div>
    <section className="discover-context">
      <div className="hardware-summary"><Cpu size={18}/><div><strong>{hw.gpuName||'Your computer'}</strong><span>{gb(hw.vram,0)} graphics memory · {gb(hw.ram,0)} system memory{diskFree!=null&&<> · {gb(diskFree,0)} free for models</>}</span></div></div>
      <div className="engine-support">{engines?engines.map(e=><span key={e.id} className={`engine-chip ${e.discoverable?'on':'off'}`} title={e.discoverable?`${e.name} runs ${e.formats.join(', ')} models${e.architectures?` · ${e.architectures.length} architectures recognised`:''}. Only models it supports are shown.`:e.installed?`${e.name} downloads are not supported yet.`:`${e.name} is not installed, so its models are hidden.`}>{e.discoverable?<Check size={12}/>:<X size={12}/>}{e.name}<small>{e.discoverable?e.formats.join(', '):e.installed?'not yet supported':'not installed'}</small></span>):<span className="engine-chip"><LoaderCircle size={12} className="spin"/>Checking installed engines…</span>}{hubToken!=null&&<button className={`engine-chip hub-chip ${hubToken?'on':'off'}`} onClick={onHubSettings} title={hubToken?'A Hugging Face token is set: gated models whose licence you accepted, and your private models, can be downloaded.':'Public models download without an account. Add a free Hugging Face token to unlock gated and private models.'}><KeyRound size={12}/>{hubToken?'Hugging Face token':'Add Hugging Face token'}<small>{hubToken?'gated & private models':'optional'}</small></button>}</div>
    </section>
    <DownloadsPanel jobs={jobs} request={request} onChanged={onJobs} onOpen={onOpen}/>
    {error&&<div className="error-banner discover-error" role="alert"><XCircle size={16}/><span>{error}</span></div>}
    {family?<FamilyView family={family} hw={hw} jobs={jobs} request={request} onJobs={onJobs} onBack={()=>setFamily(null)} diskFree={diskFree} localRepos={localRepos}/>:<>
      <label className="discover-search"><Search size={19}/><input autoFocus aria-label="Search Hugging Face models" placeholder="Search a model, e.g. Qwen3.6 27B, gemma 4 or GLM" value={query} onChange={e=>setQuery(e.target.value)}/>{searching&&<LoaderCircle size={16} className="spin"/>}</label>
      {engines&&!discoverable.length&&<div className="models-empty"><p>No installed engine supports downloads yet. Install llama.cpp or ExLlamaV3 from Settings → Engines.</p></div>}
      <div className="discover-caption"><h2>{query?`Models matching “${query}”`:'Trending right now'}</h2><span>Grouped by model; pick one to choose a provider and quant.</span></div>
      <div className="family-grid">{results?.map(f=>{const experts=familyExperts(f),q4=f.params?f.params*4.8/8:null,estimate=q4?assessFit(q4,experts,hw):null;
        // Without an active-parameter count we cannot tell a large MoE from a dense model yet.
        const fit=estimate&&(estimate.level==='gpu'||f.active_b)?estimate:null;
        return <button key={f.key} className="family-card" style={hueStyle(f.title)} onClick={()=>open(f)} disabled={!!opening}>
          <span className="family-top"><span className="model-symbol">{f.title.charAt(0).toUpperCase()}</span><strong>{f.title}</strong>{opening===f.key&&<LoaderCircle size={15} className="spin"/>}</span>
          <span className="family-facts">{billions(f.params)&&<span>{billions(f.params)} {f.active_b?`· MoE, ${f.active_b}B active`:'· dense'}</span>}{f.context&&<span>{shortTokens(f.context)} context</span>}</span>
          <span className="family-engines">{f.engines.map(e=><span key={e} className={`format-tag ${e==='gguf'?'gguf':'exl3'}`}>{engineName(e)}</span>)}</span>
          <span className="family-foot">{fit&&<span className="family-fit" title={`Estimate at about 4.8 bits per weight · ${fit.detail}`}><FitDot fit={fit}/>{fit.level==='gpu'?'Fits your GPU':fit.level==='split'?'Runs well here':fit.level==='slow'?'Runs slowly here':'Too large here'} at ~4-bit</span>}{!fit&&q4&&<span className="family-fit">Open to check fit</span>}<span>{f.repos} version{f.repos===1?'':'s'} · {compact(f.downloads)} downloads</span></span>
          <span className="family-owners">From {f.owners.slice(0,3).join(', ')}{f.owners.length>3?' and others':''}</span>
        </button>})}</div>
      {results&&!results.length&&!searching&&<div className="models-empty"><p>No downloadable models match “{query}” for your installed engines. Try fewer words, such as just the model name.</p></div>}
    </>}
  </main>;
}

type Sort = 'fit'|'quality'|'small'|'popular';
function FamilyView({family,hw,jobs,request,onJobs,onBack,diskFree,localRepos}:{family:FamilyDetail;hw:Hardware;jobs:Job[];request:Request;onJobs:(jobs:Job[])=>void;onBack:()=>void;diskFree:number|null;localRepos:Set<string>}) {
  const [engine,setEngine]=useState('all'),[owner,setOwner]=useState(''),[variant,setVariant]=useState(''),[quant,setQuant]=useState(''),[runsWell,setRunsWell]=useState(true),[sort,setSort]=useState<Sort>('fit');
  const [starting,setStarting]=useState<string|null>(null),[error,setError]=useState(''),[limit,setLimit]=useState(40);
  const experts=familyExperts(family);
  const rated=useMemo(()=>family.options.map(o=>({o,fit:assessFit(o.size,experts,hw),bits:bits(o,family.params)})),[family,hw.vram,hw.ram]);
  const scoped=rated.filter(({o})=>(engine==='all'||o.engine===engine));
  const owners=[...new Set(scoped.map(r=>r.o.owner))].sort((a,b)=>scoped.filter(r=>r.o.owner===b).reduce((n,r)=>Math.max(n,r.o.downloads),0)-scoped.filter(r=>r.o.owner===a).reduce((n,r)=>Math.max(n,r.o.downloads),0));
  const variants=[...new Set(scoped.filter(r=>!owner||r.o.owner===owner).map(r=>r.o.variant))].sort((a,b)=>a==='Original'?-1:b==='Original'?1:a.localeCompare(b));
  const quants=[...new Set(scoped.filter(r=>(!owner||r.o.owner===owner)&&(!variant||r.o.variant===variant)).map(r=>r.o.quant))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
  const visible=scoped.filter(({o,fit})=>(!owner||o.owner===owner)&&(!variant||o.variant===variant)&&(!quant||o.quant===quant)&&(!runsWell||fitRank[fit.level]<=1));
  const order={fit:(a:typeof rated[0],b:typeof rated[0])=>fitRank[a.fit.level]-fitRank[b.fit.level]||(b.bits??0)-(a.bits??0)||b.o.downloads-a.o.downloads,
    quality:(a:typeof rated[0],b:typeof rated[0])=>(b.bits??0)-(a.bits??0)||b.o.downloads-a.o.downloads,
    small:(a:typeof rated[0],b:typeof rated[0])=>a.o.size-b.o.size,
    popular:(a:typeof rated[0],b:typeof rated[0])=>b.o.downloads-a.o.downloads||(b.bits??0)-(a.bits??0)}[sort];
  visible.sort(order);
  // Best match: the most detailed quant that runs well, from the most trusted (downloaded) original upload.
  const recommended=[...rated].filter(r=>fitRank[r.fit.level]<=1&&r.o.variant==='Original'&&(r.bits??9)<=8.6&&!r.o.gated).sort((a,b)=>fitRank[a.fit.level]-fitRank[b.fit.level]||Math.round((b.bits??0)*2)-Math.round((a.bits??0)*2)||b.o.downloads-a.o.downloads)[0];
  const jobFor=(o:Option)=>jobs.find(j=>j.repo===o.repo&&j.quant===o.quant&&!['cancelled','failed'].includes(j.state));
  const start=async(o:Option)=>{setStarting(o.id);setError('');try{onJobs(await request('/api/downloads',{option_id:o.id}))}catch(e){setError((e as Error).message)}finally{setStarting(null)}};
  const action=(o:Option,fit:Fit)=>{const job=jobFor(o);
    if(job)return <span className={`option-state ${job.state}`}>{job.state==='complete'?<><Check size={13}/>Downloaded</>:job.state==='downloading'?<><LoaderCircle size={13} className="spin"/>{Math.floor(job.done/job.total*100)}%</>:job.state==='verifying'?'Checking…':job.state==='paused'?'Paused':'Queued'}</span>;
    const tooBig=diskFree!=null&&o.size>diskFree-2*GIB;
    return <button className={fitRank[fit.level]<=1?'primary small':'secondary small'} disabled={!!starting||tooBig} title={tooBig?'Not enough free space on the model drive':o.gated?'Gated: accept the licence on its Hugging Face page and add a token in Settings → Model library':`Download ${gb(o.size)} into ${o.folder}`} onClick={()=>start(o)}>{starting===o.id?<LoaderCircle size={13} className="spin"/>:<Download size={13}/>}Download</button>};
  return <section className="family-view" style={hueStyle(family.title)}>
    <button className="back-link" onClick={onBack}><ArrowLeft size={15}/>All models</button>
    <header className="model-hero discover-hero"><span className="model-symbol big">{family.title.charAt(0).toUpperCase()}</span>
      <div className="model-hero-copy"><h2>{family.title}</h2><p>{family.options.length} versions from {new Set(family.options.map(o=>o.owner)).size} providers</p>
        <div className="fact-chips">{billions(family.params)&&<span className="fact">{billions(family.params)} parameters</span>}<span className="fact" title={experts?'Only some experts work on each word; the rest can wait in system RAM.':'All weights are used for every word, so it should fit on the GPU to be fast.'}>{experts?`Mixture of experts${family.active_b?` · ${family.active_b}B active`:''}`:'Dense model'}</span>{family.context&&<span className="fact">Up to {shortTokens(family.context)} context</span>}{family.vision&&<span className="fact good"><Eye size={12}/>Understands images</span>}{family.engines.map(e=><span key={e} className="fact"><Cpu size={12}/>{engineName(e)}</span>)}{family.base_model&&<a className="fact link" href={`https://huggingface.co/${family.base_model}`} target="_blank" rel="noreferrer">Model card<ArrowUpRight size={12}/></a>}</div></div></header>
    {recommended&&<div className="recommended-option"><Star size={17}/><div><strong>Best match for your computer</strong><span>{recommended.o.owner} · {recommended.o.quant} · {engineName(recommended.o.engine)} · {gb(recommended.o.size)} · {recommended.fit.label}{recommended.o.vision?' · includes image support':''}</span></div>{action(recommended.o,recommended.fit)}</div>}
    <div className="option-filters">
      <div className="filter-block"><span>Inference engine<Hint label="inference engine" text="llama.cpp runs single-file GGUF models and handles CPU offload flexibly. ExLlamaV3 runs EXL3 folders and is usually faster when the model fits on the GPU."/></span><div className="segmented">{['all',...family.engines].map(e=><button key={e} className={engine===e?'chosen':''} onClick={()=>{setEngine(e);setOwner('');setVariant('');setQuant('')}}>{e==='all'?'All':engineName(e)}</button>)}</div></div>
      <label className="filter-block"><span>Provider</span><select value={owner} onChange={e=>{setOwner(e.target.value);setVariant('');setQuant('')}}><option value="">All providers ({owners.length})</option>{owners.map(o=><option key={o}>{o}</option>)}</select></label>
      <label className="filter-block"><span>Variant<Hint label="variant" text="Original is the model as released. Other variants are community fine-tunes or merges, such as uncensored (“abliterated”, “heretic”) or distilled versions."/></span><select value={variant} onChange={e=>{setVariant(e.target.value);setQuant('')}}><option value="">All variants ({variants.length})</option>{variants.map(v=><option key={v}>{v}</option>)}</select></label>
      <label className="filter-block"><span>Quant<Hint label="quant" text="How strongly the model is compressed. More bits keep more quality but need more memory. Around 4–5 bits is the usual sweet spot; below 3 bits quality drops noticeably."/></span><select value={quant} onChange={e=>setQuant(e.target.value)}><option value="">All quants ({quants.length})</option>{quants.map(q=><option key={q}>{q}</option>)}</select></label>
      <label className="filter-block"><span>Sort by</span><select value={sort} onChange={e=>setSort(e.target.value as Sort)}><option value="fit">Best fit, then quality</option><option value="quality">Highest quality</option><option value="small">Smallest download</option><option value="popular">Most popular</option></select></label>
      <label className="only-diff runs-well"><input type="checkbox" checked={runsWell} onChange={e=>setRunsWell(e.target.checked)}/>Only what runs well here</label>
    </div>
    {error&&<div className="error-banner" role="alert"><XCircle size={16}/><span>{error}</span></div>}
    <div className="option-table" role="table" aria-label="Download options">
      <div className="option-row head" role="row"><span role="columnheader">Provider · variant</span><span role="columnheader">Engine</span><span role="columnheader">Quant</span><span role="columnheader">Download</span><span role="columnheader">On this computer</span><span role="columnheader"><span className="sr-only">Action</span></span></div>
      {visible.slice(0,limit).map(({o,fit,bits})=><div key={o.id} className={`option-row ${recommended?.o.id===o.id?'recommended':''}`} role="row">
        <span role="cell" className="option-provider"><strong>{o.owner}{o.gated&&<Lock size={11} className="gated-icon" aria-label="Gated"><title>Gated: accept the licence on Hugging Face first, and add a token in Settings</title></Lock>}</strong><small title={o.variant}>{o.variant}{o.mtp&&!/mtp/i.test(o.variant)?' · MTP':''}</small></span>
        <span role="cell"><span className={`format-tag ${o.engine==='gguf'?'gguf':'exl3'}`}>{engineName(o.engine)}</span></span>
        <span role="cell" className="option-quant"><b>{o.quant}</b>{bits&&<small>≈ {bits.toFixed(1)} bits</small>}</span>
        <span role="cell" className="option-size">{gb(o.size)}<small>{o.file_count} file{o.file_count===1?'':'s'}{o.vision?' · incl. vision':''}</small></span>
        <span role="cell" className={`option-fit ${fit.level}`} title={fit.detail}><FitDot fit={fit}/>{fit.label}</span>
        <span role="cell" className="option-action">{localRepos.has(o.repo)&&!jobFor(o)&&<small className="have-note" title="You already have a download from this repository">In library</small>}{action(o,fit)}</span>
      </div>)}
      {!visible.length&&<div className="option-empty">{runsWell&&scoped.length?<>Nothing here runs well on this computer. <button className="icon-link" onClick={()=>setRunsWell(false)}>Show all options</button></>:'No downloads match these filters.'}</div>}
      {visible.length>limit&&<button className="secondary more-options" onClick={()=>setLimit(limit+60)}>Show {Math.min(60,visible.length-limit)} more of {visible.length-limit}</button>}
    </div>
    <p className="discover-footnote"><HardDrive size={13}/>Downloads resume after interruptions, are checked against Hugging Face’s checksums, and appear in Models when complete. Multi-part models and their image-support files are combined into one folder automatically.</p>
  </section>;
}
