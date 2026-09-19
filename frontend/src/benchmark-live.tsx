import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowDown,Copy,Maximize2,Minimize2,Minus,Terminal} from 'lucide-react';
import {Modal,formatNumber} from './ui';
import './benchmark-live.css';

type Block={id:number;revision:number;kind:string;text:string;timestamp:number};
type Api=(path:string,body?:unknown,method?:string)=>Promise<any>;
export function BenchmarkLive({session,request,copy,onResults}:{session:any;request:Api;copy:(text:string)=>Promise<void>;onResults:(kind:string,id:string)=>void}) {
  const meta=session?.live_output;
  const [blocks,setBlocks]=useState<Block[]>([]),[mode,setMode]=useState<'panel'|'full'|'minimized'>('panel');
  const [follow,setFollow]=useState(true),[error,setError]=useState(''),[truncated,setTruncated]=useState(false),[copied,setCopied]=useState(false);
  const [result,setResult]=useState<any>(null),[finished,setFinished]=useState<any>(null);
  const cursor=useRef(0),runId=useRef(''),observed=useRef(''),announced=useRef('');
  const body=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!meta?.id)return;
    if(meta.state==='running')observed.current=meta.id;
    if(runId.current!==meta.id){runId.current=meta.id;cursor.current=0;setBlocks([]);setTruncated(false);setFollow(true);setMode(meta.state==='running'?'panel':'minimized');setError('')}
    let disposed=false,running=false;
    const poll=async()=>{if(running)return;running=true;try{
      const data=await request(`/api/benchmark-output?${new URLSearchParams({run_id:runId.current,after:String(cursor.current)})}`);
      if(disposed)return;
      if(data.run?.id!==meta.id)return;
      cursor.current=data.cursor;
      setBlocks(old=>{const map=new Map<number,Block>((data.reset?[]:old).filter((b:Block)=>b.id>=data.first_id).map((b:Block)=>[b.id,b]));for(const block of data.blocks)map.set(block.id,block);return [...map.values()]});
      setTruncated(data.truncated);setError('');
    }catch(e){if(!disposed)setError('Live output disconnected. Retrying… '+String(e))}finally{running=false}};
    poll();const timer=setInterval(poll,meta.state==='running'?750:3000);
    return()=>{disposed=true;clearInterval(timer)};
  },[meta?.id,meta?.state]);
  useEffect(()=>{
    if(!meta||meta.state==='running'||observed.current!==meta.id||announced.current===meta.id)return;
    announced.current=meta.id;
    if(['cancelled','aborted','interrupted'].includes(meta.state)){setMode('minimized');setBlocks([]);return}
    setMode('minimized');setFinished(meta);setResult(null);
    loadResult(meta);
  },[meta?.id,meta?.state]);
  const loadResult=async(run:any)=>{try{const result=run.kind==='coding'?await request(`/api/evaluations/${run.id}`):(await request('/api/benchmarks')).find((r:any)=>r.id===run.id);if(!result)throw new Error('Result not found');setResult(result);setError('')}catch(e){setError('Could not load the result: '+String(e))}};
  useLayoutEffect(()=>{if(follow&&body.current)body.current.scrollTop=body.current.scrollHeight},[blocks,mode,follow]);
  useEffect(()=>{const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setMode(old=>old==='full'?'panel':old)};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape)},[]);
  const copyTail=async()=>{try{await copy(blocks.map(b=>b.text).join(''));setCopied(true);setTimeout(()=>setCopied(false),2000)}catch{setError('Copy failed. Select the output and press Ctrl+C.')}};
  if(!meta)return null;
  const running=meta.state==='running';
  return <>
    {mode==='minimized'?<button className="benchmark-tail-minimized" onClick={()=>setMode('panel')}><Terminal size={15}/>{meta.title} · {running?'Live output':meta.state}<Maximize2 size={13}/></button>:
      <section className={`benchmark-tail ${mode==='full'?'fullscreen':''}`} aria-label="Live benchmark output">
        <header><Terminal size={16}/><div><strong>{meta.title} <i className={running?'live':''}/></strong><span title={meta.model.name}>{meta.model.name} · {meta.state}</span></div>
          <button aria-label={copied?'Output copied':'Copy visible benchmark output'} title="Copy retained output" onClick={copyTail}><Copy size={15}/>{copied?'Copied':''}</button>
          <button aria-label={mode==='full'?'Exit full-screen benchmark output':'Expand benchmark output full screen'} onClick={()=>setMode(mode==='full'?'panel':'full')}>{mode==='full'?<Minimize2 size={16}/>:<Maximize2 size={16}/>}</button>
          <button aria-label="Minimize benchmark output" onClick={()=>setMode('minimized')}><Minus size={18}/></button>
        </header>
        <div className="benchmark-tail-controls"><span>Model output · thinking · commands · test progress</span><label><input type="checkbox" checked={follow} onChange={e=>setFollow(e.target.checked)}/>Follow tail</label></div>
        {error&&<p className="benchmark-tail-error" role="status">{error}</p>}
        <div className="benchmark-tail-body" ref={body} tabIndex={0} aria-label="Benchmark terminal output" onScroll={e=>{const el=e.currentTarget;if(el.scrollHeight-el.scrollTop-el.clientHeight>60&&follow)setFollow(false)}}>
          {truncated&&<p className="tail-truncated">Earlier output trimmed. This window keeps the latest output; completed coding runs retain their full artifacts.</p>}
          {!blocks.length&&<p>{running?'Waiting for the model…':['cancelled','aborted'].includes(meta.state)?'Stopped benchmark output discarded.':'No retained output.'}</p>}
          {blocks.map((block,index)=><div key={block.id} className={`tail-block ${block.kind}`}>
            {(index===0||blocks[index-1].kind!==block.kind)&&<span className="tail-block-label">{new Date(block.timestamp*1000).toLocaleTimeString()} · {({status:'Progress',model:'Answer',reasoning:'Thinking',command:'Command',output:'Tool / test output',prompt:'Prompt'} as Record<string,string>)[block.kind]||block.kind}</span>}
            <pre>{block.text}</pre>
          </div>)}
        </div>
        <footer><span>{running?'Streaming locally · refreshes every 0.75s':'Run '+meta.state}</span><button onClick={()=>{setFollow(true);if(body.current)body.current.scrollTop=body.current.scrollHeight}}><ArrowDown size={13}/>{follow?'Following latest':'Jump to latest'}</button></footer>
      </section>}
    {finished&&<Modal title={finished.state==='complete'?'Benchmark finished':'Benchmark ended'} onClose={()=>setFinished(null)} className="benchmark-finished-dialog">
      <p className="eyebrow">{finished.title} · {finished.state}</p><h3 title={finished.model.name}>{finished.model.name}</h3>
      {result?<>
        {result.summary&&<div className="benchmark-finished-score"><strong>{result.summary.score==null?'Incomplete grading':`${result.summary.score}%`}</strong><span>{result.summary.passed}/{result.summary.total} tasks passed · {result.summary.graded} fully graded</span></div>}
        <div className="profile-priority-metrics"><div><span>Generation</span><strong>{formatNumber(result.performance?.decode_tps??result.median_tps)} <small>tok/s</small></strong></div><div><span>Prefill</span><strong>{formatNumber(result.performance?.prefill_tps??result.median_prompt_tps,0)} <small>tok/s</small></strong></div></div>
        <p className="dialog-description">Median engine speeds. Prefill reflects prompt-cache reuse.</p>
        {result.suite==='swebench'&&<p className="dialog-description">Each issue is resolved or unresolved, without partial credit. {result.summary.total===1?'This preset tests one issue, so only 0% or 100% is possible.':`This preset tests ${result.summary.total} issues; intermediate percentages reflect the number resolved.`}</p>}
        {result.error&&<p role="alert">{result.error}</p>}
        <div className="dialog-actions"><button className="secondary" onClick={()=>setFinished(null)}>Close</button><button className="primary" onClick={()=>{onResults(finished.kind,finished.id);setFinished(null)}}>View full results</button></div>
      </>:<><p role="status">{error||'Loading results…'}</p>{error&&<button className="secondary" onClick={()=>loadResult(finished)}>Retry</button>}</>}
    </Modal>}
  </>;
}
