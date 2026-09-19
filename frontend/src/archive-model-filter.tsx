import {useEffect,useRef,useState} from 'react';
import {ChevronDown,LoaderCircle,Search} from 'lucide-react';

type ModelChoice={id:string;name:string;quant:string;format:string};
export function ArchiveModelFilter({value,onChange,request}:{value:ModelChoice|null;onChange:(model:ModelChoice|null)=>void;request:(path:string)=>Promise<any>}) {
  const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[items,setItems]=useState<ModelChoice[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const root=useRef<HTMLDivElement>(null),ticket=useRef(0);
  useEffect(()=>{if(!open)return;const close=(e:MouseEvent)=>{if(!root.current?.contains(e.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[open]);
  useEffect(()=>{if(!open)return;const current=++ticket.current;setLoading(true);setError('');const timer=setTimeout(()=>request(`/api/archive/models?q=${encodeURIComponent(query)}`).then(models=>{if(current===ticket.current)setItems(models)}).catch(e=>{if(current===ticket.current)setError(e.message)}).finally(()=>{if(current===ticket.current)setLoading(false)}),200);return()=>{clearTimeout(timer);ticket.current++}},[open,query]);
  const choose=(model:ModelChoice|null)=>{onChange(model);setOpen(false);setQuery('')};
  return <div className="archive-model-filter" ref={root} onKeyDown={e=>{if(e.key==='Escape'){setOpen(false);root.current?.querySelector('button')?.focus()}}}>
    <button className="secondary" aria-label="Filter by model" aria-expanded={open} title={value?.name||'All models'} onClick={()=>setOpen(v=>!v)}><span>{value?.name||'All models'}</span><ChevronDown size={14}/></button>
    {open&&<div className="archive-model-menu"><label><Search size={15}/><input autoFocus aria-label="Search current library models" placeholder="Search your model library…" value={query} onChange={e=>setQuery(e.target.value)}/>{loading&&<LoaderCircle size={14} className="spin"/>}</label>
      <div className="archive-model-options" aria-label="Current library models"><button aria-pressed={!value} onClick={()=>choose(null)}>All models</button>{!loading&&items.map(model=><button key={model.id} title={model.name} aria-pressed={value?.id===model.id} onClick={()=>choose(model)}><strong>{model.name}</strong><small>{model.format} · {model.quant}</small></button>)}{error?<p role="alert">{error}</p>:!loading&&!items.length&&<p>No matching models in the current library.</p>}</div>
    </div>}
  </div>;
}
