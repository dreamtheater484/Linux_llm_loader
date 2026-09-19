import {useEffect, useRef} from 'react';
import type {ReactNode} from 'react';
import {ArrowDownToLine, Timer, X, Zap} from 'lucide-react';

export const formatNumber = (n:number|null|undefined, digits=1) => n == null || !Number.isFinite(n) ? '—' : n.toLocaleString(undefined,{maximumFractionDigits:digits});
export const promptSpeed = (m:any):number|null => m?.prompt_tokens_per_second ?? m?.usage?.prompt_tokens_per_sec ?? null;
export function medianPrompt(result:any):number|null {
  if(result.median_prompt_tps!=null)return result.median_prompt_tps;
  const values=(result.runs||[]).map(promptSpeed).filter((n:any)=>typeof n==='number'&&Number.isFinite(n)).sort((a:number,b:number)=>a-b);
  const mid=Math.floor(values.length/2);
  return values.length?values.length%2?values[mid]:(values[mid-1]+values[mid])/2:null;
}

export function Performance({metrics,compact=false}:{metrics:any;compact?:boolean}) {
  const cached=metrics?.usage?.prompt_tokens_details?.cached_tokens;
  const predictions=metrics?.usage?.completion_tokens_details;
  const accepted=predictions?.accepted_prediction_tokens;
  const drafted=accepted==null?null:accepted+(predictions?.rejected_prediction_tokens??0);
  return <div className={`performance ${compact?'compact-performance':''}`}>
    <span className="speed prompt-speed" title={`Speed reported by the engine while processing the prompt. ${formatNumber(cached??0,0)} input tokens reused from cache.`}>
      <ArrowDownToLine size={14}/><span>Prompt processing<strong>{formatNumber(promptSpeed(metrics))}<small>tok/s</small></strong></span>
    </span>
    <span className="speed decode-speed" title={`Generation speed includes thinking and the final answer. Prompt processing is measured separately.${drafted>0?` MTP: ${formatNumber(accepted,0)} of ${formatNumber(drafted,0)} draft tokens accepted.`:''}`}>
      <Zap size={14}/><span>Decoding<strong>{formatNumber(metrics?.tokens_per_second)}<small>tok/s</small></strong></span>
    </span>
    <span className="speed latency" title="Time from sending the request to the first visible token, including prompt processing.">
      <Timer size={14}/><span>First token<strong>{formatNumber(metrics?.first_token_seconds)}<small>s</small></strong></span>
    </span>
    {compact&&<span className="response-token-count" title="Total generated tokens, including thinking and the final answer.">{formatNumber(metrics?.usage?.completion_tokens,0)} output tokens{cached>0&&<span> · {formatNumber(cached,0)} cached input</span>}</span>}
  </div>;
}

export function Modal({title,onClose,children,className=''}:{title:string;onClose:()=>void;children:ReactNode;className?:string}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const el=ref.current;el?.showModal();const input=el?.querySelector<HTMLInputElement>('input');input?.focus();input?.select();return()=>{el?.close()}},[]);
  return <dialog ref={ref} className={`app-dialog ${className}`} aria-label={title}
    onCancel={e=>{e.preventDefault();onClose()}} onClick={e=>{if(e.target===ref.current)onClose()}}>
    <div className="dialog-content">
      <header className="dialog-header"><h2>{title}</h2><button className="icon-btn" aria-label={`Close ${title.toLowerCase()}`} onClick={onClose}><X size={20}/></button></header>
      {children}
    </div>
  </dialog>;
}
