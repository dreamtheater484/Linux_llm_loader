import{c as m,r,p as v,j as t,L as j}from"./index-COeS9Jgg.js";/**
 * @license lucide-react v0.468.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const P=m("ChevronLeft",[["path",{d:"m15 18-6-6 6-6",key:"1wnfg3"}]]);/**
 * @license lucide-react v0.468.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const y=m("ChevronRight",[["path",{d:"m9 18 6-6-6-6",key:"mthhwq"}]]);function C({url:f}){const[a,p]=r.useState(null),[c,d]=r.useState(1),[g,u]=r.useState(""),[w,l]=r.useState(!0),i=r.useRef(null);return r.useEffect(()=>{let e=!1,n,s;return u(""),l(!0),p(null),d(1),(async()=>{try{const o=await v();if(e)return;s=o.getDocument({url:f}),n=await s.promise,e?await s.destroy():p(n)}catch(o){e||u(String(o))}})(),()=>{e=!0,s?.destroy()}},[f]),r.useEffect(()=>{if(!a)return;let e=!1,n;return l(!0),(async()=>{try{const s=await a.getPage(c);if(e||!i.current)return;const o=s.getViewport({scale:1}),x=Math.min(1400,i.current.parentElement.clientWidth*window.devicePixelRatio-32),h=s.getViewport({scale:Math.max(.2,x/o.width)});i.current.width=h.width,i.current.height=h.height,n=s.render({canvas:i.current,viewport:h}),await n.promise,e||l(!1)}catch(s){e||(u(String(s)),l(!1))}})(),()=>{e=!0,n?.cancel()}},[a,c]),t.jsxs("div",{className:"pdf-preview",children:[t.jsxs("div",{className:"pdf-controls",children:[t.jsx("button",{"aria-label":"Previous PDF page",disabled:c<=1,onClick:()=>d(e=>e-1),children:t.jsx(P,{size:16})}),t.jsxs("span",{children:["Page ",c," of ",a?.numPages||"…"]}),t.jsx("button",{"aria-label":"Next PDF page",disabled:!a||c>=a.numPages,onClick:()=>d(e=>e+1),children:t.jsx(y,{size:16})})]}),g?t.jsx("p",{role:"alert",children:g}):t.jsxs(t.Fragment,{children:[w&&t.jsx(j,{size:18,className:"spin"}),t.jsx("canvas",{ref:i})]})]})}export{C as default};
