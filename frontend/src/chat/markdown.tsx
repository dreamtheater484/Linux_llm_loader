import {memo,useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import {Check, Copy, Download, ExternalLink, PanelRightOpen} from 'lucide-react';
import {copyText, downloadText} from './types';
import type {Preview} from './types';
import 'katex/dist/katex.min.css';

export function codePreview(text:string,language=''):Preview {
  const lang=language.toLowerCase();
  const kind=['html','htm'].includes(lang)?'html':lang==='svg'?'svg':['md','markdown'].includes(lang)?'markdown':lang==='csv'?'text':'code';
  return {name:`Untitled.${lang==='javascript'?'js':lang==='typescript'?'ts':lang==='python'?'py':lang||'txt'}`,kind,text,language:lang};
}
function CodeBlock({children,onPreview}:{children:any;onPreview?:(p:Preview)=>void}) {
  const [copied,setCopied]=useState(false),[error,setError]=useState('');
  const node=children?.props;const language=/language-([\w+-]+)/.exec(node?.className||'')?.[1]||'text';
  const plain=(value:any):string=>typeof value==='string'?value:Array.isArray(value)?value.map(plain).join(''):value?.props?plain(value.props.children):'';
  const text=plain(node?.children).replace(/\n$/,'');
  return <div className="code-block"><div className="code-toolbar"><span>{language}</span><div>
    <button title="Copy code" onClick={async()=>{try{await copyText(text);setCopied(true);setTimeout(()=>setCopied(false),1600)}catch(e){setError(String(e))}}}>{copied?<Check size={13}/>:<Copy size={13}/>}<span>{copied?'Copied':'Copy'}</span></button>
    <button title="Download code" onClick={()=>downloadText(codePreview(text,language).name,text)}><Download size={13}/></button>
    {onPreview&&<button className="preview-code" onClick={()=>onPreview(codePreview(text,language))}><PanelRightOpen size={13}/>Preview</button>}
  </div></div><pre>{children}</pre>{error&&<small role="alert">{error}</small>}</div>;
}
export const RichMarkdown=memo(function RichMarkdown({text,onPreview}:{text:string;onPreview?:(p:Preview)=>void}) {
  return <ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[[rehypeKatex,{trust:false,strict:'ignore'}],[rehypeHighlight,{detect:false,ignoreMissing:true}]]} components={{
    pre:({children})=><CodeBlock onPreview={onPreview}>{children}</CodeBlock>,
    img:({src,alt})=><a href={typeof src==='string'?src:undefined} target="_blank" rel="noreferrer">{alt||'Image'} <ExternalLink size={12}/></a>,
    a:({children,href})=><a href={href} target="_blank" rel="noreferrer">{children}</a>,
    table:({children})=><div className="table-scroll"><table>{children}</table></div>
  }}>{text}</ReactMarkdown>;
});
