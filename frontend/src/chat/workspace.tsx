import {useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowDown,ArrowUp,ArrowUpRight,Check,ChevronDown,Copy,Download,FileText,GitBranch,History,LoaderCircle,Maximize2,MessageSquare,Minimize2,MoreHorizontal,Paperclip,Pencil,Plus,RotateCcw,Search,Settings2,Sparkles,Square,Trash2,Upload,X} from 'lucide-react';
import type {Model,Profile,Settings} from '../types';
import {InflectMark} from '../brand';
import {Modal,Performance,formatNumber,hueStyle} from '../ui';
import {ThinkingSelect} from '../reasoning';
import {RichMarkdown} from './markdown';
import {PreviewPanel} from './preview';
import {acceptedFiles,prepareFile} from './files';
import {chatPath,copyText,fileUrl,request} from './types';

/** Why a reply stopped with finish_reason "length": a deliberate cap, or a full context window. */
const outputLimitNote=(metrics:any)=>{
  // Replies saved before Auto existed carry no budget; they always hit a fixed cap.
  const capped=!('output_budget' in metrics)||(metrics.output_cap&&metrics.output_budget>=metrics.output_cap);
  return capped?`Stopped at the ${metrics.output_cap?formatNumber(metrics.output_cap,0)+'-token ':''}output cap. Set Maximum output to Auto in model settings (no reload needed), then retry.`
    :'The context window is full, so the reply could not continue. Start a new conversation or reload with a larger context window.';
};
import type {Attachment,ChatSummary,Conversation,Message,Preview} from './types';
import './workspace.css';

type Props={model:Model;models:Model[];settings:Settings;session:any;active:boolean;dirty:boolean;expanded:boolean;onExpand:()=>void;onModel:(model:Model)=>void;onBusy:(busy:boolean)=>void;onPreview:(open:boolean)=>void;onSaveProfile:()=>void;onLoad:()=>void;onSettings:()=>void;profiles?:Profile[];onProfile?:(p:Profile)=>void;setupLabel?:(p:Profile)=>string;loadedModel?:Model|null;onUseLoaded?:()=>void};
export function ChatWorkspace({model,models,settings,session,active,dirty,expanded,onExpand,onModel,onBusy,onPreview,onSaveProfile,onLoad,onSettings,profiles=[],onProfile,setupLabel,loadedModel,onUseLoaded}:Props) {
  const [chats,setChats]=useState<ChatSummary[]>([]),[doc,setDoc]=useState<Conversation|null>(null),[draft,setDraft]=useState(''),[draftFiles,setDraftFiles]=useState<string[]>([]);
  const [query,setQuery]=useState(''),[historyOpen,setHistoryOpen]=useState(false),[preview,setPreview]=useState<Preview|null>(null);
  const [streaming,setStreaming]=useState(false),[working,setWorking]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState(''),[saveState,setSaveState]=useState('Saved locally');
  const [effort,setEffort]=useState(settings.reasoning_effort),[menu,setMenu]=useState(false),[modal,setModal]=useState<'rename'|'delete'|'instructions'|'model'|'details'|null>(null),[modalText,setModalText]=useState('');
  const [edit,setEdit]=useState<{index:number;text:string}|null>(null),[copied,setCopied]=useState(''),[dragging,setDragging]=useState(false),[showLatest,setShowLatest]=useState(false);
  const current=useRef<Conversation|null>(null),draftRef=useRef(''),filesRef=useRef<string[]>([]),saveTail=useRef<Promise<unknown>>(Promise.resolve());
  const streamController=useRef<AbortController|null>(null),streamPromise=useRef<Promise<void>|null>(null),streamRef=useRef(false),disposed=useRef(false);
  const fileInput=useRef<HTMLInputElement>(null),importInput=useRef<HTMLInputElement>(null),composer=useRef<HTMLTextAreaElement>(null),scroll=useRef<HTMLDivElement>(null),content=useRef<HTMLDivElement>(null),follow=useRef(true),searchRef=useRef<HTMLInputElement>(null),listSequence=useRef(0);
  const busy=working||streaming||!!doc?.generating;
  const blocked=busy||session?.busy||session?.state==='loading'||session?.state==='stopping';
  const install=useCallback((value:Conversation,restoreDraft=true)=>{
    current.current=value;setDoc(value);
    if(restoreDraft){draftRef.current=value.draft;filesRef.current=value.draft_attachments;setDraft(value.draft);setDraftFiles(value.draft_attachments)}
    try{localStorage.setItem('inflect.active-chat',value.id)}catch{}
  },[]);
  const refreshList=useCallback(async(q='')=>{const sequence=++listSequence.current;const list=await request<ChatSummary[]>('/api/conversations/search',{query:q});if(!disposed.current&&sequence===listSequence.current)setChats(list);return list},[]);
  const openPreview=useCallback((value:Preview|null)=>{setPreview(value);onPreview(!!value)},[onPreview]);
  const fail=(value:unknown)=>{setError(value instanceof Error?value.message:String(value));setSaveState('Check required')};
  const action=async(fn:()=>Promise<void>)=>{if(working)return;setWorking(true);setError('');try{await fn()}catch(e){fail(e)}finally{setWorking(false)}};

  // Serialize draft writes. Every request uses the latest acknowledged revision;
  // an older response must never replace more recent typing in the composer.
  const saveNow=useCallback(async(extra:Record<string,unknown>={})=>{
    const id=current.current?.id;if(!id||streamRef.current)return;
    const snapshot={draft:draftRef.current,draft_attachments:[...filesRef.current],...extra};
    const run=saveTail.current.catch(()=>{}).then(async()=>{
      const latest=current.current;if(!latest||latest.id!==id)return;
      if(!Object.keys(extra).length&&latest.draft===snapshot.draft&&JSON.stringify(latest.draft_attachments)===JSON.stringify(snapshot.draft_attachments))return;
      setSaveState('Saving…');
      const saved=await request<Conversation>(chatPath(id),{revision:latest.revision,...snapshot},'PATCH');
      if(current.current?.id===id){install(saved,false);setSaveState('Saved locally')}
    });
    saveTail.current=run;await run;
  },[install]);
  useEffect(()=>{disposed.current=false;(async()=>{try{const list=await refreshList();let id:string|null=null;try{id=localStorage.getItem('inflect.active-chat')}catch{}const selected=list.find(c=>c.id===id)||list[0];const value=selected?await request<Conversation>(chatPath(selected.id)):await request<Conversation>('/api/conversations',{model_id:model.id});if(!disposed.current){install(value);await refreshList()}}catch(e){if(!disposed.current)fail(e)}finally{if(!disposed.current)setLoading(false)}})();return()=>{disposed.current=true;streamController.current?.abort()}},[]);
  useEffect(()=>{const timer=setTimeout(()=>{refreshList(query).catch(fail)},220);return()=>clearTimeout(timer)},[query]);
  useEffect(()=>{if(!doc||streaming||doc.generating)return;const timer=setTimeout(()=>saveNow().catch(fail),500);return()=>clearTimeout(timer)},[draft,draftFiles,doc?.id,streaming]);
  useEffect(()=>{setEffort(settings.reasoning_effort)},[model.id,settings.reasoning_effort]);
  useEffect(()=>{onBusy(streaming);streamRef.current=streaming},[streaming]);
  useEffect(()=>{if(!doc?.generating||streaming)return;const id=doc.id;const timer=setInterval(()=>request<Conversation>(chatPath(id)).then(value=>{if(current.current?.id===id){install(value,false);if(!value.generating)refreshList(query)}}).catch(fail),1200);return()=>clearInterval(timer)},[doc?.generating,doc?.id,streaming]);
  useEffect(()=>{const handler=(e:BeforeUnloadEvent)=>{const value=current.current;if(value&&(draftRef.current!==value.draft||JSON.stringify(filesRef.current)!==JSON.stringify(value.draft_attachments))){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',handler);return()=>window.removeEventListener('beforeunload',handler)},[]);
  const fitComposer=useCallback(()=>{const el=composer.current;if(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,180)+'px'}},[]);
  useEffect(fitComposer,[draft,doc?.id,loading]);
  // Re-measure when the box changes width (first layout, preview panel, window resize).
  useEffect(()=>{const box=composer.current?.parentElement;if(!box)return;let width=box.clientWidth;const observer=new ResizeObserver(()=>{if(box.clientWidth!==width){width=box.clientWidth;fitComposer()}});observer.observe(box);fitComposer();return()=>observer.disconnect()},[loading,fitComposer]);
  const latest=()=>{follow.current=true;setShowLatest(false);if(scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight};
  useLayoutEffect(()=>{if(follow.current&&scroll.current)scroll.current.scrollTop=current.current?.messages.length?scroll.current.scrollHeight:0},[doc?.messages,streaming]);
  useEffect(()=>{if(!content.current)return;const observer=new ResizeObserver(()=>{if(follow.current&&scroll.current)scroll.current.scrollTop=current.current?.messages.length?scroll.current.scrollHeight:0});observer.observe(content.current);return()=>observer.disconnect()},[doc?.id,loading]);
  useEffect(()=>{const handler=(event:KeyboardEvent)=>{
    const target=event.target as HTMLElement;if(target?.closest('[hidden]'))return;
    if((event.ctrlKey||event.metaKey)&&event.key==='k'){event.preventDefault();setHistoryOpen(true);setTimeout(()=>searchRef.current?.focus(),0)}
    if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.key.toLowerCase()==='o'&&!busy){event.preventDefault();newChat()}
    if(event.key==='Escape'){setMenu(false);if(preview)openPreview(null);else if(historyOpen)setHistoryOpen(false)}
  };window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)},[busy,preview,historyOpen,model.id]);

  useEffect(()=>{
    if(!('BroadcastChannel' in window))return;
    const channel=new BroadcastChannel('inflect-chats');channel.onmessage=event=>{
      if(event.data?.deleted===current.current?.id){current.current=null;setDoc(null);draftRef.current='';filesRef.current=[];setDraft('');setDraftFiles([]);setPreview(null);onPreview(false);setError('This conversation was permanently deleted in another window. Start a new conversation to continue.');setModal(null)}
      refreshList(query).catch(()=>{});
    };return()=>channel.close();
  },[query]);

  const navigate=async(id:string)=>{await saveNow();const value=await request<Conversation>(chatPath(id));install(value);setError('');setSaveState('Saved locally');setEdit(null);openPreview(null);follow.current=true;setShowLatest(false);setHistoryOpen(false)};
  const newChat=()=>action(async()=>{if(streamRef.current)return;await saveNow();const value=await request<Conversation>('/api/conversations',{model_id:model.id});install(value);openPreview(null);setEdit(null);setSaveState('Saved locally');setHistoryOpen(false);follow.current=true;await refreshList(query);composer.current?.focus()});
  const openFile=async(file:Attachment)=>{
    if(!doc)return;
    const url=fileUrl(doc.id,file.id);openPreview({name:file.name,kind:file.kind,url,downloadUrl:url+'?download=true'});
  };
  const attach=(files:File[])=>action(async()=>{
    if(!current.current||streamRef.current)return;if(filesRef.current.length+files.length>20)throw new Error('Attach up to 20 files per message.');
    for(const file of files){
      const payload=await prepareFile(file);const id=current.current!.id;
      const attachment=await request<Attachment>(`${chatPath(id)}/attachments`,payload);
      const next={...current.current!,files:[...current.current!.files,attachment]};install(next,false);
      filesRef.current=[...filesRef.current,attachment.id];setDraftFiles(filesRef.current);await saveNow();
      if(payload.kind==='pdf'&&!payload.text.trim())setError('This PDF has no selectable text. Preview it here, or attach page images to a vision model.');
    }
    composer.current?.focus();
  });
  const removeFile=(id:string)=>action(async()=>{filesRef.current=filesRef.current.filter(f=>f!==id);setDraftFiles(filesRef.current);await saveNow();const value=current.current!;
    if(!value.messages.some(m=>m.attachments.includes(id))){await request(fileUrl(value.id,id),undefined,'DELETE');install({...value,files:value.files.filter(f=>f.id!==id)},false)}
  });
  const changeDraft=(text:string)=>{draftRef.current=text;setDraft(text);setSaveState('Saving…')};
  const updateStream=(fn:(value:Conversation)=>Conversation)=>{if(current.current)install(fn(current.current),false)};

  const runGeneration=async(retry=false)=>{
    if(!current.current||!active||dirty||streamRef.current||session?.busy)return;
    setError('');await saveNow();const value=current.current!;const prompt=draftRef.current,attachments=[...filesRef.current];
    if(!retry&&!prompt.trim()&&!attachments.length)return;
    streamRef.current=true;setStreaming(true);onBusy(true);setSaveState('Saving reply…');follow.current=true;setShowLatest(false);
    const controller=new AbortController();streamController.current=controller;
    try{
      const response=await fetch(`${chatPath(value.id)}/generate`,{method:'POST',headers:{'Content-Type':'application/json','X-Inflect-Local':'1'},body:JSON.stringify({revision:value.revision,model_id:model.id,prompt,attachment_ids:attachments,retry,max_output:settings.max_output,temperature:settings.temperature,reasoning_effort:effort}),signal:controller.signal});
      if(!response.ok){const problem=await response.json();throw new Error(typeof problem.detail==='string'?problem.detail:JSON.stringify(problem.detail))}
      const reader=response.body!.getReader(),decoder=new TextDecoder();let buffer='';
      const handle=(chunk:string)=>{const data=chunk.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(!data)return;const item=JSON.parse(data);
        if(item.type==='conversation'){install(item.conversation);refreshList(query).catch(()=>{});return}
        if(item.type==='saved'){install(item.conversation);return}
        if(item.type==='error'){setError(item.message);return}
        updateStream(old=>{
          const messages=[...old.messages];const tail={...messages[messages.length-1]};if(!tail)return old;
          if(item.type==='token'){tail.content+=(item.text||'');tail.reasoning=(tail.reasoning||'')+(item.reasoning||'')}
          if(item.type==='cancelled')tail.cancelled=true;
          if(item.type==='complete')tail.metrics=item;
          messages[messages.length-1]=tail;
          return {...old,messages,used_context:item.type==='context'?item.input_tokens:item.type==='complete'?item.usage.total_tokens:old.used_context};
        });
      };
      while(true){const {value:chunk,done}=await reader.read();buffer+=done?decoder.decode():decoder.decode(chunk,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');const events=buffer.split('\n\n');buffer=events.pop()||'';events.forEach(handle);if(done){if(buffer.trim())handle(buffer);break}}
    }catch(e){if((e as Error).name!=='AbortError')fail(e)}
    finally{
      streamRef.current=false;setStreaming(false);onBusy(false);streamController.current=null;
      try{const saved=await request<Conversation>(chatPath(value.id));if(current.current?.id===value.id)install(saved);setSaveState(saved.generating?'Saving reply…':'Saved locally');await refreshList(query)}catch(e){fail(e)}
    }
  };
  const send=(retry=false)=>{const promise=runGeneration(retry).catch(fail);streamPromise.current=promise;return promise};
  const stop=async()=>{if(!current.current)return;try{await request(`${chatPath(current.current.id)}/stop`,{});await streamPromise.current}catch(e){fail(e)}};
  const forkAt=async(through:number)=>{await saveNow();const next=await request<Conversation>(`${chatPath(current.current!.id)}/fork`,{through});install(next);openPreview(null);await refreshList(query);follow.current=true;return next};
  const branch=(index:number)=>action(async()=>{await forkAt(index+1);setSaveState('Branch saved');composer.current?.focus()});
  const retry=(index:number)=>action(async()=>{const before=doc!.messages.slice(0,index);const lastUser=before.map(m=>m.role).lastIndexOf('user');if(lastUser<0)throw new Error('There is no user message to retry.');await forkAt(lastUser+1);await send(true)});
  const editAndSend=()=>action(async()=>{
    if(!edit||!current.current)return;await saveNow();
    const next=await request<Conversation>(`${chatPath(current.current.id)}/fork`,{through:edit.index+1,edited_prompt:edit.text});
    install(next);setEdit(null);openPreview(null);follow.current=true;await refreshList(query);await send(true);
  });

  const exportChat=()=>action(async()=>{await saveNow();const response=await fetch(`${chatPath(current.current!.id)}/export`);if(!response.ok)throw new Error('Could not export the conversation.');const blob=await response.blob();const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`Inflect - ${current.current!.title.replace(/[^\p{L}\p{N} _-]/gu,'').slice(0,80)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setMenu(false)});
  const importChat=(file:File)=>action(async()=>{
    if(file.size>150_000_000)throw new Error('Conversation imports must be smaller than 150 MB.');await saveNow();const parsed=JSON.parse(await file.text());
    const source=parsed.format==='inflect-chat'?parsed.conversation:parsed;
    if(!source||!Array.isArray(source.messages))throw new Error('Choose an Inflect export or a JSON conversation containing a messages list.');
    if(source.messages.length>1000)throw new Error('Imports can contain up to 1,000 messages.');
    const created=await request<Conversation>('/api/conversations',{title:(source.title||source.name||'Imported conversation').slice(0,140),model_id:source.model_id||model.id});
    try{
      const mapping=new Map<string,string>();
      for(const file of parsed.attachments||[]){const payload={name:file.name,kind:file.kind,mime:file.mime,data:file.data,text:file.text||'',image:file.kind==='image'&&file.mime!=='image/gif'?undefined:file.image||undefined};const saved=await request<Attachment>(`${chatPath(created.id)}/attachments`,payload);mapping.set(file.id,saved.id)}
      const mapped=(ids:string[]=[])=>ids.map(id=>{const mapped=mapping.get(id);if(!mapped)throw new Error('An attachment is missing from this export.');return mapped});
      const messages=source.messages.filter((m:any)=>m.role!=='system').map((m:any)=>({role:m.role,content:m.content,reasoning:m.reasoning||m.reasoning_content||'',attachments:mapped(m.attachments),model:m.model||'',cancelled:!!m.cancelled,error:!!m.error,metrics:m.metrics||null}));
      const result=await request<Conversation>(`${chatPath(created.id)}/import`,{title:created.title,model_id:created.model_id,messages,system_prompt:source.system_prompt||source.messages.find((m:any)=>m.role==='system')?.content||'',draft:source.draft||'',draft_attachments:mapped(source.draft_attachments)});
      install(result);openPreview(null);setHistoryOpen(false);setSaveState('Imported locally');await refreshList(query);
    }catch(e){await request(chatPath(created.id),undefined,'DELETE').catch(()=>{});throw e}
  });
  const deleteChat=()=>action(async()=>{const id=current.current!.id;await saveNow();await saveTail.current.catch(()=>{});await request(chatPath(id),undefined,'DELETE');try{const channel=new BroadcastChannel('inflect-chats');channel.postMessage({deleted:id});channel.close()}catch{}setModal(null);openPreview(null);current.current=null;setDoc(null);draftRef.current='';filesRef.current=[];setDraft('');setDraftFiles([]);try{localStorage.removeItem('inflect.active-chat')}catch{}const remaining=await refreshList(query);if(remaining.length)await navigate(remaining[0].id);else{const next=await request<Conversation>('/api/conversations',{model_id:model.id});install(next);await refreshList(query)}setSaveState('Conversation permanently deleted')});
  const confirmModal=()=>modal==='delete'?deleteChat():action(async()=>{await saveNow(modal==='rename'?{title:modalText.trim()}:{system_prompt:modalText});setModal(null);await refreshList(query)});
  const copy=async(message:Message)=>{try{await copyText(message.content);setCopied(message.id);setTimeout(()=>setCopied(''),1500)}catch(e){fail(e)}};
  const fileChip=(id:string,removable=false)=>{const file=doc?.files.find(f=>f.id===id);if(!file)return null;return <span className="file-chip" key={id}><button onClick={()=>openFile(file)} aria-label={`Preview ${file.name}`} title={`Preview ${file.name}`}><FileText size={14}/><span>{file.name}</span><small>{Math.max(1,Math.round(file.size/1024))} KB</small></button>{removable&&<button aria-label={`Remove ${file.name}`} disabled={busy} onClick={()=>removeFile(id)}><X size={13}/></button>}</span>};

  return <section className={`chat-workspace ${historyOpen?'history-visible':''} ${preview?'preview-visible':''}`} aria-label="Chat workspace">
    {historyOpen&&<aside className="chat-history" aria-label="Conversation history"><header><strong>Conversations</strong><button className="icon-btn" aria-label="Close history" onClick={()=>setHistoryOpen(false)}><X size={16}/></button></header><button className="primary new-conversation" disabled={busy} onClick={newChat}><Plus size={16}/>New conversation</button><label className="history-search"><Search size={15}/><input ref={searchRef} aria-label="Search conversations" placeholder="Search chats and messages" value={query} onChange={e=>setQuery(e.target.value.slice(0,300))}/></label><div className="history-list">{chats.map(chat=><button disabled={busy} key={chat.id} className={`history-item ${doc?.id===chat.id?'selected':''}`} onClick={()=>action(()=>navigate(chat.id))}><MessageSquare size={15}/><span><strong>{chat.title}</strong><small>{chat.preview||'Start something new'}</small><em>{new Date(chat.updated_at*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'})} · {chat.message_count} messages{chat.generating?' · replying':''}</em></span></button>)}{!chats.length&&<p className="history-empty">{query?'No matching conversations.':'Your conversations will appear here.'}</p>}</div><footer><button disabled={busy} onClick={()=>importInput.current?.click()}><Upload size={14}/>Import chat</button><span>Saved on this computer</span></footer></aside>}
    <div className="chat-center" onPaste={e=>{const files=Array.from(e.clipboardData.files);if(files.length&&!blocked){e.preventDefault();attach(files)}}} onDragOver={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();setDragging(true)}}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDragging(false)}} onDrop={e=>{e.preventDefault();setDragging(false);if(!blocked)attach(Array.from(e.dataTransfer.files))}}>
      <header className="chat-heading"><button className={`icon-btn history-toggle ${historyOpen?'selected':''}`} title="Conversation history · Ctrl+K" aria-label="Open conversation history" onClick={()=>setHistoryOpen(!historyOpen)}><History size={19}/></button><div className="chat-heading-title"><button className="conversation-title" aria-label="Rename conversation" disabled={busy||!doc} onClick={()=>{setModalText(doc!.title);setModal('rename')}}>{doc?.title||'Your conversations'}<Pencil size={12}/></button><button className="chat-model" disabled={blocked} title="Switch model for this conversation" style={hueStyle(model.title)} onClick={()=>setModal('model')}><span className={active?'local-indicator':'idle-indicator'}/>{model.title}<span className="chat-model-state">{active?'loaded':'not loaded'}</span><ChevronDown size={12}/></button></div><div className="chat-heading-actions"><span className="save-state" title="Conversations and files are stored locally">{working||saveState.includes('Saving')?<LoaderCircle size={12} className="spin"/>:<Check size={12}/>}<span>{saveState}</span></span><button className="icon-btn" aria-label="New conversation" title="New conversation · Ctrl+Shift+O" disabled={busy} onClick={newChat}><Plus size={19}/></button><div className="chat-menu-wrap"><button className="icon-btn" aria-label="Conversation options" onClick={()=>setMenu(!menu)}><MoreHorizontal size={19}/></button>{menu&&<><button className="menu-dismiss" aria-label="Close options" onClick={()=>setMenu(false)}/><div className="chat-menu"><button disabled={busy||!doc} onClick={()=>{setModalText(doc!.system_prompt);setModal('instructions');setMenu(false)}}><Settings2 size={14}/>Chat instructions</button><button disabled={busy||!doc} onClick={exportChat}><Download size={14}/>Export conversation</button><button disabled={busy} onClick={()=>{importInput.current?.click();setMenu(false)}}><Upload size={14}/>Import conversation</button><button onClick={()=>{setModal('details');setMenu(false)}}><FileText size={14}/>Model details</button><button onClick={()=>{onSaveProfile();setMenu(false)}}><Settings2 size={14}/>Save model profile</button><button className="danger-text" disabled={busy||!doc} onClick={()=>{setModal('delete');setMenu(false)}}><Trash2 size={14}/>Delete permanently</button></div></>}</div><button className="icon-btn expand-chat" aria-label={expanded?'Restore dashboard':'Maximize chat'} title={expanded?'Restore dashboard':'Maximize chat'} onClick={onExpand}>{expanded?<Minimize2 size={18}/>:<Maximize2 size={18}/>}</button></div></header>
      {error&&<div className="chat-error" role="alert"><span>{error}</span><button aria-label="Dismiss chat error" onClick={()=>setError('')}><X size={15}/></button></div>}
      {!active&&!dirty&&!loading&&loadedModel&&session?.state==='ready'?<div className="chat-model-notice mismatch" role="status"><span><strong>{loadedModel.title}</strong> is loaded, but <strong>{model.title}</strong> is selected and not loaded yet.{doc?.model_id===loadedModel.id?' This conversation was started with the loaded model.':''}</span><button className="use-loaded" disabled={blocked} onClick={onUseLoaded}><MessageSquare size={13}/>Continue with {loadedModel.title}</button><button disabled={blocked} onClick={onLoad}>Load {model.title} instead<ArrowUpRight size={13}/></button></div>:
      (!active||dirty)&&!loading&&<div className="chat-model-notice"><span>{dirty?'Reload to apply your model settings.':session?.state==='loading'?'Your model is loading. Draft a message while you wait.':'Load this model to continue. Your saved conversations are available below.'}</span><button disabled={blocked} onClick={onLoad}>{dirty?'Reload model':'Load model'}<ArrowUpRight size={13}/></button></div>}
      <div className="chat-stage"><div className="chat-scroll inflect-chat-scroll" ref={scroll} aria-label="Conversation messages" tabIndex={0} onScroll={e=>{const el=e.currentTarget;const near=el.scrollHeight-el.scrollTop-el.clientHeight<72;follow.current=near;setShowLatest(!near)}} onWheel={e=>{if(e.deltaY<0)follow.current=false}}><div ref={content}>
      {loading?<div className="chat-welcome"><LoaderCircle className="spin"/><h2>Opening your conversations…</h2></div>:!doc?<div className="chat-welcome"><h2>Let’s reconnect.</h2><p>Your chat library could not be opened.</p><button className="primary" onClick={()=>window.location.reload()}>Try again</button></div>:doc.messages.length===0?<div className="chat-welcome"><InflectMark size={52}/><h2>Where shall we begin?</h2><p>Ask a question, shape a rough idea, or drop in a file to explore.</p><div className="chat-starters"><button onClick={()=>{changeDraft('Create a beautiful, self-contained HTML dashboard with interactive charts using only HTML, CSS and JavaScript. Put the complete file in one html code block.');composer.current?.focus()}}><span><Sparkles size={17}/></span><strong>Build something</strong><small>Turn an idea into a working preview</small><ArrowUpRight size={15}/></button><button onClick={()=>fileInput.current?.click()}><span><Paperclip size={17}/></span><strong>Explore a file</strong><small>Read a document, image or code file</small><ArrowUpRight size={15}/></button></div><small className="welcome-local"><span className="local-indicator"/>Your conversations stay on this computer</small></div>:<div className="messages inflect-messages">{doc.messages.map((message,index)=><article key={message.id} className={`message ${message.role} ${message.error?'failed':''}`}><div className="message-avatar">{message.role==='assistant'?<InflectMark size={30}/>:<span>You</span>}</div><div className="message-body"><div className="message-author">{message.role==='assistant'?'Inflect':'You'}{message.role==='assistant'&&message.model&&<span className="message-model" style={hueStyle(message.model)}>{message.model}</span>}<time dateTime={new Date(message.created_at*1000).toISOString()}>{new Date(message.created_at*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}</time></div>{message.attachments.length>0&&<div className="message-files">{message.attachments.map(id=>fileChip(id))}</div>}{message.reasoning&&<details className="reasoning"><summary><Sparkles size={13}/>Thinking</summary><div className="markdown"><RichMarkdown text={message.reasoning} onPreview={openPreview}/></div></details>}<div className="markdown"><RichMarkdown text={message.content} onPreview={openPreview} live={(streaming||!!doc.generating)&&index===doc.messages.length-1}/></div>{streaming&&index===doc.messages.length-1&&<span className="typing" aria-label="Generating"><i/><i/><i/></span>}{message.cancelled&&<p className="response-state"><Square size={11}/>Stopped · partial answer saved</p>}{message.error&&<p className="response-state error">{message.error_message||'This reply did not complete. You can retry it.'}</p>}{message.metrics?.finish_reason==='length'&&<p className="output-limit-note">{outputLimitNote(message.metrics)}</p>}{message.metrics&&<Performance metrics={message.metrics} compact/>}<div className="message-actions"><button title="Copy message" aria-label="Copy message" onClick={()=>copy(message)}>{copied===message.id?<Check size={13}/>:<Copy size={13}/>}<span>{copied===message.id?'Copied':'Copy'}</span></button>{message.role==='user'?<button disabled={blocked} onClick={()=>setEdit({index,text:message.content})}><Pencil size={13}/>Edit</button>:<button disabled={blocked||!active||!!dirty} onClick={()=>retry(index)}><RotateCcw size={13}/>Retry</button>}<button disabled={busy} onClick={()=>branch(index)} title="Continue from here in a new conversation"><GitBranch size={13}/>Branch</button></div></div></article>)}</div>}
      </div></div>{showLatest&&<button className="jump-latest" onClick={latest}><ArrowDown size={14}/>Jump to latest</button>}</div>
      <div className="inflect-composer-wrap">{draftFiles.length>0&&<div className="draft-files">{draftFiles.map(id=>fileChip(id,true))}</div>}<div className="composer"><textarea ref={composer} aria-label="Message" placeholder={active?'Message the model, or drop in a file…':'Draft a message…'} disabled={busy||!doc} value={draft} onChange={e=>changeDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!blocked)send()}}} rows={1}/><div className="composer-toolbar"><button className="icon-btn" disabled={busy||!doc} aria-label="Attach files" title="Attach files · HTML, PDF, images, Markdown, code or audio" onClick={()=>fileInput.current?.click()}><Paperclip size={17}/></button><label className="composer-chip" title="How much the model reasons before it answers"><Sparkles size={13}/><span>Thinking</span><ThinkingSelect model={model} value={effort} disabled={blocked} onChange={setEffort} label="Chat thinking level"/></label><button className="composer-chip composer-settings" title="Context, memory and speed settings for this model" onClick={onSettings}><Settings2 size={13}/>Model settings</button><div className="toolbar-spacer"/><small>Enter to send · Shift + Enter for a new line</small>{streaming||doc?.generating?<button className="send-button" aria-label="Stop generation" title="Stop · the partial answer is kept" onClick={stop}><Square size={15}/></button>:<button className="send-button" aria-label="Send message" title="Send" disabled={!active||!!dirty||blocked||!doc||(!draft.trim()&&!draftFiles.length)} onClick={()=>send()}><ArrowUp size={19}/></button>}</div></div><div className="context-footer"><span className="context-usage" title="How much of the model’s context window this conversation uses">{doc?.used_context!=null&&<span className="context-meter" aria-hidden="true"><i style={{width:`${Math.min(100,doc.used_context/Math.max(settings.context,1)*100)}%`}}/></span>}{doc?.used_context!=null?`${formatNumber(doc.used_context,0)} / ${formatNumber(settings.context,0)} tokens`:`${formatNumber(settings.context,0)} token context`}</span><span>{doc?.system_prompt?'Custom instructions · ':''}{active?'Connected locally':loadedModel&&session?.state==='ready'?`${model.title} is not loaded`:'Model not loaded'}</span></div></div>
      {dragging&&<div className="drop-hint"><Paperclip size={24}/>Drop files to add them to this conversation</div>}
    </div>
    {preview&&<PreviewPanel preview={preview} onClose={()=>openPreview(null)}/>}
    <input type="file" ref={fileInput} hidden multiple accept={acceptedFiles} onChange={e=>{attach(Array.from(e.target.files||[]));e.target.value=''}}/><input type="file" ref={importInput} hidden accept=".json,application/json" onChange={e=>{const file=e.target.files?.[0];if(file)importChat(file);e.target.value=''}}/>
    {modal&&modal!=='model'&&modal!=='details'&&<Modal title={modal==='rename'?'Rename conversation':modal==='instructions'?'Chat instructions':'Delete this conversation?'} onClose={()=>{if(!working)setModal(null)}}>{modal==='delete'?<><p className="dialog-description">Permanently delete <strong>{doc?.title}</strong>, including messages, drafts, attachments and previews. There is no trash or undo.</p>{doc?.messages.some(m=>m.role==='assistant')&&session?.state!=='idle'&&<p className="delete-cache-note">The loaded model will be unloaded to clear its retained chat cache. You can load it again afterwards.</p>}<p className="dialog-description deletion-scope">Separately exported files, independent branches and system backups are outside this conversation.</p></>:<label className="field">{modal==='rename'?'Conversation name':'Instructions for every reply in this conversation'}{modal==='rename'?<input maxLength={140} autoFocus value={modalText} onChange={e=>setModalText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&modalText.trim())confirmModal()}}/>:<textarea className="instructions-input" value={modalText} maxLength={100000} onChange={e=>setModalText(e.target.value)} placeholder="For example: explain clearly, use practical examples, and ask when a requirement is ambiguous."/>}</label>}{error&&<p className="dialog-error" role="alert">{error}</p>}<div className="dialog-actions"><button className="secondary" disabled={working} onClick={()=>setModal(null)}>Cancel</button><button className={modal==='delete'?'danger-button':'primary'} disabled={working||modal==='rename'&&!modalText.trim()} onClick={confirmModal}>{working?<LoaderCircle size={15} className="spin"/>:modal==='delete'?<Trash2 size={15}/>:<Check size={15}/>} {modal==='delete'?'Delete permanently':'Save changes'}</button></div></Modal>}
    {modal==='details'&&<Modal title="Model details" onClose={()=>setModal(null)} className="model-picker"><div className="model-detail-dialog"><h3>{model.title}</h3><dl><dt>Format</dt><dd>{model.format} · {model.quant}</dd><dt>Architecture</dt><dd>{model.architecture}</dd><dt>Native context</dt><dd>{formatNumber(model.context,0)} tokens</dd><dt>Vision</dt><dd>{model.vision?'Available in checkpoint':'Text only'}</dd><dt>Prediction</dt><dd>{model.mtp?'MTP weights identified':'Not identified'}</dd><dt>Verification</dt><dd>{model.receipt?.status==='verified'?`Verified ${new Date(model.receipt.verified_utc).toLocaleString()}`:'No downloader receipt'}</dd><dt>Location</dt><dd>{model.path}</dd></dl>{model.issues.map(issue=><p className="warning" key={issue}>{issue}</p>)}{active&&<details><summary>Effective engine settings</summary><pre>{JSON.stringify(session?.effective,null,2)}</pre></details>}</div></Modal>}
    {modal==='model'&&<Modal title="Choose a model" onClose={()=>setModal(null)} className="model-picker"><p className="dialog-description">Your conversation stays open when you switch. Pick a saved setup to use its settings, then load it to continue.</p><div className="model-picker-list">{models.map(m=>{const setups=profiles.filter(p=>p.settings.model_id===m.id);return <div key={m.id} className="model-picker-group"><button className={m.id===model.id?'selected':''} onClick={()=>{onModel(m);setModal(null)}}><span><strong>{m.title}</strong><small>{m.format} · {m.quant} · {m.vision?'Vision':'Text'}{setups.length?` · ${setups.length} saved setup${setups.length===1?'':'s'}`:''}</small></span>{m.id===model.id&&<Check size={16}/>}</button>{setups.map(p=><button key={p.id} className="model-picker-setup" onClick={()=>{onProfile?.(p);setModal(null)}}><span><small>{setupLabel?setupLabel(p):p.name}</small></span></button>)}</div>})}</div></Modal>}
    {edit&&<Modal title="Edit and continue" onClose={()=>{if(!working)setEdit(null)}}><p className="dialog-description">Continue with your edited message in a new branch. The original conversation remains available.</p><textarea className="instructions-input" aria-label="Edit message" value={edit.text} onChange={e=>setEdit({...edit,text:e.target.value})}/><div className="dialog-actions"><button className="secondary" disabled={working} onClick={()=>setEdit(null)}>Cancel</button><button className="primary" disabled={working||!edit.text.trim()||!active||!!dirty} onClick={editAndSend}>{working?<LoaderCircle size={15} className="spin"/>:<GitBranch size={15}/>}Save branch & send</button></div></Modal>}
  </section>;
}
