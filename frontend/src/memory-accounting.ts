export type ModelMemory = {resident_bytes:number;anonymous_bytes:number;file_bytes:number;shared_bytes:number;swap_bytes:number;sampled_at:number;pid:number};
export type Ram = {used_bytes:number;total_bytes:number;free_bytes:number;available_bytes:number;cache_bytes:number;non_cache_bytes:number;accounting?:string};
const nonnegative=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)&&value>=0;

export function memoryBreakdown(ram:Ram|undefined, model:ModelMemory|undefined, loaded:boolean) {
  const current=ram?.accounting==='physical_including_cache_v1';
  const validSystem=current&&nonnegative(ram?.total_bytes)&&nonnegative(ram?.used_bytes)&&ram.used_bytes<=ram.total_bytes;
  // Model and system samples have different cadences. Inconsistent samples stay unknown.
  const resident=model?.resident_bytes;
  const modelBytes=!loaded?0:validSystem&&nonnegative(resident)&&resident<=ram!.used_bytes?resident:null;
  const fileBytes=!loaded?0:model?.file_bytes;
  let availableBytes:number|null=null;
  if(validSystem&&modelBytes!=null&&nonnegative(ram?.available_bytes)&&nonnegative(fileBytes)&&fileBytes<=modelBytes){
    // MemAvailable already excludes anonymous/shared allocations but can count
    // resident file-backed model pages as reclaimable. Reserve those pages too.
    // Subtract all model file PSS conservatively: locked/shared file pages and
    // kernel reserves can make this underestimate headroom. It is not a fit guarantee.
    availableBytes=Math.max(0,Math.min(ram!.total_bytes-modelBytes,ram!.available_bytes-fileBytes));
  }
  return {modelBytes,availableBytes};
}
