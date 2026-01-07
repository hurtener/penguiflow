import{p as U,a,f as v,i as b,e as i,h as s,j as V,A as j,B as X,g as n,t as u,c as p,l as x,C as A,n as o,m as B,o as Y,k as Z,r as $,x as ee}from"./index-CnkXvaOM.js";var te=v('<button class="svelte-1wmt5tz">Copy</button>'),ae=v('<div class="code-header svelte-1wmt5tz"><span> </span> <!></div>'),se=v(`
          <span class="line-number svelte-1wmt5tz"> </span>
        `,1),ie=v(`
      
      <div>
        <!>
        <code>
          <!>
        </code>
      </div>
    `,1),re=v(`<div class="code-block svelte-1wmt5tz"><!> <pre>
    <!>
  </pre></div>`);function de(F,e){U(e,!0);let w=a(e,"code",3,""),z=a(e,"language",3,void 0),L=a(e,"filename",3,void 0),S=a(e,"showLineNumbers",3,!0),T=a(e,"startLine",3,1),q=a(e,"highlightLines",3,void 0),k=a(e,"diff",3,!1),y=a(e,"maxHeight",3,void 0),D=a(e,"copyable",3,!0);const E=t=>t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"),G=p(()=>w().split(`
`)),I=p(()=>new Set(q()??[])),J=t=>k()?t.startsWith("+")?"diff-add":t.startsWith("-")?"diff-remove":"diff-context":"";function K(){navigator.clipboard.writeText(w())}var _=re(),C=i(_);{var M=t=>{var d=ae(),c=i(d),f=i(c),h=s(c,2);{var m=r=>{var g=te();g.__click=K,o(r,g)};b(h,r=>{D()&&r(m)})}u(()=>B(f,L())),o(t,d)};b(C,t=>{L()&&t(M)})}var H=s(C,2),O=s(i(H));V(O,17,()=>n(G),Y,(t,d,c)=>{const f=p(()=>T()+c);var h=ie(),m=s(j(h)),r=s(i(m));{var g=l=>{var W=se(),Q=s(j(W)),R=i(Q);u(()=>B(R,n(f))),o(l,W)};b(r,l=>{S()&&l(g)})}var N=s(r,2),P=s(i(N));X(P,()=>E(n(d))),u(l=>{x(m,1,l,"svelte-1wmt5tz"),x(N,1,A(z()?`lang-${z()}`:""),"svelte-1wmt5tz")},[()=>`code-line ${n(I).has(n(f))?"highlight":""} ${J(n(d))}`]),o(t,h)}),u(()=>{Z(_,y()?`max-height: ${y()}`:""),x(H,1,A(k()?"diff":""),"svelte-1wmt5tz")}),o(F,_),$()}ee(["click"]);export{de as default};
