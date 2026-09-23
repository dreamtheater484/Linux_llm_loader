import type {Model, Settings} from '../types';

export const GIB = 2**30;
/** GPU memory kept free for the context cache, activations and the engine itself. */
export const GPU_WORKING = 4.5*GIB;
/** Inflect refuses to load unless this much system RAM stays available. */
export const RAM_HEADROOM = 16*GIB;

export type Hardware = {vram:number|null; ram:number|null; gpuName?:string};
export type FitLevel = 'gpu'|'split'|'slow'|'no'|'unknown';
export type Fit = {level:FitLevel; label:string; detail:string; gpu:number; cpu:number; cpuShare:number};

export const hardwareOf = (hardware:any):Hardware => ({vram:hardware?.gpu?.total_bytes??null, ram:hardware?.ram?.total_bytes??null, gpuName:hardware?.gpu?.name?.replace('NVIDIA GeForce ','')});
export const gb = (bytes:number|null|undefined, digits=1) => bytes==null||!Number.isFinite(bytes)?'—':`${(bytes/GIB).toLocaleString(undefined,{maximumFractionDigits:bytes<10*GIB?digits:0})} GiB`;
const share = (part:number, whole:number) => whole>0?Math.min(100,Math.max(0,Math.ceil(part/whole*100/5)*5)):0;

/** Share of the weights that live in routed experts (0 for dense models). */
export function expertFraction(model:Pick<Model,'bytes'|'experts'|'expert_bytes'|'active_b'>):number {
  if(!model.experts)return 0;
  if(model.expert_bytes&&model.bytes)return Math.min(.97,model.expert_bytes/model.bytes);
  return .85;
}

/** Will this many bytes of weights run well here? Experts of a mixture-of-experts
 * model can live in system RAM; the rest must fit on the GPU. `host` bytes (an
 * n-gram lookup table) never go to the GPU: engines keep them in system RAM. */
export function assessFit(size:number, experts:number|null|undefined, hw:Hardware, host=0):Fit {
  if(!hw.vram||!hw.ram||!size)return {level:'unknown',label:'Fit unknown',detail:'Waiting for hardware readings.',gpu:0,cpu:0,cpuShare:0};
  const gpuBudget=Math.max(0,hw.vram-GPU_WORKING), ramBudget=Math.max(0,hw.ram-RAM_HEADROOM);
  const device=Math.max(0,size-host), expertBytes=Math.min(device,size*(experts||0));
  const table=host?` The ${gb(host)} n-gram table stays in system RAM.`:'';
  if(device<=gpuBudget){
    if(host>ramBudget)return {level:'no',label:'Too large',detail:`The ${gb(host)} n-gram table needs more system memory than this computer can spare.`,gpu:device,cpu:host,cpuShare:0};
    return {level:'gpu',label:'Fits on your GPU',detail:`All ${gb(device)} of model layers on the graphics card: the fastest option.${table}`,gpu:device,cpu:host,cpuShare:0};
  }
  const cpu=device-gpuBudget, ram=cpu+host;
  if(ram>ramBudget)return {level:'no',label:'Too large',detail:`Needs about ${gb(ram-ramBudget)} more memory than this computer can spare.`,gpu:gpuBudget,cpu:ram,cpuShare:100};
  if(expertBytes>0){
    if(device-expertBytes<=gpuBudget)return {level:'split',label:'Runs well',detail:`Core layers on the GPU, about ${gb(cpu)} of experts in system RAM.${table}`,gpu:gpuBudget,cpu:ram,cpuShare:share(cpu,expertBytes)};
    return {level:'slow',label:'Runs slowly',detail:`Even the always-used layers exceed the GPU; ${gb(cpu)} of them runs from system RAM.${table}`,gpu:gpuBudget,cpu:ram,cpuShare:share(cpu,device)};
  }
  if(cpu/device<=.15)return {level:'split',label:'Mostly on GPU',detail:`About ${gb(cpu)} runs from system RAM, which is somewhat slower.${table}`,gpu:gpuBudget,cpu:ram,cpuShare:share(cpu,device)};
  return {level:'slow',label:'Runs slowly',detail:`Too big for the GPU; about ${gb(cpu)} runs from system RAM.${table}`,gpu:gpuBudget,cpu:ram,cpuShare:share(cpu,device)};
}

export const modelFit = (model:Model, hw:Hardware) => assessFit(model.bytes, expertFraction(model), hw, model.ngram_bytes||0);

/** Where the chosen settings place the weights, rounding the CPU share the way
 * each engine does (loader/engines.py). The n-gram table is never on the GPU. */
export function placement(model:Model, settings:Settings) {
  const engine=settings.engine==='auto'?model.engines[0]:settings.engine, p=settings.cpu_percent/100;
  const table=model.ngram_bytes||0, device=Math.max(0,model.bytes-table);
  const experts=model.experts?Math.min(device,model.expert_bytes||model.bytes*expertFraction(model)):0;
  const layers=Math.max(1,(model.layers||1)-(model.nextn_layers||0)), cpuLayers=Math.min(layers,Math.ceil(layers*p));
  // ExLlamaV3 can only move experts off the GPU; a dense model stays whole.
  const cpu=engine==='exl3'?(model.experts?experts*Math.min(model.experts,Math.ceil(model.experts*p/8)*8)/model.experts:0)
    :engine==='gguf'?(settings.gguf_offload==='experts'?experts:device)*cpuLayers/layers
    :Math.min(device,model.bytes*p);
  // Without "keep in RAM" the table is read from storage as needed.
  const streamed=model.ngram&&!settings.ngram_ram?table:0;
  return {gpu:device-cpu,cpu:cpu+table-streamed,table:table-streamed,streamed};
}

export const fitRank:Record<FitLevel,number> = {gpu:0,split:1,slow:2,no:3,unknown:4};
