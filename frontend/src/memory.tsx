import {useState} from 'react';
import {CircleHelp, Database} from 'lucide-react';
import {formatNumber, Modal} from './ui';
import {memoryBreakdown} from './memory-accounting';
import type {Ram,ModelMemory} from './memory-accounting';
export type {ModelMemory} from './memory-accounting';
import './memory.css';

export const correctedRam = (memory?:{ram_accounting?:string;ram_bytes:number|null}|null) => memory?.ram_accounting==='physical_including_cache_v1'?memory.ram_bytes:null;
const gib=(bytes:number|null|undefined)=>formatNumber(bytes==null?null:bytes/2**30,1);

export function MemoryMetric({ram,model,loaded}:{ram?:Ram;model?:ModelMemory;loaded:boolean}) {
  const [open,setOpen]=useState(false);
  const {modelBytes,availableBytes}=memoryBreakdown(ram,model,loaded);
  const percent=ram?.total_bytes&&modelBytes!=null?Math.max(0,Math.min(100,modelBytes/ram.total_bytes*100)):0;
  return <div className="metric memory-metric">
    <div className="metric-label"><Database size={15}/>MODEL RAM<button className="memory-info" aria-label="Explain RAM usage" title="About these figures" onClick={()=>setOpen(true)}><CircleHelp size={13}/></button></div>
    <div className="memory-capacity"><strong>{gib(modelBytes)}</strong><span>GiB <span className="memory-total">/ {gib(ram?.total_bytes)} GiB total</span></span></div>
    <div className="meter memory-meter" aria-label={`Model uses ${gib(modelBytes)} of ${gib(ram?.total_bytes)} GiB total RAM`}><i style={{width:`${percent}%`}}/></div>
    <div className="memory-available"><span>Available <strong>{gib(availableBytes)} GiB</strong></span><small>estimated</small></div>
    <div className="memory-caption">{!loaded?'No model loaded':modelBytes==null||availableBytes==null?'Measuring model memory…':'Keeping this model in memory'}</div>
    {open&&<Modal title="RAM at a glance" onClose={()=>setOpen(false)} className="memory-dialog">
      <dl className="memory-breakdown">
        <div><dt>Model RAM<small>Memory currently used by the loaded model and its inference engine.</small></dt><dd>{gib(modelBytes)} <small>GiB</small></dd></div>
        <div><dt>Available · estimated<small>Extra memory available for other work while keeping the current model in memory. Allows for other applications and the operating system. This is a conservative estimate, not a guarantee that another model will fit.</small></dt><dd>{gib(availableBytes)} <small>GiB</small></dd></div>
        <div><dt>Total RAM<small>Total usable memory in this computer.</small></dt><dd>{gib(ram?.total_bytes)} <small>GiB</small></dd></div>
      </dl>
      <p className="memory-explainer">The difference between total RAM and model RAM is not all available: other applications and the system need memory too. These readings update automatically.</p>
    </Modal>}
  </div>;
}
