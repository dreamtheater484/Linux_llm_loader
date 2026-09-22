import {useEffect,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,LoaderCircle} from 'lucide-react';
import {pdfLibrary} from './files';
export default function PdfPreview({url}:{url:string}) {
  const [doc,setDoc]=useState<any>(null),[page,setPage]=useState(1),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const canvas=useRef<HTMLCanvasElement>(null);
  useEffect(()=>{let disposed=false,document:any,task:any;setError('');setLoading(true);setDoc(null);setPage(1);
    (async()=>{try{const pdf=await pdfLibrary();if(disposed)return;task=pdf.getDocument({url});document=await task.promise;if(!disposed)setDoc(document);else await task.destroy()}catch(e){if(!disposed)setError(String(e))}})();
    return()=>{disposed=true;task?.destroy()};
  },[url]);
  useEffect(()=>{if(!doc)return;let disposed=false,render:any;setLoading(true);
    (async()=>{try{const p=await doc.getPage(page);if(disposed||!canvas.current)return;const initial=p.getViewport({scale:1});const width=Math.min(1400,canvas.current.parentElement!.clientWidth*window.devicePixelRatio-32);const viewport=p.getViewport({scale:Math.max(.2,width/initial.width)});canvas.current.width=viewport.width;canvas.current.height=viewport.height;render=p.render({canvas:canvas.current,viewport});await render.promise;if(!disposed)setLoading(false)}catch(e){if(!disposed){setError(String(e));setLoading(false)}}})();return()=>{disposed=true;render?.cancel()};
  },[doc,page]);
  return <div className="pdf-preview"><div className="pdf-controls"><button aria-label="Previous PDF page" disabled={page<=1} onClick={()=>setPage(p=>p-1)}><ChevronLeft size={16}/></button><span>Page {page} of {doc?.numPages||'…'}</span><button aria-label="Next PDF page" disabled={!doc||page>=doc.numPages} onClick={()=>setPage(p=>p+1)}><ChevronRight size={16}/></button></div>{error?<p role="alert">{error}</p>:<>{loading&&<LoaderCircle size={18} className="spin"/>}<canvas ref={canvas}/></>}</div>;
}
