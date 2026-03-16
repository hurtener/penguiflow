import{p as Ae,a as d,s as g,b as Te,u as G,c as a,o as Me,d as V,f as H,i as Q,g as s,e as x,h as Se,j as L,t as K,k as Re,l as Pe,m as Ee,n as X,q as Oe,r as Ie,w as Y,x as ke}from"./index-DvZCQmfE.js";var Le=L('<div class="mcp-app-loading svelte-134bwg1">Loading MCP App...</div>'),ze=L('<div class="mcp-app-error svelte-134bwg1"> </div>'),qe=L('<iframe title="MCP App"></iframe>');function Ne($,c){Ae(c,!0);const ee="2026-01-26";let z=d(c,"artifact_url",3,void 0),R=d(c,"html",3,void 0),u=d(c,"csp",19,()=>({})),C=d(c,"permissions",19,()=>({})),j=d(c,"tool_data",3,void 0),te=d(c,"tool_input",19,()=>({})),A=d(c,"namespace",3,void 0),q=d(c,"session_id",3,void 0),D=d(c,"tenant_id",3,void 0),N=d(c,"user_id",3,void 0),P=d(c,"height",3,"500px"),ne=d(c,"prefers_border",3,!1),oe=d(c,"sandbox",3,void 0),ie=d(c,"onToolCall",3,void 0),W=d(c,"onSendMessage",3,void 0),p=g(void 0),h=g("500px"),y=g(""),w=g(!1),_=g(""),E=g(!1),b=g(Te({}));const se=X(()=>()=>{const e=new Set((oe()??"allow-scripts allow-forms").split(/\s+/).map(t=>t.trim()).filter(Boolean));return e.add("allow-same-origin"),Array.from(e).join(" ")});function re(){const e=["'unsafe-inline'"],t=["'unsafe-inline'"],n=["data:","blob:"],i=["data:"],o=["default-src 'none'"];if(u()?.connect_domains?.length&&o.push(`connect-src ${u().connect_domains.join(" ")}`),u()?.resource_domains?.length){const r=u().resource_domains.join(" ");e.push(r),t.push(r),n.push(r),i.push(r)}return u()?.frame_domains?.length&&o.push(`frame-src ${u().frame_domains.join(" ")}`),o.push(`script-src ${e.join(" ")}`),o.push(`style-src ${t.join(" ")}`),o.push(`img-src ${n.join(" ")}`),o.push(`font-src ${i.join(" ")}`),`<meta http-equiv="Content-Security-Policy" content="${o.join("; ")}">`}function ae(e){const t=re(),n=`
<script>
(function() {
  const MCP_ORIGIN = '*';
  let _jsonrpcId = 0;
  const _pending = new Map();

  window.addEventListener('message', function(event) {
    try {
      const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
      if (!msg || !msg.jsonrpc) return;

      if (msg.id !== undefined && _pending.has(msg.id)) {
        const resolve = _pending.get(msg.id);
        _pending.delete(msg.id);
        resolve(msg.result ?? msg.error);
        return;
      }

      if (msg.method === 'tools/result' || msg.method === 'mcp/tool-result') {
        if (typeof window.onMcpToolResult === 'function') {
          window.onMcpToolResult(msg.params);
        }
        window.dispatchEvent(new CustomEvent('mcp:tool-result', { detail: msg.params }));
      }
      else if (msg.method === 'ui/theme' || msg.method === 'mcp/theme') {
        window.dispatchEvent(new CustomEvent('mcp:theme', { detail: msg.params }));
      }
      else if (msg.method === 'tools/response' || msg.method === 'mcp/tool-response') {
        window.dispatchEvent(new CustomEvent('mcp:tool-response', { detail: msg.params }));
      }
    } catch(e) { /* ignore malformed messages */ }
  });

  window.mcpRequest = function(method, params) {
    return new Promise(function(resolve) {
      const id = ++_jsonrpcId;
      _pending.set(id, resolve);
      parent.postMessage({
        jsonrpc: '2.0',
        id: id,
        method: method,
        params: params
      }, MCP_ORIGIN);
    });
  };

  window.mcpCallTool = function(name, args) {
    return window.mcpRequest('tools/call', { name: name, arguments: args || {} });
  };

  window.mcpSendMessage = function(text) {
    return window.mcpRequest('ui/message', { role: 'user', content: [{ type: 'text', text: text }] });
  };

  window.mcpResize = function(h) {
    return window.mcpRequest('ui/notifications/size-changed', { height: h });
  };
})();
<\/script>`;return e.includes("</head>")?e.replace("</head>",t+n+"</head>"):e.includes("<body>")?e.replace("<body>","<body>"+t+n):t+n+e}function ce(){const e=new URLSearchParams;q()&&e.set("session_id",q()),D()&&e.set("tenant_id",D()),N()&&e.set("user_id",N());const t=e.toString();return t?`?${t}`:""}async function T(e,t){if(!A())throw new Error("MCP App namespace is missing");const n=await fetch(`/apps/${encodeURIComponent(A())}${e}${ce()}`,t);if(!n.ok){const i=await n.text();throw new Error(`Request failed (${n.status}): ${i}`)}return await n.json()}async function de(e,t){const n=await T("/call-tool",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:e,arguments:t??{}})});return n&&typeof n=="object"&&"result"in n?n.result:n}async function ue(){return await T("/tools")}async function J(){return await T("/resources")}async function le(e){return await T("/read-resource",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uri:e})})}function f(e){return e&&typeof e=="object"&&!Array.isArray(e)?e:{}}function U(e,t){const n={...e};for(const[i,o]of Object.entries(t)){const r=n[i];if(o&&typeof o=="object"&&!Array.isArray(o)&&r&&typeof r=="object"&&!Array.isArray(r)){n[i]=U(r,o);continue}n[i]=o}return n}function fe(){return document.documentElement.classList.contains("dark")?"dark":"light"}function me(){return{width:s(p)?.clientWidth||window.innerWidth||0,maxHeight:Number.parseInt(s(h),10)||500}}function pe(){const e={};C()?.camera&&(e.camera={}),C()?.microphone&&(e.microphone={}),C()?.geolocation&&(e.geolocation={}),C()?.clipboard_write&&(e.clipboardWrite={});const t={};return u()?.connect_domains?.length&&(t.connectDomains=u().connect_domains),u()?.resource_domains?.length&&(t.resourceDomains=u().resource_domains),u()?.frame_domains?.length&&(t.frameDomains=u().frame_domains),u()?.base_uri_domains?.length&&(t.baseUriDomains=u().base_uri_domains),{openLinks:{},serverTools:{listChanged:!0},serverResources:{listChanged:!0},logging:{},sandbox:{permissions:e,csp:t},updateModelContext:{text:{},image:{},resource:{},resourceLink:{},structuredContent:{}},message:{text:{},resource:{},resourceLink:{},structuredContent:{}}}}function ge(){const e=Intl.DateTimeFormat().resolvedOptions().timeZone,t=navigator.maxTouchPoints>0;return{theme:fe(),displayMode:"inline",availableDisplayModes:["inline"],containerDimensions:me(),locale:navigator.language,timeZone:e,userAgent:navigator.userAgent,platform:"web",deviceCapabilities:{touch:t,hover:window.matchMedia("(hover: hover)").matches}}}function F(e){if(e&&typeof e=="object"&&!Array.isArray(e)){const t=e;if("content"in t||"structuredContent"in t||"isError"in t||"_meta"in t)return t}return{content:[],structuredContent:e,isError:!1}}function he(e){const t=e?.text;if(typeof t=="string"&&t.trim())return t;const n=e?.content;return Array.isArray(n)?n.map(i=>{if(!i||typeof i!="object")return"";const o=i.text;return typeof o=="string"?o:""}).filter(Boolean).join(`
`):""}function ye(e){if(!e)return null;if(typeof e=="string")try{return JSON.parse(e)}catch{return null}return typeof e=="object"&&e.jsonrpc==="2.0"?e:null}function O(e){if(e==null||typeof e=="string"||typeof e=="number"||typeof e=="boolean")return e;if(Array.isArray(e))return e.map(t=>O(t));if(typeof e=="object"){const t={};for(const[n,i]of Object.entries(e)){if(typeof i=="function"||typeof i=="symbol")continue;const o=O(i);o!==void 0&&(t[n]=o)}return t}return String(e)}function I(e){s(p)?.contentWindow&&s(p).contentWindow.postMessage(O(e),"*")}function l(e,t){e!==void 0&&I({jsonrpc:"2.0",id:e,result:t})}function v(e,t,n){e!==void 0&&I({jsonrpc:"2.0",id:e,error:{code:t,message:n}})}function M(e,t={}){I({jsonrpc:"2.0",method:e,params:t})}function we(){j()!==void 0&&M("tools/result",F(j()))}function _e(){M("ui/notifications/tool-input",{arguments:f(te())}),j()!==void 0&&M("ui/notifications/tool-result",F(j()))}async function B(e){if(!s(p)||e.source!==s(p).contentWindow)return;const t=ye(e.data);if(t)try{switch(t.method){case"ui/initialize":{a(E,!0),l(t.id,{protocolVersion:ee,hostInfo:{name:"PenguiFlow Playground",version:"3.1.1"},hostCapabilities:pe(),hostContext:ge()}),queueMicrotask(()=>{M("ui/notifications/initialized"),_e()});return}case"tools/call":case"mcp/call-tool":{const{name:n,arguments:i}=f(t.params);if(typeof n!="string"||!n){v(t.id,-32602,"Invalid tool name");return}const o=f(i),r=ie()??de;l(t.id,await r(n,o));return}case"tools/list":{l(t.id,await ue());return}case"resources/list":{const n=f(await J());l(t.id,{resources:Array.isArray(n.resources)?n.resources:[]});return}case"resources/templates/list":{const n=f(await J());l(t.id,{resourceTemplates:Array.isArray(n.resourceTemplates)?n.resourceTemplates:[]});return}case"resources/read":{const{uri:n}=f(t.params);if(typeof n!="string"||!n){v(t.id,-32602,"Invalid resource uri");return}l(t.id,await le(n));return}case"ui/message":case"mcp/send-message":{const n=he(f(t.params));n&&W()&&await W()({text:n,...A()?{namespace:A()}:{},...Object.keys(s(b)).length?{modelContext:s(b)}:{}}),l(t.id,{});return}case"ui/open-link":{const{url:n}=f(t.params);if(typeof n!="string"||!n){v(t.id,-32602,"Invalid URL");return}window.open(n,"_blank","noopener,noreferrer"),l(t.id,{});return}case"ui/request-display-mode":{l(t.id,{mode:"inline"});return}case"ui/update-model-context":{a(b,U(s(b),f(t.params)),!0),l(t.id,{});return}case"ui/notifications/size-changed":case"mcp/resize":{const{height:n}=f(t.params);typeof n=="number"?a(h,`${n}px`):typeof n=="string"&&n&&a(h,n,!0),t.id!==void 0&&l(t.id,{});return}case"notifications/message":case"ping":case"ui/notifications/initialized":{t.id!==void 0&&l(t.id,{});return}default:v(t.id,-32601,`Method not found: ${t.method}`)}}catch(n){const i=n instanceof Error?n.message:String(n);v(t.id,-32e3,i)}}function be(){s(E)||we()}G(()=>{a(h,typeof P()=="string"&&P().trim()?P():"500px",!0)}),G(()=>{if(a(E,!1),a(_,""),a(b,{},!0),typeof R()=="string"&&R()){a(y,R()),a(w,!1);return}if(a(y,""),!z()){a(w,!1);return}a(w,!0);let e=!1;return(async()=>{try{const t=await fetch(z());if(e)return;t.ok?a(y,await t.text(),!0):a(_,`Failed to load app: ${t.status}`)}catch(t){if(e)return;a(_,`Failed to load app: ${t}`)}finally{e||a(w,!1)}})(),()=>{e=!0}}),Me(()=>(window.addEventListener("message",B),()=>{typeof window<"u"&&window.removeEventListener("message",B)}));const ve=X(()=>s(y)?ae(s(y)):"");var Z=V(),xe=H(Z);{var Ce=e=>{var t=Le();x(e,t)},je=e=>{var t=V(),n=H(t);{var i=r=>{var m=ze(),S=Pe(m);K(()=>Re(S,s(_))),x(r,m)},o=r=>{var m=qe();let S;Ee(m,k=>a(p,k),()=>s(p)),K(k=>{S=Oe(m,1,"mcp-app-frame svelte-134bwg1",null,S,{bordered:ne()}),Ie(m,`height: ${s(h)};`),Y(m,"sandbox",k),Y(m,"srcdoc",s(ve))},[()=>s(se)()]),ke("load",m,be),x(r,m)};Q(n,r=>{s(_)?r(i):r(o,!1)},!0)}x(e,t)};Q(xe,e=>{s(w)?e(Ce):e(je,!1)})}x($,Z),Se()}export{Ne as default};
