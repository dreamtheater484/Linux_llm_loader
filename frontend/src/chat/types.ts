export type FileKind = 'image'|'pdf'|'audio'|'text'|'html'|'svg'|'markdown'|'code';
export type Attachment = {id:string;name:string;kind:FileKind;mime:string;size:number};
export type Message = {id:string;role:'user'|'assistant';content:string;reasoning?:string;attachments:string[];model?:string;created_at:number;metrics?:any;error?:boolean;error_message?:string;cancelled?:boolean};
export type Conversation = {id:string;title:string;model_id:string;created_at:number;updated_at:number;revision:number;messages:Message[];draft:string;draft_attachments:string[];system_prompt:string;used_context:number|null;files:Attachment[];generating:boolean};
export type ChatSummary = Pick<Conversation,'id'|'title'|'model_id'|'created_at'|'updated_at'|'revision'|'generating'> & {message_count:number;preview:string};
export type Preview = {name:string;kind:FileKind;text?:string;url?:string;language?:string;downloadUrl?:string};
export async function request<T=any>(path:string, body?:unknown, method?:string):Promise<T> {
  const response=await fetch(path,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json','X-Inflect-Local':'1'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const value=await response.json().catch(()=>({detail:response.statusText}));throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail))}
  return response.json();
}
export const chatPath=(id:string)=>`/api/conversations/${encodeURIComponent(id)}`;
export const fileUrl=(chatId:string,fileId:string)=>`${chatPath(chatId)}/attachments/${encodeURIComponent(fileId)}`;
export function downloadText(name:string,text:string,type='text/plain') {
  const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function copyText(text:string) {
  if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);return}
  const el=document.createElement('textarea');el.value=text;el.style.position='fixed';el.style.opacity='0';document.body.append(el);el.select();const ok=document.execCommand('copy');el.remove();if(!ok)throw new Error('Select the text and press Ctrl+C to copy.');
}
