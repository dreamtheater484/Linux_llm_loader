import React, {useEffect, useState} from 'react';
import {ArrowLeft, ArrowUpRight, Check, CheckCircle2, ChevronDown, Cpu, FolderOpen, HardDrive, Info, KeyRound, LoaderCircle, Monitor, Moon, Network, Palette, Plug, RefreshCw, RotateCcw, Save, Settings2, ShieldCheck, Sun, Trash2} from 'lucide-react';
import {useTheme} from './ui';
import './settings.css';

export type SettingsSection = 'network'|'engines'|'library'|'comfyui'|'appearance';
type Values = {network_enabled:boolean;network_interface:string;port:number;model_root:string;gguf_server:string;exl_python:string;tabby_dir:string;comfyui_enabled:boolean;comfyui_mode:'docker'|'api';comfyui_url:string;comfyui_container:string};
type Snapshot = {values:Values;revision:string;interfaces:{name:string;host:string;subnet:string}[];active:{url:string;lan_network:string};next_url:string;next_subnet:string;restart_required:boolean;storage:{config:string;runtime:string;data:string}};
const sections = [
  {id:'network',label:'Network',detail:'Use on other devices',icon:Network},
  {id:'engines',label:'Engines',detail:'Locations & versions',icon:Cpu},
  {id:'library',label:'Model library',detail:'Folders & storage',icon:FolderOpen},
  {id:'comfyui',label:'ComfyUI',detail:'Share GPU memory',icon:Plug},
  {id:'appearance',label:'Appearance',detail:'Light or dark',icon:Palette},
] as const;
const themes = [
  {id:'system',label:'Match this device',detail:'Follows your operating system’s light or dark setting.',icon:Monitor},
  {id:'light',label:'Light',detail:'Bright surfaces, best in well-lit rooms.',icon:Sun},
  {id:'dark',label:'Dark',detail:'Easy on the eyes for long evening sessions.',icon:Moon},
] as const;

function Switch({label,checked,onChange,disabled}:{label:string;checked:boolean;onChange:(value:boolean)=>void;disabled?:boolean}) {
  return <button type="button" role="switch" aria-label={label} aria-checked={checked} disabled={disabled} className={`toggle ${checked?'on':''}`} onClick={()=>onChange(!checked)}><span/></button>;
}
function Card({title,description,children}:{title:string;description?:string;children:React.ReactNode}) {
  return <section className="preferences-card"><header><h3>{title}</h3>{description&&<p>{description}</p>}</header>{children}</section>;
}

type HubStatus = {connected:boolean;source:'environment'|'inflect'|'huggingface-cli'|null;masked:string|null;user:string|null;role:string|null;error:string|null;file:string};
const SOURCES = {environment:'the HF_TOKEN environment variable', inflect:'Inflect', 'huggingface-cli':'your huggingface-cli login'};

/** Optional Hugging Face token: unlocks gated and private models and gives downloads
    a higher rate limit. Stored outside the project folder; never sent back to the page. */
function HubAccessCard({request,visible}:{request:(path:string,body?:unknown,method?:string)=>Promise<any>;visible:boolean}) {
  const [status,setStatus]=useState<HubStatus|null>(null),[token,setToken]=useState(''),[working,setWorking]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false);
  useEffect(()=>{if(visible&&!status)request('/api/huggingface').then(setStatus).catch(e=>setError(e.message))},[visible]);
  const run=async(action:()=>Promise<HubStatus>,done?:()=>void)=>{setWorking(true);setError('');setSaved(false);try{setStatus(await action());done?.()}catch(e){setError((e as Error).message)}finally{setWorking(false)}};
  const save=()=>run(()=>request('/api/huggingface',{token:token.trim()}),()=>{setToken('');setSaved(true)});
  const editable=!status||status.source!=='environment';
  return <Card title="Hugging Face access" description="Optional. A free read token lets Discover show and download gated models (after you accept their licence on Hugging Face) and your own private models, and gives downloads a higher rate limit.">
    {!status?<p className="settings-help"><LoaderCircle size={15} className="spin"/>Checking…</p>:status.connected?
      <div className={`hub-status ${status.error?'problem':''}`}>{status.error?<Info size={18}/>:<ShieldCheck size={18}/>}<div><strong>{status.error?'Token not working':status.user?`Signed in as ${status.user}`:'Token saved'}</strong><small>{status.error||<>Token {status.masked}{status.role?` · ${status.role} access`:''} · from {SOURCES[status.source!]}</>}</small></div>
        {status.source==='inflect'&&<button className="secondary small" disabled={working} onClick={()=>run(()=>request('/api/huggingface/remove',{}))}><Trash2 size={13}/>Remove</button>}</div>
      :<p className="settings-help"><Info size={15}/>No token yet. Public models work without one.</p>}
    {saved&&<p className="settings-help" role="status"><CheckCircle2 size={15}/>Token checked with Hugging Face and saved.</p>}
    {editable&&<form className="hub-token-form" onSubmit={e=>{e.preventDefault();if(token.trim())save()}}>
      <label className="field">{status?.connected?'Replace token':'Access token'}<input type="password" autoComplete="off" spellCheck={false} placeholder="hf_…" value={token} onChange={e=>{setToken(e.target.value);setError('')}} disabled={working}/><small>Create one at huggingface.co → Settings → Access Tokens. “Read” access is enough.</small></label>
      <button className="primary" type="submit" disabled={working||token.trim().length<8}>{working?<LoaderCircle size={15} className="spin"/>:<KeyRound size={15}/>}Check and save</button>
    </form>}
    {status?.source==='environment'&&<p className="settings-help"><Info size={15}/>Set by the HF_TOKEN environment variable, which takes priority. Change it where Inflect is started.</p>}
    {error&&<p className="settings-help hub-error" role="alert"><Info size={15}/>{error}</p>}
    <p className="settings-help"><KeyRound size={15}/><span>Stored only on this computer, readable by your user account only, and outside the Inflect folder so it can never end up in git: <code>{status?.file||'~/.config/inflect/huggingface-token'}</code></span></p>
  </Card>;
}

export function ApplicationSettings({visible,section,onSection,request,copy,modelSettings,modelName,onBack,onRescan,busy,modelLoaded,onRestart,locations=[],onAddLocation}:{visible:boolean;section:SettingsSection;onSection:(section:SettingsSection)=>void;request:(path:string,body?:unknown,method?:string)=>Promise<any>;copy:(value:string)=>Promise<void>;modelSettings:React.ReactNode;modelName?:string;onBack:()=>void;onRescan:()=>Promise<void>;busy:boolean;modelLoaded:boolean;onRestart:()=>void;locations?:string[];onAddLocation?:()=>void}) {
  const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[draft,setDraft]=useState<Values|null>(null);
  const [engines,setEngines]=useState<any[]>([]),[updateJob,setUpdateJob]=useState<any>(null);
  const [discovery,setDiscovery]=useState<any>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [working,setWorking]=useState(false),[discovering,setDiscovering]=useState(false),[restartUrl,setRestartUrl]=useState('');
  const [theme,setTheme]=useTheme();
  const dirty=!!draft&&!!snapshot&&JSON.stringify(draft)!==JSON.stringify(snapshot.values);
  const locked=working||!!updateJob?.active||!!restartUrl;
  const assign=(data:Snapshot)=>{setSnapshot(data);setDraft(data.values)};
  const reload=async()=>{setError('');try{assign(await request('/api/settings'))}catch(e){setError((e as Error).message)}};
  const checkEngines=async()=>{const data=await request('/api/settings/engines');setEngines(data.engines);setUpdateJob(data.update);return data};
  useEffect(()=>{if(visible&&!snapshot)reload()},[visible]);
  useEffect(()=>{if(!visible||(section!=='engines'&&!updateJob?.active))return;let disposed=false,running=false;
    const poll=async()=>{if(running)return;running=true;try{const data=await request('/api/settings/engines');if(!disposed){setEngines(data.engines);setUpdateJob(data.update)}}catch(e){if(!disposed)setError((e as Error).message)}finally{running=false}};
    poll();const timer=setInterval(poll,3000);return()=>{disposed=true;clearInterval(timer)};
  },[visible,section,updateJob?.active]);
  useEffect(()=>{if(!dirty)return;const handler=(e:BeforeUnloadEvent)=>{e.preventDefault()};window.addEventListener('beforeunload',handler);return()=>window.removeEventListener('beforeunload',handler)},[dirty]);
  useEffect(()=>{if(updateJob?.state==='complete'&&!dirty)reload()},[updateJob?.state]);
  const edit=<K extends keyof Values>(key:K,value:Values[K])=>{setDraft(old=>old?{...old,[key]:value}:old);setNotice('');setError('')};
  const act=async(action:()=>Promise<void>)=>{setWorking(true);setError('');setNotice('');try{await action()}catch(e){setError((e as Error).message)}finally{setWorking(false)}};
  const save=()=>act(async()=>{if(!snapshot||!draft)return;const data=await request('/api/settings',{revision:snapshot.revision,values:draft},'PUT');assign(data);setNotice(data.restart_required?'Saved. Restart Inflect to apply the changes shown below.':'Settings saved. ComfyUI changes apply to the next model load.')});
  const discover=async()=>{setDiscovering(true);setError('');try{
    const data=await request('/api/settings/comfyui/discover');setDiscovery(data);
    setDraft(old=>old&&!old.comfyui_container&&data.containers.length===1?{...old,comfyui_container:data.containers[0].name}:old);
  }catch(e){setError((e as Error).message)}finally{setDiscovering(false)}};
  useEffect(()=>{if(visible&&section==='comfyui'&&!discovery)discover()},[visible,section]);
  const pathField=(key:'model_root'|'gguf_server'|'exl_python'|'tabby_dir',label:string,help?:string)=><label className="field" key={key}>{label}<input spellCheck={false} autoComplete="off" aria-label={label} value={draft?.[key]||''} onChange={e=>edit(key,e.target.value)} disabled={locked}/>{help&&<small>{help}</small>}</label>;
  const selectedInterface=snapshot?.interfaces.find(item=>item.name===draft?.network_interface)||(!draft?.network_interface?snapshot?.interfaces[0]:undefined);
  const plannedUrl=draft?`http://${draft.network_enabled&&selectedInterface?selectedInterface.host:'127.0.0.1'}:${draft.port}`:'';
  const meta=sections.find(item=>item.id===section)!;
  return <main className="preferences-page" aria-label="Inflect settings">
    <div className="preferences-heading"><div><h1>Settings</h1><p>A few thoughtful controls. Everything is stored on this computer.</p></div><button className="secondary" onClick={onBack}><ArrowLeft size={15}/>Back to chat</button></div>
    <div className="preferences-layout"><nav className="preferences-nav" aria-label="Settings sections">{sections.map(({id,label,detail,icon:Icon})=><button key={id} aria-current={section===id?'page':undefined} onClick={()=>onSection(id)}><Icon size={18}/><span><strong>{label}</strong><small>{detail}</small></span></button>)}<p><HardDrive size={13}/>Saved on this computer</p></nav>
      <div className="preferences-content"><div className="preferences-section-title"><meta.icon size={21}/><div><h2>{meta.label}</h2><p>{meta.detail}</p></div></div>
        {error&&<div className="preferences-alert error" role="alert"><Info size={17}/><span>{error}</span><button onClick={reload} disabled={locked}>Reload settings</button></div>}
        {notice&&<div className="preferences-alert success" role="status"><CheckCircle2 size={17}/><span>{notice}</span></div>}
        {restartUrl?<Card title="Inflect is restarting" description="The loaded model is being unloaded. Give Inflect a moment to reopen."><p className="settings-help">If you turned LAN access off, open Inflect on the computer running it.</p><a className="primary restart-link" href={restartUrl}>Reopen Inflect<ArrowUpRight size={15}/></a><code className="settings-path">{restartUrl}</code></Card>:<>
        {snapshot?.restart_required&&<div className="preferences-alert restart"><RotateCcw size={18}/><span><strong>Restart to apply saved changes</strong><small>This unloads the current model. Next address: {snapshot.next_url}</small></span><button className="secondary" disabled={locked||dirty||busy} onClick={()=>act(async()=>{const data=await request('/api/settings/restart',{});setRestartUrl(data.url);onRestart()})}>Restart Inflect</button></div>}
        {!draft||!snapshot?<p className="settings-help"><LoaderCircle size={16} className="spin"/>Loading your settings…</p>:<>
        {section==='network'&&<>
          <Card title="Use Inflect on other devices" description="Open Inflect from another computer, tablet or phone on the same home or office network (LAN).">
            <div className="preferences-switch"><div><strong>Allow network access</strong><p>On by default. Switch off to use Inflect on this computer only.</p></div><Switch label="Allow network access" checked={draft.network_enabled} onChange={value=>edit('network_enabled',value)} disabled={locked}/></div>
            <div className={`connection-card ${draft.network_enabled?'enabled':''}`}><Network size={23}/><div><span>{draft.network_enabled&&selectedInterface?'LAN address after applying changes':'Address on this computer'}</span><strong>{plannedUrl}</strong><small>{draft.network_enabled&&selectedInterface?`${selectedInterface.name} · only ${selectedInterface.subnet}`:draft.network_enabled?'No private LAN detected. Inflect will stay local until one is available.':'Other devices will not be able to connect.'}</small></div><button className="secondary" onClick={()=>act(async()=>{await copy(plannedUrl);setNotice('Address copied')})}>Copy</button></div>
            <p className="settings-help"><Info size={15}/>Only turn this on for a network you trust: any device on it can open your chats and controls. Inflect is never reachable from the internet — it does not open router ports or public addresses.</p>
            <details className="preferences-advanced"><summary>Connection details<ChevronDown size={15}/></summary><div className="preferences-grid"><label className="field">Network interface<select disabled={locked||!draft.network_enabled} value={draft.network_interface} onChange={e=>edit('network_interface',e.target.value)}><option value="">Automatic · recommended</option>{snapshot.interfaces.map(item=><option key={item.name+item.host} value={item.name}>{item.name} · {item.host}</option>)}{draft.network_interface&&!snapshot.interfaces.some(item=>item.name===draft.network_interface)&&<option value={draft.network_interface}>{draft.network_interface} · unavailable</option>}</select><small>Detected from your private network connection.</small></label><label className="field">Port<input type="number" min={1024} max={65535} value={draft.port} disabled={locked} onChange={e=>edit('port',Number(e.target.value))}/><small>The number at the end of the address. Keep 7860 unless another app uses it.</small></label></div><p className="settings-help">Currently serving: {snapshot.active.url} · {snapshot.active.lan_network||'this computer only'}. Network changes need a restart.</p></details>
          </Card>
        </>}
        {section==='engines'&&<>
          <div className="settings-help engine-explanation"><p>Engine locations are filled in from your installation. Updates use the versions tested with this Inflect checkout, including the matching GPU dependencies.</p><button className="secondary" disabled={locked} onClick={()=>act(async()=>{await checkEngines();setNotice('Installed engines and supported versions refreshed.')})}><RefreshCw size={14}/>Refresh versions</button></div>
          {engines.map(engine=><Card key={engine.id} title={engine.name} description={engine.id==='gguf'?'Runs GGUF models':'Runs EXL3 models through TabbyAPI'}><div className="engine-versions"><div><span>Installed</span><strong>{engine.version}</strong></div><div><span>Supported by Inflect</span><strong>{engine.supported_version}</strong></div></div><div className="engine-status"><span className={`engine-badge ${engine.supported?'current':''}`}>{engine.supported?<><Check size={13}/>Supported version installed</>:engine.managed?'Supported version available':'Custom installation'}</span>{engine.update_available&&<button className="secondary" disabled={locked||busy||modelLoaded||dirty} onClick={()=>act(async()=>{const job=await request(`/api/settings/engines/${engine.id}/update`,{});setUpdateJob(job)})}><RefreshCw size={14}/>{engine.update_label}</button>}</div><code className="settings-path">{engine.path}</code>{!engine.managed&&<p className="settings-help">This location is managed outside Inflect. Update it with its own installer, then refresh versions.</p>}</Card>)}
          {(busy||modelLoaded)&&<p className="settings-help"><Info size={15}/>Unload the model (in Chat or Models) before updating an engine.</p>}
          {updateJob&&updateJob.state!=='idle'&&<Card title={updateJob.active?'Updating engine…':updateJob.state==='complete'?'Engine update complete':'Engine update failed'}><p role="status" className="settings-help">{updateJob.error||(updateJob.active?'You can leave this page open while the supported runtime is prepared.':'The next load or restart will use the installed version.')}</p><details open={updateJob.state==='error'}><summary>Update log</summary><pre className="settings-update-log">{updateJob.lines.join('\n')}</pre></details></Card>}
          <Card title="Engine locations" description="Advanced: point Inflect at an existing installation on this computer."><details className="preferences-advanced"><summary>Change engine locations<ChevronDown size={15}/></summary>{pathField('gguf_server','llama.cpp executable','The llama-server executable, not its folder.')}{pathField('exl_python','ExLlama Python executable','The Python inside the environment containing ExLlamaV3.')}{pathField('tabby_dir','TabbyAPI folder','The folder containing TabbyAPI’s main.py.')}<p className="settings-help">Location changes take effect after restarting Inflect.</p></details></Card>
        </>}
        {section==='appearance'&&<Card title="Theme" description="Choose how Inflect looks in this browser. You can also switch quickly with the sun and moon button at the top of the window."><div className="theme-choices" role="radiogroup" aria-label="Theme">{themes.map(({id,label,detail,icon:Icon})=><label key={id} className={`preference-choice theme-choice ${theme===id?'selected':''}`}><input type="radio" name="theme" value={id} checked={theme===id} onChange={()=>setTheme(id)}/><span className={`theme-swatch ${id}`} aria-hidden="true"><Icon size={16}/></span><span><strong>{label}</strong><small>{detail}</small></span></label>)}</div><p className="settings-help"><Info size={15}/>Saved instantly for this browser only; other devices keep their own choice.</p></Card>}
        {section==='library'&&<>
          <Card title="Your model folder" description="Inflect finds compatible models in this folder and its subfolders. Downloads from Discover are saved here, each in its own folder.">{pathField('model_root','Model folder','A folder on the computer running Inflect. A new location needs a restart.')}<div className="preferences-row"><p className="settings-help">Added new models to the current folder?</p><button className="secondary" disabled={locked||dirty} onClick={()=>act(async()=>{await onRescan();setNotice('Model library rescanned.')})}><RefreshCw size={14}/>Rescan library</button></div></Card>
          <Card title="Other locations" description="Models kept elsewhere on this computer, used where they are.">{locations.length?locations.map(path=><div className="storage-location" key={path}><span>Added</span><code className="settings-path">{path}</code></div>):<p className="settings-help">No other locations yet.</p>}<div className="preferences-row"><p className="settings-help">Nothing is copied or moved when you add a location.</p><button className="secondary" onClick={onAddLocation}><FolderOpen size={14}/>Add or remove locations</button></div></Card>
          <HubAccessCard request={request} visible={visible}/>
          <Card title="Application storage" description="These locations are managed by Inflect.">{([['Engines & dependencies',snapshot.storage.runtime],['Chats, profiles & results',snapshot.storage.data],['Settings file',snapshot.storage.config]]).map(([name,path])=><div className="storage-location" key={name}><span>{name}</span><code className="settings-path">{path}</code></div>)}<p className="settings-help">Conversations autosave. Delete or export a conversation from its chat menu.</p></Card>
        </>}
        {section==='comfyui'&&<>
          <Card title="Share GPU memory with ComfyUI" description="Free ComfyUI’s GPU memory before loading an LLM."><div className="preferences-switch"><div><strong>ComfyUI integration</strong><p>When off, Inflect leaves ComfyUI alone.</p></div><Switch label="ComfyUI integration" checked={draft.comfyui_enabled} disabled={locked} onChange={value=>edit('comfyui_enabled',value)}/></div>
            <fieldset className="comfy-options" disabled={!draft.comfyui_enabled||locked}><legend>How should memory be released?</legend><label className={`preference-choice ${draft.comfyui_mode==='docker'?'selected':''}`}><input type="radio" name="comfy-mode" value="docker" checked={draft.comfyui_mode==='docker'} onChange={()=>edit('comfyui_mode','docker')}/><span><strong>Stop and restart its Docker container</strong><small>Releases all GPU memory. Waits for queued jobs, loads the LLM, then restarts ComfyUI with empty caches.</small></span></label><label className={`preference-choice ${draft.comfyui_mode==='api'?'selected':''}`}><input type="radio" name="comfy-mode" value="api" checked={draft.comfyui_mode==='api'} onChange={()=>edit('comfyui_mode','api')}/><span><strong>Unload models through its API</strong><small>Keeps ComfyUI running. Its CUDA context may still reserve some memory.</small></span></label>
            {draft.comfyui_mode==='docker'&&<label className="field">Docker container<input list="comfy-containers" value={draft.comfyui_container} onChange={e=>edit('comfyui_container',e.target.value)} placeholder="Select a detected container or enter its exact name"/><datalist id="comfy-containers">{discovery?.containers.map((item:any)=><option key={item.name} value={item.name}>{item.state}</option>)}</datalist><small>{discovery?.containers.length?`${discovery.containers.length} ComfyUI container(s) detected.`:'Enter the exact container Inflect may stop and restart.'}</small></label>}
            <label className="field">ComfyUI address<input aria-label="ComfyUI address" value={draft.comfyui_url} onChange={e=>edit('comfyui_url',e.target.value)} spellCheck={false}/><small>A local address on the Inflect computer. Usually http://127.0.0.1:8188.</small></label></fieldset>
            <div className="preferences-row"><p className="settings-help">{discovering?'Looking for ComfyUI…':discovery?.detected?'ComfyUI responded at its saved local address.':'ComfyUI has not responded at its saved address.'}</p><button className="secondary" disabled={discovering||locked} onClick={discover}><RefreshCw size={14} className={discovering?'spin':''}/>Detect again</button></div>{discovery?.docker_error&&<p className="settings-help">Docker discovery unavailable. You can enter a container name manually.</p>}
          </Card>
          <p className="settings-help"><Info size={15}/>Jobs are never cancelled. Changes apply to the next model load; any container already stopped by an ongoing load will still be restored.</p>
        </>}
        </>}
        </>}
        {draft&&!restartUrl&&(section!=='appearance'||dirty)&&<div className={`preferences-save ${dirty?'unsaved':''}`}><span>{dirty?'Unsaved application settings':'Application settings are saved.'}</span><button className="secondary" disabled={!dirty||locked} onClick={()=>{setDraft(snapshot!.values);setError('');setNotice('Changes discarded')}}>Discard</button><button className="primary" disabled={!dirty||locked} onClick={save}>{working?<LoaderCircle size={15} className="spin"/>:<Save size={15}/>}Save changes</button></div>}
      </div>
    </div>
  </main>;
}
