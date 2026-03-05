import{p as se,a as i,s as h,b as O,H as ne,d as p,J as oe,D as k,A as L,i as N,g as a,n as g,r as ie,f as b,t as J,m as ae,e as re,K as de,c as z,l as ce,k as me,F as D,L as le}from"./index-DH_8uZM6.js";var ue=b('<div class="mcp-app-loading svelte-134bwg1">Loading MCP App...</div>'),pe=b('<div class="mcp-app-error svelte-134bwg1"> </div>'),fe=b('<iframe title="MCP App"></iframe>');function ge(F,o){se(o,!0);let _=i(o,"artifact_url",3,void 0),j=i(o,"html",3,void 0),m=i(o,"csp",19,()=>({})),W=i(o,"permissions",19,()=>({})),C=i(o,"tool_data",3,void 0),M=i(o,"namespace",3,void 0),x=i(o,"session_id",3,void 0),R=i(o,"tenant_id",3,void 0),S=i(o,"user_id",3,void 0),G=i(o,"height",3,"500px"),U=i(o,"prefers_border",3,!1),T=i(o,"sandbox",3,void 0),B=i(o,"onToolCall",3,void 0),q=i(o,"onSendMessage",3,void 0),l=h(void 0),E=h(O(G())),v=h(O(j()??"")),A=h(!!_()),w=h("");const K=z(()=>()=>{if(T())return T();const e=["allow-scripts","allow-forms"];return W()?.clipboard_write&&e.push("allow-same-origin"),e.join(" ")});function H(){const e=["default-src 'none'","script-src 'unsafe-inline'","style-src 'unsafe-inline'"];if(m()?.connect_domains?.length&&e.push(`connect-src ${m().connect_domains.join(" ")}`),m()?.resource_domains?.length){const t=m().resource_domains.join(" ");e.push(`script-src 'unsafe-inline' ${t}`),e.push(`img-src ${t}`),e.push(`font-src ${t}`),e.push(`style-src 'unsafe-inline' ${t}`)}return m()?.frame_domains?.length&&e.push(`frame-src ${m().frame_domains.join(" ")}`),`<meta http-equiv="Content-Security-Policy" content="${e.join("; ")}">`}function Q(e){const t=H(),s=`
<script>
(function() {
  const MCP_ORIGIN = '*';
  let _jsonrpcId = 0;
  const _pending = new Map();

  // Receive messages from host
  window.addEventListener('message', function(event) {
    try {
      const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
      if (!msg || !msg.jsonrpc) return;

      // Handle responses to our requests
      if (msg.id !== undefined && _pending.has(msg.id)) {
        const resolve = _pending.get(msg.id);
        _pending.delete(msg.id);
        resolve(msg.result ?? msg.error);
        return;
      }

      // Handle host-initiated methods
      if (msg.method === 'tools/result' || msg.method === 'mcp/tool-result') {
        if (typeof window.onMcpToolResult === 'function') {
          window.onMcpToolResult(msg.params);
        }
        // Also dispatch as custom event
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

  // Send JSON-RPC request to host
  window.mcpRequest = function(method, params) {
    return new Promise(function(resolve) {
      const id = ++_jsonrpcId;
      _pending.set(id, resolve);
      parent.postMessage(JSON.stringify({
        jsonrpc: '2.0',
        id: id,
        method: method,
        params: params
      }), MCP_ORIGIN);
    });
  };

  // Convenience methods
  window.mcpCallTool = function(name, args) {
    return window.mcpRequest('tools/call', { name: name, arguments: args || {} });
  };

  window.mcpSendMessage = function(text) {
    return window.mcpRequest('ui/message', { text: text });
  };

  window.mcpRequestTheme = function() {
    return window.mcpRequest('mcp/request-theme', {});
  };

  window.mcpResize = function(h) {
    return window.mcpRequest('mcp/resize', { height: h });
  };
})();
<\/script>`;return e.includes("</head>")?e.replace("</head>",t+s+"</head>"):e.includes("<body>")?e.replace("<body>","<body>"+t+s):t+s+e}async function V(e,t){if(!M())throw new Error("MCP App namespace is missing");const s=new URLSearchParams;x()&&s.set("session_id",x()),R()&&s.set("tenant_id",R()),S()&&s.set("user_id",S());const d=s.toString(),f=`/apps/${encodeURIComponent(M())}/call-tool${d?`?${d}`:""}`,r=await fetch(f,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:e,arguments:t??{}})});if(!r.ok){const u=await r.text();throw new Error(`Tool call failed (${r.status}): ${u}`)}const n=await r.json();return n&&typeof n=="object"&&"result"in n?n.result:n}function P(e){if(!(!a(l)||e.source!==a(l).contentWindow))try{const t=typeof e.data=="string"?JSON.parse(e.data):e.data;if(!t||t.jsonrpc!=="2.0")return;if(t.method==="tools/call"||t.method==="mcp/call-tool"){const{name:s,arguments:d}=t.params??{};if(typeof s!="string"||!s){c({jsonrpc:"2.0",id:t.id,error:{code:-32602,message:"Invalid tool name"}});return}const f=d&&typeof d=="object"&&!Array.isArray(d)?d:{};(B()??V)(s,f).then(n=>{c({jsonrpc:"2.0",id:t.id,result:n})}).catch(n=>{c({jsonrpc:"2.0",id:t.id,error:{code:-1,message:n.message}})})}else if(t.method==="ui/message"||t.method==="mcp/send-message"){const s=t.params?.text;s&&q()&&q()(s),c({jsonrpc:"2.0",id:t.id,result:{ok:!0}})}else if(t.method==="mcp/request-theme")c({jsonrpc:"2.0",id:t.id,result:{theme:document.documentElement.classList.contains("dark")?"dark":"light"}});else if(t.method==="mcp/resize"){const s=t.params?.height;s&&p(E,typeof s=="number"?`${s}px`:s,!0),c({jsonrpc:"2.0",id:t.id,result:{ok:!0}})}}catch{}}function c(e){a(l)?.contentWindow&&a(l).contentWindow.postMessage(JSON.stringify(e),"*")}function X(){C()!==void 0&&c({jsonrpc:"2.0",method:"tools/result",params:C()})}function Y(){X()}ne(async()=>{if(window.addEventListener("message",P),_()&&!j())try{const e=await fetch(_());e.ok?p(v,await e.text(),!0):p(w,`Failed to load app: ${e.status}`)}catch(e){p(w,`Failed to load app: ${e}`)}finally{p(A,!1)}}),oe(()=>{typeof window<"u"&&window.removeEventListener("message",P)});const Z=z(()=>a(v)?Q(a(v)):"");var I=k(),$=L(I);{var ee=e=>{var t=ue();g(e,t)},te=e=>{var t=k(),s=L(t);{var d=r=>{var n=pe(),u=re(n);J(()=>ae(u,a(w))),g(r,n)},f=r=>{var n=fe();let u;de(n,y=>p(l,y),()=>a(l)),J(y=>{u=ce(n,1,"mcp-app-frame svelte-134bwg1",null,u,{bordered:U()}),me(n,`height: ${a(E)};`),D(n,"sandbox",y),D(n,"srcdoc",a(Z))},[()=>a(K)()]),le("load",n,Y),g(r,n)};N(s,r=>{a(w)?r(d):r(f,!1)},!0)}g(e,t)};N($,e=>{a(A)?e(ee):e(te,!1)})}g(F,I),ie()}export{ge as default};
