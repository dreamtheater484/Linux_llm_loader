import type {FileKind} from './types';
const textExtensions=new Set('txt log csv tsv json jsonl yaml yml xml toml ini cfg conf env py js jsx ts tsx css scss sh bash zsh ps1 bat c cpp h hpp cs java go rs rb php sql r lua swift kt vue svelte dockerfile gitignore'.split(' '));
export function kindFor(name:string,mime=''):FileKind {
  const ext=name.toLowerCase().split('.').at(-1)||'';
  if(['html','htm'].includes(ext))return 'html';
  if(ext==='svg')return 'svg';
  if(['md','markdown','mdx'].includes(ext))return 'markdown';
  if(ext==='pdf'||mime==='application/pdf')return 'pdf';
  if(['mp3','wav'].includes(ext))return 'audio';
  if(['png','jpg','jpeg','webp','gif'].includes(ext))return 'image';
  if(textExtensions.has(ext)||mime.startsWith('text/'))return ['txt','log','csv','tsv'].includes(ext)?'text':'code';
  throw new Error(`“${name}” is not supported. Attach HTML, Markdown, code, text, PDF, an image, or MP3/WAV audio.`);
}
export async function pdfLibrary() {
  const pdf=await import('pdfjs-dist');
  pdf.GlobalWorkerOptions.workerSrc=new URL('pdfjs-dist/build/pdf.worker.min.mjs',import.meta.url).href;
  return pdf;
}
export function dataUrl(file:Blob):Promise<string> {return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('Could not read the file.'));reader.readAsDataURL(file)})}
async function convertImage(file:File):Promise<string> {
  const url=URL.createObjectURL(file);
  try{
    const img=new Image();img.src=url;await img.decode();
    const scale=Math.min(1,4096/Math.max(img.naturalWidth,img.naturalHeight));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.naturalWidth*scale));canvas.height=Math.max(1,Math.round(img.naturalHeight*scale));
    if(!canvas.width||!canvas.height)throw new Error('The image is empty.');
    canvas.getContext('2d')!.drawImage(img,0,0,canvas.width,canvas.height);
    return canvas.toDataURL('image/png');
  }finally{URL.revokeObjectURL(url)}
}
export async function prepareFile(file:File) {
  if(file.size>12_000_000||!file.size)throw new Error('Choose a file between 1 byte and 12 MB.');
  const kind=kindFor(file.name,file.type);let text='',image:string|undefined;
  const ext=file.name.toLowerCase().split('.').at(-1);
  const mime=kind==='pdf'?'application/pdf':kind==='audio'?(ext==='wav'?'audio/wav':'audio/mpeg'):kind==='image'?({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp'}[ext!]||file.type):file.type||'text/plain';
  const data=(await dataUrl(file)).split(',')[1];
  if(kind==='image'&&ext==='gif')image=await convertImage(file);
  else if(kind==='pdf'){
    const pdf=await pdfLibrary();const task=pdf.getDocument({data:new Uint8Array(await file.arrayBuffer())});
    const doc=await task.promise;
    try{if(doc.numPages>250)throw new Error('PDFs can contain up to 250 pages. Split this document before attaching it.');
      for(let page=1;page<=doc.numPages;page++){
        const content=await (await doc.getPage(page)).getTextContent();
        text+=`\n[Page ${page}]\n`+content.items.map(item=>'str' in item?item.str+('hasEOL' in item&&item.hasEOL?'\n':' '):'').join('');
        if(text.length>2_000_000)throw new Error('This PDF contains too much text. Split it into smaller documents.');
      }
      // Page markers alone do not count as readable document text.
      if(!text.replace(/\[Page \d+\]/g,'').trim())text='';
    }finally{await task.destroy()}
  }else if(kind!=='audio'&&kind!=='image'){
    text=await file.text();if(text.includes('\u0000'))throw new Error('This appears to be a binary file. Attach a text or source-code file.');
    if(text.length>2_000_000)throw new Error('Text files can contain up to 2 million characters.');
  }
  return {name:file.name,kind,mime,data,text,image};
}
export const acceptedFiles='.html,.htm,.svg,.md,.markdown,.txt,.log,.csv,.tsv,.json,.jsonl,.yaml,.yml,.xml,.toml,.py,.js,.jsx,.ts,.tsx,.css,.scss,.sh,.c,.cpp,.h,.rs,.go,.java,.sql,.pdf,.png,.jpg,.jpeg,.webp,.gif,.mp3,.wav';
