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
 * model can live in system RAM; the rest must fit on the GPU. */
export function assessFit(size:number, experts:number|null|undefined, hw:Hardware):Fit {
  if(!hw.vram||!hw.ram||!size)return {level:'unknown',label:'Fit unknown',detail:'Waiting for hardware readings.',gpu:0,cpu:0,cpuShare:0};
  const gpuBudget=Math.max(0,hw.vram-GPU_WORKING), ramBudget=Math.max(0,hw.ram-RAM_HEADROOM), fraction=experts||0;
  if(size<=gpuBudget)return {level:'gpu',label:'Fits on your GPU',detail:`All ${gb(size)} on the graphics card: the fastest option.`,gpu:size,cpu:0,cpuShare:0};
  const cpu=size-gpuBudget;
  if(cpu>ramBudget)return {level:'no',label:'Too large',detail:`Needs about ${gb(cpu-ramBudget)} more memory than this computer can spare.`,gpu:gpuBudget,cpu,cpuShare:100};
  if(fraction>0){
    const core=size*(1-fraction);
    if(core<=gpuBudget)return {level:'split',label:'Runs well',detail:`Core layers on the GPU, about ${gb(cpu)} of experts in system RAM.`,gpu:gpuBudget,cpu,cpuShare:share(cpu,size*fraction)};
    return {level:'slow',label:'Runs slowly',detail:`Even the always-used layers exceed the GPU; ${gb(cpu)} runs from system RAM.`,gpu:gpuBudget,cpu,cpuShare:share(cpu,size)};
  }
  if(cpu/size<=.15)return {level:'split',label:'Mostly on GPU',detail:`About ${gb(cpu)} runs from system RAM, which is somewhat slower.`,gpu:gpuBudget,cpu,cpuShare:share(cpu,size)};
  return {level:'slow',label:'Runs slowly',detail:`Too big for the GPU; about ${gb(cpu)} runs from system RAM.`,gpu:gpuBudget,cpu,cpuShare:share(cpu,size)};
}

export const modelFit = (model:Model, hw:Hardware) => assessFit(model.bytes, expertFraction(model), hw);

/** Where the chosen CPU share places the weights, following each engine's rules. */
export function placement(model:Model, settings:Settings) {
  const engine=settings.engine==='auto'?model.engines[0]:settings.engine, p=settings.cpu_percent/100;
  const experts=model.experts?(model.expert_bytes||model.bytes*expertFraction(model)):0;
  const cpu=engine==='exl3'?experts*p:engine==='gguf'&&settings.gguf_offload==='experts'?experts*p:model.bytes*p;
  return {gpu:Math.max(0,model.bytes-cpu),cpu};
}

export const fitRank:Record<FitLevel,number> = {gpu:0,split:1,slow:2,no:3,unknown:4};
