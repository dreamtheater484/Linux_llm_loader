import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, Copy, Pencil, RotateCcw, ArrowDown, ArrowUpRight, Check, ChevronDown, CircleHelp, Cpu, Eye, Gauge, HardDrive, ImagePlus, Layers, LoaderCircle, MessageSquare, Play, Plus, Power, RefreshCw, Save, Search, Settings2, Sparkles, Square, Terminal, Trash2, X, Zap} from 'lucide-react';
import './style.css';
import {Modal, Performance, formatNumber} from './ui';
import './refinements.css';
import {Benchmarks} from './benchmarks';
import type {Model, Settings, Profile} from './types';
import {ProfileList, suggestedProfileName} from './profiles';
import {BenchmarkLive} from './benchmark-live';
import {LoadFeedback} from './load-feedback';
import {ThinkingSelect} from './reasoning';
import {MemoryMetric} from './memory';
import {memoryBreakdown} from './memory-accounting';
import {InflectMark} from './brand';
import {ChatWorkspace} from './chat/workspace';
import './chat/layout.css';

const number = formatNumber;
const gib = (n:number|undefined|null) => n == null ? '—' : number(n / 2**30, n < 2**30 ? 2 : 1);
const short = (n:number) => n>=1024 ? `${n/1024}K` : `${n}`;
const normalizeSettings = (value:Settings & {thinking?:boolean}):Settings => {
  const {thinking,...settings}=value;
  return {...settings,ngram_ram:settings.ngram_ram??true,gguf_offload:settings.gguf_offload??'layers',reasoning_effort:settings.reasoning_effort??(thinking===false?'off':'default')};
};
const defaults = (model:Model):Settings => {
  const qwen=model.name.startsWith('Qwen3.8-Flash-Next-EXL3');
  const deep=model.name.startsWith('DeepSeek')&&model.format==='EXL3';
  const gguf=model.format==='GGUF';
  return {model_id:model.id,engine:'auto',context:Math.min(gguf?32768:262144,model.context),kv:'Q8',vision:model.vision,ngram_ram:true,
    prediction:qwen||deep||(gguf&&model.mtp)?'mtp':'off',draft_tokens:deep?3:2,max_output:Math.min(4096,Math.floor(model.context/4)),
    cpu_percent:gguf?(model.bytes>26*2**30?10:0):qwen?60:80,gguf_offload:gguf&&model.experts>0?'experts':'layers',cpu_threads:8,chunk_size:gguf?512:1024,temperature:gguf?1:0.7,reasoning_effort:'default'};
};

async function api(path:string, body?:unknown, method?:string) {
  const response = await fetch(path,{method:method||(body === undefined?'GET':'POST'),headers:body===undefined&&!method?{}:{'Content-Type':'application/json','X-Inflect-Local':'1'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){let data;try{data=await response.json()}catch{data={detail:response.statusText}}throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail))}
  return response.json();
}

async function writeClipboard(text:string) {
  try {
    if(navigator.clipboard){await navigator.clipboard.writeText(text);return}
  } catch {}
  const area=document.createElement('textarea');
  area.value=text;area.readOnly=true;area.style.position='fixed';area.style.opacity='0';area.style.pointerEvents='none';
  document.body.appendChild(area);area.select();const copied=document.execCommand('copy');area.remove();
  if(!copied)throw new Error('Clipboard unavailable');
}

function Toggle({checked,onChange,disabled=false,label}:{checked:boolean;onChange:(v:boolean)=>void;disabled?:boolean;label:string}) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} className={`toggle ${checked?'on':''}`} onClick={()=>onChange(!checked)}><span/></button>;
}

function Metric({icon:Icon,label,value,unit,detail,percent,history}:{icon:any;label:string;value:string;unit?:string;detail:string;percent?:number|null;history?:number[]}) {
  return <div className="metric"><div className="metric-label"><Icon size={15}/>{label}{history&&<svg className="sparkline" viewBox="0 0 78 20" aria-hidden="true"><polyline points={history.map((v,i)=>`${i*78/Math.max(history.length-1,1)},${19-Math.min(v,100)*.18}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="1.5"/></svg>}</div><div className="metric-value">{value}<span>{unit}</span></div><div className="metric-detail">{detail}</div>{percent!=null&&<div className="meter"><i style={{width:`${Math.min(percent,100)}%`}}/></div>}</div>;
}

function App(){
  const [models,setModels]=useState<Model[]>([]), [inventoryErrors,setInventoryErrors]=useState<string[]>([]), [modelRoot,setModelRoot]=useState('');
  const [selected,setSelected]=useState<Model|null>(null), [settings,setSettings]=useState<Settings|null>(null), [status,setStatus]=useState<any>(null);
  const [benchmarkResult,setBenchmarkResult]=useState<{kind:string;id:string}|null>(null);
  const [view,setView]=useState<'workbench'|'benchmarks'|'profiles'>('workbench');
  const [expanded,setExpanded]=useState(false),[previewOpen,setPreviewOpen]=useState(false),[settingsOpen,setSettingsOpen]=useState(false);
  const [query,setQuery]=useState(''), [showAll,setShowAll]=useState(false);
  const [streaming,setStreaming]=useState(false), [error,setError]=useState(''), [toast,setToast]=useState('');
  const [logs,setLogs]=useState<string[]|null>(null), [profiles,setProfiles]=useState<Profile[]>([]), [benchmarks,setBenchmarks]=useState<any[]>([]);
  const [profileName,setProfileName]=useState(''), [saveOpen,setSaveOpen]=useState(false), [acting,setActing]=useState(false), [offline,setOffline]=useState(false), [closed,setClosed]=useState(false);
  const [cpuHistory,setCpuHistory]=useState<number[]>([]), [powerHistory,setPowerHistory]=useState<number[]>([]);
  const [editingId,setEditingId]=useState<string|null>(null), [saveMode,setSaveMode]=useState<'create'|'update'|'rename'>('create'), [saveTarget,setSaveTarget]=useState<Profile|null>(null), [profileError,setProfileError]=useState('');
  const [loadTarget,setLoadTarget]=useState<Model|null>(null), [autoProfileName,setAutoProfileName]=useState(true);
  const [deletedProfiles,setDeletedProfiles]=useState<Profile[]>([]), [showDeleted,setShowDeleted]=useState(false), [undoProfile,setUndoProfile]=useState<Profile|null>(null);
  const [cpuPowerHistory,setCpuPowerHistory]=useState<number[]>([]), [copiedProfileId,setCopiedProfileId]=useState<string|null>(null);
  const draftSettings=useRef<Settings|null>(null), selectedId=useRef<string|null>(null), lastEngineSettings=useRef<Settings|null>(null);
  const stopped=useRef(false);
  draftSettings.current=settings;selectedId.current=selected?.id||null;
  const editingProfile=profiles.find(p=>p.id===editingId);
  const profileDirty=!!editingProfile&&JSON.stringify(editingProfile.settings)!==JSON.stringify(settings);
  const session=status?.session, hardware=status?.hardware, gpu=hardware?.gpu, ram=hardware?.ram;
  const modelRam=memoryBreakdown(ram,hardware?.model_memory,!!session?.model&&['loading','ready','stopping'].includes(session?.state)).modelBytes;
  const ready=!loadTarget&&session?.state==='ready', loading=!!loadTarget||session?.state==='loading'||session?.state==='stopping', active=ready&&session?.model?.id===selected?.id;
  const installed=status?.engines?.find((e:any)=>e.id===(settings?.engine==='auto'?selected?.engines[0]:settings?.engine))?.installed;
  const dirty=active&&settings&&session?.settings&&Object.entries(settings).some(([k,v])=>!['max_output','temperature','engine','reasoning_effort'].includes(k)&&session.settings[k]!==v);
  const busy=streaming||session?.busy||session?.benchmark?.state==='running';

  const choose=(model:Model,preset?:Settings,profile?:Profile)=>{
    if(streaming)return;
    const chosen=normalizeSettings(preset||(session?.model?.id===model.id?session.settings:defaults(model)));setSelected(model);setSettings(chosen);setEditingId(profile?.id||null);setError('');setView('workbench');
  };
  const update=(key:keyof Settings,value:any)=>setSettings(s=>s?({...s,[key]:value,...(key==='context'&&s.max_output>=value?{max_output:Math.min(4096,value/4)}:{})}):s);
  const refresh=async(initial=false)=>{try{const data=await api(initial?'/api/library':'/api/library/refresh',initial?undefined:{});setModels(data.models);setInventoryErrors(data.errors);setModelRoot(data.root);if(initial&&data.models.length){const current=await api('/api/status');setStatus(current);const loaded=data.models.find((m:Model)=>m.id===current.session.model?.id);choose(loaded||data.models[0],loaded?current.session.settings:undefined);}if(!initial)setToast('Model library refreshed')}catch(e){setError(String(e))}};
  useEffect(()=>{refresh(true);api('/api/profiles').then(setProfiles).catch(()=>{});api('/api/benchmarks').then(setBenchmarks).catch(()=>{});
    let running=false;const poll=async()=>{if(running||stopped.current)return;running=true;try{const data=await api('/api/status');setStatus(data);if(data.session.settings){if(selectedId.current===data.session.model?.id&&lastEngineSettings.current&&JSON.stringify(draftSettings.current)===JSON.stringify(lastEngineSettings.current))setSettings(data.session.settings);lastEngineSettings.current=data.session.settings}setOffline(false);setCpuHistory(h=>[...h.slice(-23),data.hardware.cpu_percent??0]);if(data.hardware.cpu_power?.watts!=null)setCpuPowerHistory(h=>[...h.slice(-23),data.hardware.cpu_power.watts]);if(data.hardware.gpu?.power_watts!=null)setPowerHistory(h=>[...h.slice(-23),data.hardware.gpu.power_watts/(data.hardware.gpu.power_limit_watts||575)*100]);}catch{setOffline(true)}finally{running=false}};
    poll();const interval=setInterval(poll,1500);return()=>clearInterval(interval)},[]);
  useEffect(()=>{if(logs===null)return;const interval=setInterval(()=>api('/api/logs').then(d=>setLogs(d.lines)).catch(()=>{}),1200);return()=>clearInterval(interval)},[logs!==null]);
  useEffect(()=>{if(view==='benchmarks')api('/api/benchmarks').then(setBenchmarks).catch(()=>{});},[view,session?.benchmark?.state]);
  useEffect(()=>{if(view!=='profiles')return;let disposed=false,running=false;
    const poll=async()=>{if(running)return;running=true;try{const [saved,deleted]=await Promise.all([api('/api/profiles'),api('/api/profiles?trash=true')]);if(!disposed){setProfiles(saved);setDeletedProfiles(deleted)}}catch(e){if(!disposed)setError('Could not refresh profile measurements: '+String(e))}finally{running=false}};
    poll();const interval=setInterval(poll,3000);return()=>{disposed=true;clearInterval(interval)}},[view,session?.benchmark?.state,session?.evaluation?.state]);
  useEffect(()=>{if(toast){const id=setTimeout(()=>{setToast('');setUndoProfile(null)},8000);return()=>clearTimeout(id)}},[toast]);
  useEffect(()=>{const handler=(event:KeyboardEvent)=>{if(event.key==='Escape'){setLogs(null);setSaveOpen(false)}if(event.key==='/'&&!(event.target instanceof HTMLInputElement)&&!(event.target instanceof HTMLTextAreaElement)){event.preventDefault();document.querySelector<HTMLInputElement>('input[aria-label="Search models"]')?.focus()}};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)},[]);

  const action=async(fn:()=>Promise<void>)=>{setActing(true);setError('');try{await fn()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setActing(false)}};
  const requestLoad=async(next:Settings,model:Model)=>{setLoadTarget(model);try{const nextSession=await api('/api/load',next);setStatus((old:any)=>({...old,session:nextSession}))}finally{setLoadTarget(null)}};
  const load=()=>action(async()=>{if(settings&&selected)await requestLoad(settings,selected)});
  const unload=()=>action(async()=>{await api('/api/unload',{});setStatus(await api('/api/status'))});
  const quit=()=>action(async()=>{await api('/api/quit',{});stopped.current=true;setClosed(true);setStatus(null);setOffline(false)});
  const stop=async()=>{await api('/api/cancel',{}).catch(()=>{})};
  const profileConfig=(profile:Profile)=>JSON.stringify({name:profile.name,settings:profile.settings},null,2);
  const copyProfileConfig=async(profile:Profile)=>{try{await writeClipboard(profileConfig(profile));setCopiedProfileId(profile.id);setTimeout(()=>setCopiedProfileId(id=>id===profile.id?null:id),2000)}catch{setError('Clipboard access was unavailable. Select the configuration and use Ctrl+C.')}};
  const openSave=(mode:'create'|'update'|'rename'='create',target?:Profile)=>{setProfileError('');setSaveMode(mode);setSaveTarget(target||editingProfile||null);setAutoProfileName(mode==='create'||mode==='update'&&!!editingProfile?.auto_name);setProfileName(mode==='create'&&selected&&settings?suggestedProfileName(selected,settings):(target||editingProfile)?.name||'');setSaveOpen(true)};
  const save=async()=>{if(acting)return;setActing(true);setProfileError('');try{
    const body={name:profileName.trim(),auto_name:autoProfileName,settings:saveMode==='rename'?saveTarget?.settings:settings};
    const saved:Profile[]=await api(saveMode==='create'?'/api/profiles':`/api/profiles/${saveTarget?.id}`,body,saveMode==='create'?'POST':'PUT');
    setProfiles(saved);if(saveMode!=='rename')setEditingId(saveMode==='create'?saved.find(p=>!profiles.some(old=>old.id===p.id))?.id||null:saveTarget?.id||null);setSaveOpen(false);setToast(saveMode==='create'?'Profile saved':saveMode==='rename'?'Profile renamed':'Profile updated');
  }catch(e){setProfileError((e as Error).message)}finally{setActing(false)}};
  const removeProfile=(profile:Profile)=>action(async()=>{setProfiles(await api(`/api/profiles/${profile.id}`,{},'DELETE'));setDeletedProfiles(await api('/api/profiles?trash=true'));if(editingId===profile.id)setEditingId(null);setUndoProfile(profile);setToast(`“${profile.name}” moved to Recently deleted`)});
  const restoreProfile=(profile:Profile)=>action(async()=>{setProfiles(await api(`/api/profiles/${profile.id}/restore`,{}));setDeletedProfiles(await api('/api/profiles?trash=true'));setUndoProfile(null);setToast('Profile restored')});
  const useProfile=(profile:Profile,loadNow=false)=>{const model=models.find(m=>m.id===profile.settings.model_id);if(!model){setError('This profile’s model is no longer in the library. Reconnect its drive and refresh the library.');return}choose(model,profile.settings,profile);if(loadNow)action(async()=>{await requestLoad(profile.settings,model)});else setToast('Edit the settings on the right, then choose Save changes.')};
  const duplicateProfile=(profile:Profile)=>action(async()=>{setProfiles(await api(`/api/profiles/${profile.id}/duplicate`,{}));setToast('Profile duplicated. Edit its settings to make a new variation.')});
  const reorderProfiles=(ids:string[])=>action(async()=>{setProfiles(await api('/api/profiles/reorder',{ids}));setToast('Profile order saved')});
  const runBenchmark=(reasoning_effort:string)=>action(async()=>{await api('/api/benchmark',{reasoning_effort});setView('benchmarks');setStatus(await api('/api/status'))});

  const searchText=(text:string)=>text.toLowerCase().replace(/[-_]+/g,' ').replace(/\s+/g,' ').trim();
  const filtered=models.filter(m=>(showAll||m.recommended||!!query)&&searchText(m.name+' '+m.title+' '+m.format).includes(searchText(query)));
  const allCount=models.filter(m=>!m.recommended).length;
  return <div className={`app-shell ${expanded&&view==='workbench'?'chat-expanded':''} ${previewOpen&&view==='workbench'?'has-preview':''} ${settingsOpen?'show-chat-settings':''}`}>
    <aside className="sidebar">
      <a className="brand" href="/" onClick={e=>{e.preventDefault();setView('workbench')}} aria-label="Inflect home"><span className="brand-mark"><InflectMark size={43}/></span><span>Inflect<small>LOCAL MODEL WORKBENCH</small></span></a>
      <nav aria-label="Main navigation">{[{id:'workbench',label:'Workbench',icon:MessageSquare},{id:'benchmarks',label:'Benchmarks',icon:Gauge},{id:'profiles',label:'Saved profiles',icon:Save}].map(({id,label,icon:Icon})=><button key={id} className={view===id?'nav-item current':'nav-item'} onClick={()=>setView(id as any)}><Icon size={17}/>{label}{id==='profiles'&&profiles.length>0&&<span className="nav-count">{profiles.length}</span>}</button>)}</nav>
      <div className="library-heading"><span>MODEL LIBRARY</span><button className="icon-btn" title="Refresh model library" onClick={()=>refresh()}><RefreshCw size={14}/></button></div>
      <label className="search"><Search size={15}/><input aria-label="Search models" placeholder="Find a model…" value={query} onChange={e=>setQuery(e.target.value)}/><kbd>/</kbd></label>
      <div className="library-list">{filtered.map(model=><button className={`model-item ${selected?.id===model.id?'selected':''}`} disabled={streaming} key={model.id} onClick={()=>choose(model)}><div className={`model-symbol ${model.name.startsWith('DeepSeek')?'blue':''}`}>{model.name.startsWith('DeepSeek')?'D':'Q'}</div><div className="model-copy"><strong>{model.title}</strong><span>{model.format} · {model.quant}</span><small>{model.vision?<><Eye size={11}/> Vision</>:<>Text only</>}<span className={model.issues.length?'issue-dot':'local-dot'}/>{model.issues.length?'Incomplete':'Local'}</small></div>{session?.model?.id===model.id&&ready&&<span className="running-dot" title="Loaded"/>}</button>)}{filtered.length===0&&<p className="empty-library">{models.length?'No matching models.':'Scanning your model library…'}</p>}
      {!query&&allCount>0&&<button className="more-models" onClick={()=>setShowAll(!showAll)}><ChevronDown size={14} className={showAll?'rotate':''}/>{showAll?'Show recommended models':`${allCount} more local variants`}</button>}</div>
      <div className="sidebar-bottom"><div><span className="local-indicator"/>On your computer</div><p>Your models. Your conversations.<br/>Inference stays on this machine.</p><div className="path" title={modelRoot}><HardDrive size={12}/>{modelRoot.split('/').slice(-2).join('/')||'AI/models'}</div><button className="quit-button" disabled={acting||busy||closed} onClick={quit}><Power size={13}/>Quit Inflect</button></div>
    </aside>

    <div className="main-shell">
      {expanded&&view==='workbench'&&<div className="focus-metrics" aria-label="Live system metrics"><button className="focus-brand" onClick={()=>setExpanded(false)} title="Restore dashboard"><InflectMark size={27}/><strong>Inflect</strong></button><div><span>GPU</span><b>{number(gpu?.power_watts,0)} <small>W</small></b></div><div><span>CPU</span><b>{number(hardware?.cpu_power?.watts,1)} <small>W</small></b></div><div><span>VRAM</span><b>{gib(gpu?.used_bytes)} <small>/ {gib(gpu?.total_bytes)} GiB</small></b></div><div><span>Model RAM</span><b>{gib(modelRam)} <small>GiB</small></b></div><div><span>CPU load</span><b>{number(hardware?.cpu_percent,0)} <small>%</small></b></div><div className="focus-speed"><span>Prompt</span><b>{number(session?.last_usage?.prompt_tokens_per_second)} <small>tok/s</small></b></div><div className="focus-speed"><span>Decode</span><b>{number(session?.last_usage?.tokens_per_second)} <small>tok/s</small></b></div><div><span>First token</span><b>{number(session?.last_usage?.first_token_seconds)} <small>s</small></b></div><span className={`focus-status ${ready?'ready':''}`}>{streaming?'Generating':ready?'Model ready':'No model loaded'}</span></div>}
      <header className="topbar"><div className="breadcrumbs">Workspace <span>/</span><strong>{view==='workbench'?'Model workbench':view==='benchmarks'?'Benchmarks':'Saved profiles'}</strong></div><div className="topbar-right"><span className={`status-pill ${offline?'bad':ready?'good':''}`}><i/>{closed?'Inflect closed':offline?'Connection lost':loading?'Loading engine':busy?'Processing request':ready?'Model ready':'No model loaded'}</span>{session?.busy&&!streaming&&<button className="secondary" onClick={stop}><Square size={12}/>Stop request</button>}<button className="icon-btn" title="View engine logs" onClick={()=>{setLogs([]);api('/api/logs').then(d=>setLogs(d.lines))}}><Terminal size={18}/></button></div></header>
      <LoadFeedback session={session} pendingName={loadTarget?.title} onCancel={unload} onLogs={()=>{setLogs([]);api('/api/logs').then(d=>setLogs(d.lines))}} onChat={()=>{const model=models.find(m=>m.id===session?.model?.id);if(model)choose(model,session.settings)}}/>
      <section className="hardware-bar" aria-label="Live system metrics">
        <Metric icon={Zap} label="GPU POWER" value={number(gpu?.power_watts,0)} unit="W" detail={gpu?`${number(gpu.power_limit_watts,0)} W power limit · ${number(gpu.temperature_c,0)}°C`:'Waiting for GPU telemetry'} history={powerHistory}/>
        <Metric icon={Activity} label="CPU POWER" value={number(hardware?.cpu_power?.watts,1)} unit="W" detail={hardware?.cpu_power?.watts!=null?'CPU package · includes SoC':hardware?.cpu_power?.detail||'Reading package sensor…'} history={cpuPowerHistory}/>
        <Metric icon={Layers} label="VRAM" value={gib(gpu?.used_bytes)} unit={`/ ${gib(gpu?.total_bytes)} GiB`} detail={gpu?.name?.replace('NVIDIA GeForce ','')||'GPU unavailable'} percent={gpu?.total_bytes?gpu.used_bytes/gpu.total_bytes*100:null}/>
        <MemoryMetric ram={ram} model={hardware?.model_memory} loaded={!!session?.model&&['loading','ready','stopping'].includes(session?.state)}/>
        <Metric icon={Cpu} label="CPU USAGE" value={number(hardware?.cpu_percent,0)} unit="%" detail={`${hardware?.cpu_count||'—'} logical cores · system-wide`} history={cpuHistory}/>
      </section>
      <section className="performance-strip" aria-label="Response performance"><div className="performance-heading"><span className="eyebrow">LAST RESPONSE</span><span>{busy?(streaming?'Generating reply…':'Request in progress…'):session?.last_usage?'Measured by the engine':'Send a message to measure'}</span></div><Performance metrics={session?.last_usage}/></section>
      {closed&&<div className="note"><Power size={18}/><p>Inflect is closed and the model is unloaded. Open Inflect from your application menu to return.</p></div>}
      {(error||session?.error||offline)&&<div role="alert" className="error-banner"><CircleHelp size={18}/><span>{error||session?.error||(offline?'The local server is unavailable. Reopen Inflect using its launcher.':'')}</span><button className="icon-btn" onClick={()=>{setError('');setLogs([]);api('/api/logs').then(d=>setLogs(d.lines))}}>View logs</button></div>}
      {inventoryErrors.length>0&&<details className="inventory-errors"><summary>{inventoryErrors.length} library inspection notice(s)</summary>{inventoryErrors.map(e=><p key={e}>{e}</p>)}</details>}

      {selected&&settings&&<div className="workbench" hidden={view!=='workbench'}>
        <ChatWorkspace model={selected} models={models} settings={settings} session={session} active={active} dirty={!!dirty} expanded={expanded} onExpand={()=>{setExpanded(!expanded);setSettingsOpen(false)}} onModel={choose} onBusy={setStreaming} onPreview={setPreviewOpen} onSaveProfile={()=>openSave(editingProfile?'update':'create')} onLoad={load} onSettings={()=>{if(expanded||previewOpen)setSettingsOpen(!settingsOpen);else document.querySelector<HTMLElement>('.settings-panel')?.focus()}}/>

        <aside className="settings-panel" tabIndex={-1}><button className="close-chat-settings icon-btn" aria-label="Close model settings" onClick={()=>setSettingsOpen(false)}><X size={17}/></button><div className="settings-title"><Settings2 size={17}/><h2>Run settings</h2><span className="small-badge">LOCAL</span></div><p className="settings-intro">Make this model work for you.</p>{editingProfile&&<div className="chat-profile-note"><strong title={editingProfile.name}>{editingProfile.name}</strong><small>{profileDirty?'Unsaved profile changes':'Saved profile'}</small><button onClick={()=>openSave('update')}><Save size={12}/>Save changes</button></div>}<div className="settings-fields"><label className="field">Inference engine<select value={settings.engine} disabled={busy||loading} onChange={e=>update('engine',e.target.value)}><option value="auto">Auto · {selected.engines[0]==='exl3'?'ExLlamaV3':selected.engines[0]==='gguf'?'llama.cpp':selected.engines[0]}</option>{status?.engines?.map((engine:any)=><option key={engine.id} value={engine.id} disabled={!selected.engines.includes(engine.id)||!engine.installed}>{engine.name}{!engine.installed?' · not installed':''}</option>)}</select><small>{selected.format==='EXL3'?'ExLlamaV3 + TabbyAPI · isolated CUDA runtime':installed?(selected.format==='GGUF'?'llama.cpp · CUDA runtime':'Compatibility still needs qualification'):'Install this engine to use this checkpoint'}</small></label>
          <label className="field">Context window<select value={settings.context} disabled={busy||loading} onChange={e=>update('context',Number(e.target.value))}>{[4096,8192,16384,32768,65536,131072,262144,524288,1048576].filter(v=>v<=selected.context).map(v=><option value={v} key={v}>{short(v)} tokens{v===(selected.format==='GGUF'?32768:262144)?' · suggested':''}</option>)}</select><small>{number(settings.context,0)} tokens, including the answer</small></label>
          <div className="field"><span>KV cache precision</span><div className="segmented">{['FP16','Q8','Q4'].map(v=><button disabled={busy||loading} key={v} className={settings.kv===v?'chosen':''} onClick={()=>update('kv',v)}>{v}{v==='Q8'&&<span className="choice-dot"/>}</button>)}</div><small>{settings.kv==='Q8'?(selected.engines[0]==='vllm'?'FP8 cache · engine support required':'8-bit cache · preferred quality') :settings.kv==='Q4'?'4-bit cache · experimental quality trade-off':'16-bit cache · higher memory use'}</small></div>
          <div className="setting-switch"><div><label>Vision tower</label><small>{selected.vision?'Understand uploaded images':selected.format==='GGUF'?'No matching vision projector found':'Text-only checkpoint'}</small></div><Toggle label="Vision tower" checked={settings.vision} disabled={!selected.vision||busy||loading} onChange={v=>update('vision',v)}/></div>
          <div className="setting-switch"><div><label>Prediction acceleration</label><small>{selected.mtp?(selected.format==='GGUF'?'Embedded MTP · drafts verified by the model':'MTP · test its speed benefit'):(selected.mtp_note||'No MTP component identified')}</small></div><Toggle label="Prediction acceleration" checked={settings.prediction==='mtp'} disabled={!selected.mtp||!['exl3','gguf'].includes(selected.engines[0])||(selected.format==='GGUF'&&status?.engines?.find((e:any)=>e.id==='gguf')?.mtp===false)||busy||loading} onChange={v=>update('prediction',v?'mtp':'off')}/></div>
          {settings.prediction==='mtp'&&<label className="field compact">Draft tokens<select value={settings.draft_tokens} disabled={busy||loading} onChange={e=>update('draft_tokens',Number(e.target.value))}>{Array.from({length:selected.draft_limit||4},(_,i)=>i+1).map(n=><option key={n}>{n}</option>)}</select></label>}
          <label className="field">Profile thinking preference<ThinkingSelect model={selected} value={settings.reasoning_effort} disabled={!!busy||loading} onChange={value=>{update('reasoning_effort',value)}} label="Profile thinking preference"/><small>Initial choice for chat and benchmarks. Saved with this profile; no reload needed. Model loading keeps all native modes available.</small></label>
          <label className="field">Maximum output<select value={settings.max_output} onChange={e=>update('max_output',Number(e.target.value))}>{[256,512,1024,2048,4096,8192,16384,32768].filter(n=>n<settings.context).map(n=><option value={n} key={n}>{short(n)} tokens</option>)}</select><small>Thinking and answer share this limit. No separate thinking cap.</small></label>
          <details className="advanced"><summary><Settings2 size={14}/>Fine-tune<ChevronDown size={14}/></summary><div>{selected.ngram&&<div className="setting-switch"><div><label>PLE n-gram table</label><small>{settings.ngram_ram?'Keep the large lookup table in system RAM. Faster; can use tens of GiB.':'Stream lookup rows from model storage. Uses less RAM and depends on SSD speed.'}</small></div><Toggle label="Keep PLE n-gram table in RAM" checked={settings.ngram_ram} disabled={busy||loading} onChange={v=>update('ngram_ram',v)}/></div>}{selected.format==='GGUF'&&selected.experts>0&&<label className="field">CPU placement<select value={settings.gguf_offload} disabled={busy||loading} onChange={e=>update('gguf_offload',e.target.value)}><option value="experts">Experts · keep attention on GPU</option><option value="layers">Whole layers</option></select></label>}<label className="field">{selected.engines[0]==='gguf'?(settings.gguf_offload==='experts'?'CPU expert-layer share':'CPU layer share'):selected.engines[0]==='vllm'?'CPU weight offload':'CPU expert share'}<span className="range-value">{settings.cpu_percent}%</span><input type="range" aria-label="CPU memory share" min="0" max="100" step="5" value={settings.cpu_percent} disabled={busy||loading} onChange={e=>update('cpu_percent',Number(e.target.value))}/><small>{selected.engines[0]==='exl3'?(selected.ngram?'Expert placement uses system RAM; n-gram residency is controlled separately above.':'Experts run in system RAM.'):'Higher values move more work or weights to system RAM.'}</small></label><label className="field">CPU threads<input type="number" min="1" max="64" value={settings.cpu_threads} onChange={e=>update('cpu_threads',Number(e.target.value))}/></label><label className="field">Prompt chunk size<select value={settings.chunk_size} onChange={e=>update('chunk_size',Number(e.target.value))}>{[256,512,1024,2048,4096].map(n=><option key={n}>{n}</option>)}</select></label><label className="field">Temperature <span className="range-value">{settings.temperature.toFixed(1)}</span><input type="range" min="0" max="2" step="0.1" value={settings.temperature} onChange={e=>update('temperature',Number(e.target.value))}/></label></div></details>
        </div><div className="load-area">{loading?<><div className="load-progress"><LoaderCircle size={16} className="spin"/><span>{loadTarget?'Preparing model…':session?.state==='stopping'?'Unloading model…':`Loading · ${number(session?.elapsed_seconds,0)}s`}</span></div><p className="load-log" title={loadTarget?undefined:session?.latest_log}>{loadTarget?'Freeing memory for the requested model…':session?.latest_log||'Preparing engine…'}</p><button className="secondary full" onClick={unload} disabled={acting}>Cancel load</button></>:<><button className="primary full" onClick={load} disabled={acting||busy||!installed||!!selected.issues.length}>{acting?<LoaderCircle className="spin" size={17}/>:<Play size={16}/>} {active?(dirty?'Apply and reload':'Reload model'):'Load model'}</button>{active&&<button className="unload-button" onClick={unload} disabled={acting||busy}><Square size={11}/>Unload model</button>}</>}
        {!installed&&!closed&&status&&<p className="warning">This engine is not installed yet.</p>}<div className="settings-note"><CircleHelp size={13}/><span>{dirty?'Settings changed. Choose Apply and reload to use them.':'Thinking, output length and temperature apply to the next message. Other settings need a reload.'}</span></div></div></aside>
      </div>}
      {view==='benchmarks'?<Benchmarks onResultOpened={()=>setBenchmarkResult(null)} requestedResult={benchmarkResult} session={session} speedResults={benchmarks} busy={!!busy||acting} request={api} copy={writeClipboard} onRunSpeed={runBenchmark} onWorkbench={()=>setView('workbench')} onRefresh={()=>api('/api/status').then(setStatus)} onUseSpeed={result=>{const model=models.find(m=>m.id===result.model.id);if(model){choose(model,result.settings);setToast('Benchmark settings applied. Load to use them.')}}}/>:view==='profiles'?<main className="page profiles-page"><div className="page-heading"><div><div className="eyebrow">YOUR EVERYDAY SETUPS</div><h1>Saved profiles</h1><p>Find your model, compare measured speeds, and choose the context that fits.</p></div><button className="primary" disabled={!settings} onClick={()=>openSave('create')}><Plus size={17}/>Save current setup</button></div>
        <div className="profile-toolbar"><button className={!showDeleted?'chosen':''} onClick={()=>setShowDeleted(false)}>Saved <span>{profiles.length}</span></button><button className={showDeleted?'chosen':''} onClick={()=>setShowDeleted(true)}><Trash2 size={14}/>Recently deleted <span>{deletedProfiles.length}</span></button></div>
        {(showDeleted?deletedProfiles:profiles).length===0?<div className="empty-page"><Save size={38}/><h2>{showDeleted?'Nothing in Recently deleted.':'Keep a setup you like.'}</h2><p>{showDeleted?'Deleted profiles can be restored here.':'Select a model, adjust its settings, then choose Save profile.'}</p>{!showDeleted&&<button className="secondary" onClick={()=>setView('workbench')}>Open workbench</button>}</div>:<ProfileList profiles={showDeleted?deletedProfiles:profiles} models={models} trash={showDeleted} busy={!!busy||loading||acting} editingId={editingId} copiedId={copiedProfileId}
          onLoad={p=>useProfile(p,true)} onEdit={p=>useProfile(p)} onRename={p=>openSave('rename',p)} onDuplicate={duplicateProfile} onDelete={removeProfile} onRestore={restoreProfile}
          onCopy={copyProfileConfig} onReorder={reorderProfiles} onBenchmarks={()=>setView('benchmarks')}/>}</main>:!selected?<main className="page empty-page"><LoaderCircle size={30} className="spin"/><h2>Opening your local library…</h2></main>:null}
      <footer className="statusbar"><span><span className={ready?'local-indicator':'idle-indicator'}/>{session?.engine==='exl3'?'ExLlamaV3 1.5.0':session?.engine==='gguf'?'llama.cpp':session?.engine||'Ready when you are'}</span><span>{session?.model?.title||'Your models stay on this computer'}</span><button onClick={()=>{setLogs([]);api('/api/logs').then(d=>setLogs(d.lines))}}><Terminal size={12}/>Engine log</button></footer>
    </div>
    <BenchmarkLive session={session} request={api} copy={writeClipboard} onResults={(kind,id)=>{setBenchmarkResult({kind,id});setView('benchmarks')}}/>

    {toast&&<div className="toast" role="status"><Check size={17}/><span>{toast}</span>{undoProfile&&<button onClick={()=>restoreProfile(undoProfile)}>Undo</button>}</div>}
    {logs!==null&&<Modal title="Engine log" onClose={()=>setLogs(null)} className="logs-dialog"><p className="dialog-description">Live output from the local inference engine.</p><pre className="engine-log">{logs.length?logs.join('\n'):'No engine output yet. Load a model to begin.'}</pre></Modal>}
    {saveOpen&&<Modal title={saveMode==='create'?'Save profile':saveMode==='rename'?'Rename profile':'Save changes'} onClose={()=>{if(!acting)setSaveOpen(false)}} className="profile-dialog"><p className="dialog-description">{saveMode==='rename'?'Give this setup a name that is easy to recognise.':saveMode==='update'?'Update this saved profile with the settings in your workbench.':'Keep this model and its settings together for next time.'}</p><form onSubmit={e=>{e.preventDefault();save()}}><label className="field">Profile name<input autoFocus required value={profileName} maxLength={180} placeholder="e.g. DeepSeek · everyday chat" onChange={e=>{setProfileName(e.target.value);setAutoProfileName(false)}}/></label><label className="auto-name-option"><input type="checkbox" checked={autoProfileName} onChange={e=>{setAutoProfileName(e.target.checked);if(e.target.checked){const targetSettings=saveMode==='rename'?saveTarget?.settings:settings;const targetModel=models.find(m=>m.id===targetSettings?.model_id);if(targetSettings&&targetModel)setProfileName(suggestedProfileName(targetModel,targetSettings))}}}/>Automatic compact name · updates with benchmark results</label>{saveMode!=='rename'&&settings&&<div className="save-summary"><strong>{selected?.title}</strong><span>{short(settings.context)} context · {settings.kv} cache · {settings.cpu_percent}% CPU</span><span>{settings.vision?'Vision on':'Text only'} · MTP {settings.prediction==='mtp'?settings.draft_tokens:'off'}</span></div>}{profileError&&<div role="alert" className="dialog-error">{profileError}</div>}<div className="dialog-actions"><button type="button" className="secondary" disabled={acting} onClick={()=>setSaveOpen(false)}>Cancel</button><button type="submit" className="primary" disabled={!profileName.trim()||acting}>{acting?<LoaderCircle size={16} className="spin"/>:<Save size={16}/>} {saveMode==='create'?'Save profile':saveMode==='rename'?'Rename profile':'Save changes'}</button></div></form></Modal>}

  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
