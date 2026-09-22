import type {Model} from './types';

const names:Record<string,string>={default:'Model default',off:'Off',on:'On',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra high (xhigh)',max:'Maximum (max)'};
export function reasoningLabel(value:string,model?:Model|null):string {
  const label=names[value]||value;
  return value==='default'&&model?.reasoning?.default_effort?`${label} · ${names[model.reasoning.default_effort]||model.reasoning.default_effort}`:label;
}
export function ThinkingSelect({model,value,onChange,disabled=false,label='Thinking level'}:{model:Model|null|undefined;value:string;onChange:(value:string)=>void;disabled?:boolean;label?:string}) {
  const options=model?.reasoning?.options||['default'];
  return <select aria-label={label} value={value} disabled={disabled||(options.length<2&&options.includes(value))} onChange={event=>onChange(event.target.value)}>
    {!options.includes(value)&&<option value={value} disabled>{names[value]||value} · unavailable</option>}
    {options.map(option=><option key={option} value={option}>{reasoningLabel(option,model)}</option>)}
  </select>;
}
