import {useId} from 'react';
import type {CSSProperties} from 'react';

const SNAPS = [4096,8192,16384,32768,65536,98304,131072,163840,196608,262144,393216,524288,786432,1048576,2097152,4194304,8388608,16777216];
export const shortTokens = (n:number) => n>=1048576?`${+(n/1048576).toFixed(2)}M`:n>=1024?`${+(n/1024).toFixed(1)}K`:`${n}`;
/** Snap points up to the model's own maximum, always including that maximum. */
export function contextPoints(max:number, value?:number) {
  const top=Math.max(2048,Math.floor(Math.min(max,16777216)/256)*256);
  const points=SNAPS.filter(p=>p<top);
  points.push(top);
  if(value&&value<=top&&!points.includes(value))points.push(value);
  return points.sort((a,b)=>a-b);
}
const LANDMARKS = new Set([32768,131072,262144,1048576]);

export function ContextSlider({value,max,onChange,disabled}:{value:number;max:number;onChange:(v:number)=>void;disabled?:boolean}) {
  const id=useId(), points=contextPoints(max,value), index=Math.max(0,points.indexOf(value)), last=points.length-1;
  const dense=points.length>11;
  return <div className="context-slider">
    <div className="context-readout"><strong>{shortTokens(value)}</strong><span>tokens · roughly {Math.round(value*.75/1000).toLocaleString()}K words</span></div>
    <input id={id} type="range" min={0} max={last} step={1} value={index} disabled={disabled} aria-label="Context window" aria-valuetext={`${shortTokens(value)} tokens`}
      style={{'--fill':`${last?index/last*100:100}%`} as CSSProperties} onChange={e=>onChange(points[Number(e.target.value)])}/>
    <div className="context-ticks" aria-hidden="true">{points.map((p,i)=>{
      const label=!dense||i===last||p===value||(LANDMARKS.has(p)&&Math.abs(i-points.indexOf(value))>1)||(i%2===0&&last-i>1&&Math.abs(i-points.indexOf(value))>1);
      return <button type="button" tabIndex={-1} key={p} disabled={disabled} className={`${p===value?'on':''} ${label?'labelled':''}`} style={{left:`${last?i/last*100:100}%`}} onClick={()=>onChange(p)}><i/>{label&&<span>{i===last?`${shortTokens(p)} max`:shortTokens(p)}</span>}</button>})}</div>
  </div>;
}
