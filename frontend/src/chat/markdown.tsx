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
/** Language for a fence written without one (or as plain text): recognise HTML and SVG from the code itself. */
const HTML_START=/^<(?:a|article|aside|body|br|button|div|footer|form|h[1-6]|head|header|hr|img|input|label|li|link|main|meta|nav|ol|p|script|section|span|style|table|title|ul)[\s>/]/i;
export function sniffLanguage(text:string):string|null {
  const head=text.trimStart().slice(0,400);
  if(/^<svg[\s>]/i.test(head)||/^<\?xml[^>]*>\s*<svg[\s>]/i.test(head))return 'svg';
  if(/^<!doctype html/i.test(head)||/<html[\s>]/i.test(text.slice(0,4000)))return 'html';
  const lines=text.split('\n').map(l=>l.trim()).filter(Boolean);
  const tagged=lines.filter(l=>/^<\/?[a-z][\w-]*(?:[\s>/]|$)/i.test(l)).length;
  // A closing tag, or HTML's own elements (a head of <link>/<meta> lines has none), rules out other markup.
  return lines.length&&tagged/lines.length>=.5&&(/<\/[a-z][\w-]*>/i.test(text)||HTML_START.test(lines[0]))?'html':null;
}
function CodeBlock({children,onPreview,writing}:{children:any;onPreview?:(p:Preview)=>void;writing?:boolean}) {
  const [copied,setCopied]=useState(false),[error,setError]=useState('');
  const node=children?.props;const declared=/language-([\w+-]+)/.exec(node?.className||'')?.[1];
  const plain=(value:any):string=>typeof value==='string'?value:Array.isArray(value)?value.map(plain).join(''):value?.props?plain(value.props.children):'';
  const text=plain(node?.children).replace(/\n$/,'');
  const language=(!declared||['text','txt','plaintext','plain'].includes(declared.toLowerCase())?sniffLanguage(text):null)||declared||'text', lines=text?text.split('\n').length:0;
  return <div className={`code-block ${writing?'writing':''}`}><div className="code-toolbar"><span className="code-meta">{language}<span className="code-lines" aria-live={writing?'polite':undefined}>{writing&&<><i/>Writing · </>}{lines.toLocaleString()} {lines===1?'line':'lines'}</span></span><div>
    <button title="Copy code" onClick={async()=>{try{await copyText(text);setCopied(true);setTimeout(()=>setCopied(false),1600)}catch(e){setError(String(e))}}}>{copied?<Check size={13}/>:<Copy size={13}/>}<span>{copied?'Copied':'Copy'}</span></button>
    <button title="Download code" onClick={()=>downloadText(codePreview(text,language).name,text)}><Download size={13}/></button>
    {onPreview&&<button className="preview-code" onClick={()=>onPreview(codePreview(text,language))}><PanelRightOpen size={13}/>Preview</button>}
  </div></div><pre>{children}</pre>{error&&<small role="alert">{error}</small>}</div>;
}
/** `live` marks a reply that is still being generated: the code block it ends inside is still being written. */
export const RichMarkdown=memo(function RichMarkdown({text,onPreview,live}:{text:string;onPreview?:(p:Preview)=>void;live?:boolean}) {
  const open=!!live&&(text.match(/^ {0,3}(?:```|~~~)/gm)||[]).length%2===1, end=text.trimEnd().length;
  return <ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[[rehypeKatex,{trust:false,strict:'ignore'}],[rehypeHighlight,{detect:false,ignoreMissing:true}]]} components={{
    pre:({children,node})=><CodeBlock onPreview={onPreview} writing={open&&(node?.position?.end?.offset??0)>=end}>{children}</CodeBlock>,
    img:({src,alt})=><a href={typeof src==='string'?src:undefined} target="_blank" rel="noreferrer">{alt||'Image'} <ExternalLink size={12}/></a>,
    a:({children,href})=><a href={href} target="_blank" rel="noreferrer">{children}</a>,
    table:({children})=><div className="table-scroll"><table>{children}</table></div>
  }}>{text}</ReactMarkdown>;
});
