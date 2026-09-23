import {lazy,Suspense,useEffect,useState} from 'react';
import {Code2,Download,Eye,FileText,Globe,Maximize2,Minimize2,RotateCcw,X} from 'lucide-react';
import type {Preview} from './types';
import {downloadText} from './types';
import {RichMarkdown} from './markdown';
const PdfPreview=lazy(()=>import('./pdf-preview'));
// An opaque origin, no network, no parent DOM/storage, no popups or form submission.
// Scripts remain available for self-contained HTML apps; downloads use the host UI.
// With `web`, the page may also load scripts, styles, images, fonts and media over
// https (CDNs, web fonts, stock photos). fetch/XHR, frames and forms stay blocked.
export function sandboxDocument(text:string,kind:string,web=false) {
  const w=web?' https:':'';
  const policy=`default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' data: blob:${w}; style-src 'unsafe-inline'${w}; img-src data: blob:${w}; font-src data:${w}; media-src data: blob:${w}; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
  const head=`<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html{color-scheme:light}body{margin:16px;font-family:system-ui,sans-serif}${kind==='svg'?'svg{max-width:100%;height:auto}':''}</style>`;
  return `<!doctype html><html><head>${head}</head><body>${text}</body></html>`;
}
export function PreviewPanel({preview,onClose}:{preview:Preview;onClose:()=>void}) {
  const [mode,setMode]=useState<'preview'|'source'>('preview'),[text,setText]=useState(preview.text||''),[error,setError]=useState(''),[wide,setWide]=useState(false),[version,setVersion]=useState(0),[web,setWeb]=useState(false);
  const textual=['html','svg','markdown','text','code'].includes(preview.kind);
  useEffect(()=>{setMode('preview');setError('');setText(preview.text||'');if(preview.text!==undefined||!textual||!preview.url)return;const controller=new AbortController();fetch(preview.url,{signal:controller.signal}).then(r=>{if(!r.ok)throw new Error('File could not be loaded.');return r.text()}).then(setText).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>controller.abort()},[preview]);
  const html=preview.kind==='html'||preview.kind==='svg';
  return <aside className={`artifact-panel ${wide?'artifact-wide':''}`} aria-label="File preview"><header><div><FileText size={16}/><strong title={preview.name}>{preview.name}</strong></div><div>
    <button className="icon-btn" title={wide?'Restore preview size':'Expand preview'} onClick={()=>setWide(!wide)}>{wide?<Minimize2 size={16}/>:<Maximize2 size={16}/>}</button>
    <button className="icon-btn" aria-label="Close preview" onClick={onClose}><X size={18}/></button></div></header>
    <div className="preview-toolbar"><div className="preview-tabs"><button className={mode==='preview'?'active':''} onClick={()=>setMode('preview')}><Eye size={13}/>Preview</button>{textual&&<button className={mode==='source'?'active':''} onClick={()=>setMode('source')}><Code2 size={13}/>Source</button>}</div><div>
    {html&&<button className={`preview-web ${web?'on':''}`} aria-pressed={web} title={web?'Stop loading images, fonts and scripts from the web':'Let this preview load images, fonts and scripts from the web. The sites it loads from see your IP address.'} onClick={()=>setWeb(v=>!v)}><Globe size={13}/>{web?'Web on':'Web off'}</button>}
    {html&&<button className="icon-btn" title="Restart preview" onClick={()=>setVersion(v=>v+1)}><RotateCcw size={14}/></button>}
    {preview.downloadUrl?<a className="icon-btn" aria-label="Download file" href={preview.downloadUrl} download><Download size={15}/></a>:<button className="icon-btn" aria-label="Download file" onClick={()=>downloadText(preview.name,text)}><Download size={15}/></button>}
    </div></div>
    <div className="preview-content">{error?<p role="alert">{error}</p>:mode==='source'||preview.kind==='code'||preview.kind==='text'?<pre className="source-view">{text}</pre>:html?<iframe key={`${version}${web}${text}`} title={`Preview of ${preview.name}`} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={sandboxDocument(text,preview.kind,web)}/>:preview.kind==='markdown'?<div className="markdown preview-markdown"><RichMarkdown text={text}/></div>:preview.kind==='image'?<div className="image-preview"><img src={preview.url} alt={preview.name}/></div>:preview.kind==='audio'?<div className="audio-preview"><FileText size={36}/><h3>{preview.name}</h3><audio controls src={preview.url} preload="metadata"/><p>Audio playback · model input requires a transcript</p></div>:preview.kind==='pdf'&&preview.url?<Suspense fallback={<p>Opening PDF…</p>}><PdfPreview url={preview.url}/></Suspense>:null}</div>
    <footer>{html?(web?'Isolated preview · loads files from the web · other connections blocked':'Isolated preview · external connections blocked'):preview.kind==='pdf'?'Document preview · original file':'Local preview'}<span>{mode==='source'?'Source':preview.kind.toUpperCase()}</span></footer>
  </aside>;
}
