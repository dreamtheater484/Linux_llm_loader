import {useEffect,useRef,useState} from 'react';
import {CheckCircle2,LoaderCircle,Terminal,X} from 'lucide-react';

export function LoadFeedback({session,pendingName,onCancel,onLogs,onChat}:{session:any;pendingName?:string;onCancel:()=>void;onLogs:()=>void;onChat:()=>void}) {
  const previous=useRef<any>(null);
  const [notice,setNotice]=useState<{name:string;details:string}|null>(null);
  const preparing=!!pendingName||session?.state==='loading',stopping=session?.state==='stopping'&&!pendingName;
  useEffect(()=>{
    const old=previous.current;
    if(session?.state==='loading'||session?.state==='stopping'||session?.state==='error')setNotice(null);
    if(session?.state==='ready'&&old&&((old.state==='loading')||old.started!==session.started)){
      const s=session.settings;
      setNotice({name:session.model.title,details:`${session.model.quant} · ${s.context/1024}K / ${s.kv} · Vision ${s.vision?'on':'off'} · MTP ${s.prediction==='mtp'?`on (${s.draft_tokens})`:'off'}`});
    }
    previous.current=session;
  },[session?.state,session?.started]);
  if(preparing||stopping)return <section className="model-load-banner" aria-label="Model loading status">
    <LoaderCircle size={25} className="spin"/><div className="load-banner-copy"><strong role="status">{stopping?'Unloading':'Loading'} {pendingName||session?.model?.title||'model'}</strong><p>{pendingName?'Preparing the model and freeing the previous model’s memory.':stopping?'Releasing model memory.':'Preparing weights, context cache and inference.'} {!pendingName&&session?.elapsed_seconds!=null&&!stopping&&<b>{Math.floor(session.elapsed_seconds)}s elapsed</b>}</p><div className="load-indicator" role="progressbar" aria-label="Loading model"><i/></div></div>
    <button className="secondary" onClick={onLogs}><Terminal size={14}/>Logs</button>{!stopping&&!pendingName&&<button className="secondary" onClick={onCancel}>Cancel</button>}
  </section>;
  if(!notice||session?.state!=='ready')return null;
  return <section className="model-ready-banner" role="status"><CheckCircle2 size={25}/><div><strong>Ready to chat · {notice.name}</strong><p>{notice.details}</p></div><button className="primary" onClick={onChat}>Open chat</button><button className="icon-btn" aria-label="Dismiss model ready notification" onClick={()=>setNotice(null)}><X size={17}/></button></section>;
}
