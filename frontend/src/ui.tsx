import {useEffect, useRef, useState} from 'react';
import type {CSSProperties, ReactNode} from 'react';
import {ArrowDownToLine, CircleHelp, Moon, Sun, Timer, X, Zap} from 'lucide-react';

/** A stable hue per model name, so each model keeps its own quiet colour. */
export const modelHue = (name='') => {let h=7;for(const c of name)h=(h*31+c.charCodeAt(0))>>>0;return h%360};
export const hueStyle = (name?:string) => ({'--hue':modelHue(name)} as CSSProperties);

export type ThemeChoice = 'system'|'light'|'dark';
const THEME_KEY='inflect.theme';
const readTheme=():ThemeChoice=>{try{const v=localStorage.getItem(THEME_KEY);return v==='light'||v==='dark'?v:'system'}catch{return 'system'}};
const systemDark=()=>typeof matchMedia!=='undefined'&&matchMedia('(prefers-color-scheme: dark)').matches;
/** Appearance is a per-browser preference; index.html applies it before first paint. */
export function useTheme():[ThemeChoice,(choice:ThemeChoice)=>void,boolean] {
  const [choice,setChoice]=useState<ThemeChoice>(readTheme), [dark,setDark]=useState(systemDark);
  useEffect(()=>{
    const sync=()=>{setChoice(readTheme());setDark(systemDark())};
    const media=matchMedia('(prefers-color-scheme: dark)');
    window.addEventListener('inflect-theme',sync);window.addEventListener('storage',sync);media.addEventListener('change',sync);
    return()=>{window.removeEventListener('inflect-theme',sync);window.removeEventListener('storage',sync);media.removeEventListener('change',sync)};
  },[]);
  const choose=(next:ThemeChoice)=>{
    try{next==='system'?localStorage.removeItem(THEME_KEY):localStorage.setItem(THEME_KEY,next)}catch{}
    if(next==='system')delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=next;
    window.dispatchEvent(new Event('inflect-theme'));
  };
  return [choice,choose,choice==='dark'||(choice==='system'&&dark)];
}
export function ThemeToggle() {
  const [,choose,dark]=useTheme();
  const label=dark?'Switch to light theme':'Switch to dark theme';
  return <button className="icon-btn theme-toggle" aria-label={label} title={label} onClick={()=>choose(dark?'light':'dark')}>{dark?<Sun size={17}/>:<Moon size={17}/>}</button>;
}

/** A small “?” that explains a setting on hover, focus or tap. */
export function Hint({text,label}:{text:string;label:string}) {
  return <span className="hint" role="button" tabIndex={0} aria-label={`About ${label}`} data-tip={text} onClick={e=>{e.preventDefault();e.stopPropagation();(e.currentTarget as HTMLElement).focus()}}><CircleHelp size={13}/></span>;
}

/** Replaces the browser's slow native tooltips with consistent, readable ones.
 * Elements keep their title for assistive technology until hovered. */
export function Tooltips() {
  const [tip,setTip]=useState<{text:string;x:number;y:number;below:boolean}|null>(null);
  const owner=useRef<HTMLElement|null>(null), timer=useRef<number>(0);
  useEffect(()=>{
    const restore=()=>{const el=owner.current;if(el?.dataset.tipTitle!=null){el.setAttribute('title',el.dataset.tipTitle);delete el.dataset.tipTitle}owner.current=null};
    const hide=()=>{clearTimeout(timer.current);restore();setTip(null)};
    const show=(target:EventTarget|null,delay:number)=>{
      const el=(target as HTMLElement|null)?.closest?.('[data-tip],[title]') as HTMLElement|null;
      if(!el||el===owner.current||el.closest('dialog'))return;
      hide();
      const title=el.getAttribute('title');
      if(title!=null){if(!title.trim())return;el.dataset.tipTitle=title;el.removeAttribute('title')}
      owner.current=el;
      const text=el.dataset.tip||title||'';
      timer.current=window.setTimeout(()=>{if(owner.current!==el||!el.isConnected)return;const r=el.getBoundingClientRect();const below=r.top<70;setTip({text,x:Math.min(Math.max(r.left+r.width/2,160),Math.max(innerWidth-160,160)),y:below?r.bottom+8:r.top-8,below})},delay);
    };
    const over=(e:PointerEvent)=>{if(e.pointerType!=='touch')show(e.target,350)};
    const out=(e:PointerEvent)=>{const el=owner.current;if(el&&!(e.relatedTarget instanceof Node&&el.contains(e.relatedTarget)))hide()};
    const focus=(e:FocusEvent)=>{const el=e.target as HTMLElement;if(el.matches?.(':focus-visible')||el.classList?.contains('hint'))show(el,0)};
    const key=(e:KeyboardEvent)=>{if(e.key==='Escape')hide()};
    document.addEventListener('pointerover',over);document.addEventListener('pointerout',out);document.addEventListener('focusin',focus);document.addEventListener('focusout',hide);
    document.addEventListener('pointerdown',hide,true);document.addEventListener('scroll',hide,true);document.addEventListener('keydown',key);
    return()=>{hide();document.removeEventListener('pointerover',over);document.removeEventListener('pointerout',out);document.removeEventListener('focusin',focus);document.removeEventListener('focusout',hide);document.removeEventListener('pointerdown',hide,true);document.removeEventListener('scroll',hide,true);document.removeEventListener('keydown',key)};
  },[]);
  if(!tip)return null;
  return <div className={`tooltip ${tip.below?'below':''}`} role="tooltip" style={{left:tip.x,top:tip.y}}>{tip.text}</div>;
}

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
    <span className="speed prompt-speed" title={`How fast the engine read your message and context. ${formatNumber(cached??0,0)} input tokens reused from cache.`}>
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
