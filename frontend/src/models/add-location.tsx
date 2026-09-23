import {useEffect, useState} from 'react';
import {ArrowUp, Box, FileBox, Folder, FolderPlus, HardDrive, Home, Info, LoaderCircle, Trash2} from 'lucide-react';
import {Modal} from '../ui';
import {gb} from './fit';

type Request = (path:string, body?:unknown, method?:string) => Promise<any>;
type Entry = {name:string;path:string;kind:'folder'|'model-folder'|'gguf-folder'|'gguf';size?:number};
type Listing = {path:string;parent:string|null;entries:Entry[];shortcuts:{name:string;path:string}[]};

/** Register models where they already are. Nothing is copied or moved. */
export function AddLocationDialog({request,locations,root,onClose,onInventory}:{request:Request;locations:string[];root:string;onClose:()=>void;onInventory:(inventory:any,added?:string)=>void}) {
  const [listing,setListing]=useState<Listing|null>(null),[typed,setTyped]=useState(''),[error,setError]=useState(''),[working,setWorking]=useState(false);
  const browse=async(path:string)=>{setError('');try{const data=await request(`/api/library/browse?path=${encodeURIComponent(path)}`);setListing(data);setTyped(data.path)}catch(e){setError((e as Error).message)}};
  useEffect(()=>{browse('')},[]);
  const add=async(path:string)=>{setWorking(true);setError('');try{onInventory(await request('/api/library/locations',{path}),path)}catch(e){setError((e as Error).message)}finally{setWorking(false)}};
  const remove=async(path:string)=>{setWorking(true);setError('');try{onInventory(await request('/api/library/locations/remove',{path}))}catch(e){setError((e as Error).message)}finally{setWorking(false)}};
  const hasModels=listing?.entries.some(e=>e.kind!=='folder');
  return <Modal title="Add models from this computer" onClose={onClose} className="add-location-dialog">
    <p className="dialog-description">Point Inflect at a folder or GGUF file you already have. Models stay where they are; nothing is copied or moved. Anything inside your model folder ({root.split('/').slice(-2).join('/')}) is found automatically.</p>
    <form className="location-path" onSubmit={e=>{e.preventDefault();browse(typed)}}><input aria-label="Folder path" spellCheck={false} value={typed} onChange={e=>setTyped(e.target.value)} placeholder="/path/to/models"/><button className="secondary" type="submit">Go</button></form>
    {listing&&<div className="location-shortcuts">{listing.shortcuts.map(s=><button key={s.path} className={listing.path===s.path?'chosen':''} onClick={()=>browse(s.path)}>{s.name==='Home'?<Home size={13}/>:<HardDrive size={13}/>}{s.name}</button>)}</div>}
    <div className="location-browser">
      {!listing?<p className="settings-help"><LoaderCircle size={15} className="spin"/>Opening folder…</p>:<>
        {listing.parent&&<button className="location-entry up" onClick={()=>browse(listing.parent!)}><ArrowUp size={15}/><span>Up one level</span></button>}
        {listing.entries.map(entry=><div key={entry.path} className={`location-entry ${entry.kind}`}>
          <button className="location-open" disabled={entry.kind==='gguf'} onClick={()=>browse(entry.path)}>{entry.kind==='folder'?<Folder size={15}/>:entry.kind==='gguf'?<FileBox size={15}/>:<Box size={15}/>}<span>{entry.name}</span>{entry.kind!=='folder'&&<small>{entry.kind==='gguf'?`GGUF · ${gb(entry.size)}`:entry.kind==='gguf-folder'?'Contains GGUF models':'Model folder'}</small>}</button>
          {entry.kind!=='folder'&&<button className="secondary small" disabled={working} onClick={()=>add(entry.path)}><FolderPlus size={13}/>Add</button>}
        </div>)}
        {!listing.entries.length&&<p className="settings-help">This folder is empty.</p>}
      </>}
    </div>
    {error&&<div role="alert" className="dialog-error">{error}</div>}
    {locations.length>0&&<div className="added-locations"><strong>Added locations</strong>{locations.map(path=><div key={path}><code title={path}>{path}</code><button className="icon-btn" disabled={working} aria-label={`Remove ${path} from the library`} title="Remove from library (files are not deleted)" onClick={()=>remove(path)}><Trash2 size={14}/></button></div>)}</div>}
    <div className="dialog-actions"><span className="settings-help"><Info size={14}/>{hasModels?'Add a model above, or the whole folder to include everything inside it.':'Open a folder that contains models.'}</span><button className="secondary" onClick={onClose}>Close</button><button className="primary" disabled={working||!listing} onClick={()=>listing&&add(listing.path)}>{working?<LoaderCircle size={15} className="spin"/>:<FolderPlus size={15}/>}Add this folder</button></div>
  </Modal>;
}
