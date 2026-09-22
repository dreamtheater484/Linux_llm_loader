import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/memory-accounting.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022}}).outputText;
const {memoryBreakdown}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const GiB=2**30;
const ram={total_bytes:183.3*GiB,used_bytes:182*GiB,available_bytes:165.7*GiB,accounting:'physical_including_cache_v1'};
const model={resident_bytes:114.4*GiB,file_bytes:110.6*GiB};

test('available headroom retains the loaded model rather than offering its pages for reuse',()=>{
  const result=memoryBreakdown(ram,model,true);
  assert.equal(result.modelBytes,114.4*GiB);
  assert.ok(Math.abs(result.availableBytes/GiB-55.1)<1e-9);
  assert.ok(result.modelBytes+result.availableBytes<=ram.total_bytes);
});
test('anonymous model allocations are not subtracted twice',()=>{
  assert.equal(memoryBreakdown(ram,{resident_bytes:20*GiB,file_bytes:0},true).availableBytes,ram.total_bytes-20*GiB);
  const pressured={...ram,available_bytes:100*GiB};
  assert.equal(memoryBreakdown(pressured,{resident_bytes:20*GiB,file_bytes:0},true).availableBytes,100*GiB);
});
test('unknown, inconsistent or legacy measurements do not invent available memory',()=>{
  for(const m of [undefined,{resident_bytes:114*GiB},{...model,file_bytes:NaN},{...model,file_bytes:120*GiB},{...model,resident_bytes:190*GiB}]){
    assert.equal(memoryBreakdown(ram,m,true).availableBytes,null);
  }
  assert.equal(memoryBreakdown({...ram,accounting:undefined},model,true).availableBytes,null);
  assert.equal(memoryBreakdown(undefined,model,true).availableBytes,null);
});
test('unload ignores stale model samples and restores system headroom',()=>{
  assert.deepEqual(memoryBreakdown(ram,model,false),{modelBytes:0,availableBytes:ram.available_bytes});
});
test('headroom cannot be negative or exceed capacity remaining after the model',()=>{
  assert.equal(memoryBreakdown({...ram,available_bytes:GiB},model,true).availableBytes,0);
  assert.equal(memoryBreakdown({...ram,available_bytes:400*GiB},model,true).availableBytes,ram.total_bytes-model.resident_bytes);
});
