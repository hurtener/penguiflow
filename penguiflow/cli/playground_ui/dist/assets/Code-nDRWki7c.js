import{p as U,a,j as v,i as b,l as i,y as s,z as V,f as j,G as X,g as n,t as u,n as p,q as w,H as q,e as o,k as A,A as Y,r as Z,h as $,D as ee}from"./index-B3zdGoIQ.js";var te=v('<button class="svelte-1wmt5tz">Copy</button>'),ae=v('<div class="code-header svelte-1wmt5tz"><span> </span> <!></div>'),se=v(`
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
  </pre></div>`);function de(D,e){U(e,!0);let x=a(e,"code",3,""),z=a(e,"language",3,void 0),y=a(e,"filename",3,void 0),F=a(e,"showLineNumbers",3,!0),G=a(e,"startLine",3,1),S=a(e,"highlightLines",3,void 0),L=a(e,"diff",3,!1),k=a(e,"maxHeight",3,void 0),T=a(e,"copyable",3,!0);const B=t=>t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"),E=p(()=>x().split(`
`)),I=p(()=>new Set(S()??[])),J=t=>L()?t.startsWith("+")?"diff-add":t.startsWith("-")?"diff-remove":"diff-context":"";function K(){navigator.clipboard.writeText(x())}var _=re(),C=i(_);{var M=t=>{var d=ae(),c=i(d),f=i(c),h=s(c,2);{var g=r=>{var m=te();m.__click=K,o(r,m)};b(h,r=>{T()&&r(g)})}u(()=>A(f,y())),o(t,d)};b(C,t=>{y()&&t(M)})}var H=s(C,2),O=s(i(H));V(O,17,()=>n(E),Y,(t,d,c)=>{const f=p(()=>G()+c);var h=ie(),g=s(j(h)),r=s(i(g));{var m=l=>{var W=se(),Q=s(j(W)),R=i(Q);u(()=>A(R,n(f))),o(l,W)};b(r,l=>{F()&&l(m)})}var N=s(r,2),P=s(i(N));X(P,()=>B(n(d))),u(l=>{w(g,1,l,"svelte-1wmt5tz"),w(N,1,q(z()?`lang-${z()}`:""),"svelte-1wmt5tz")},[()=>`code-line ${n(I).has(n(f))?"highlight":""} ${J(n(d))}`]),o(t,h)}),u(()=>{Z(_,k()?`max-height: ${k()}`:""),w(H,1,q(L()?"diff":""),"svelte-1wmt5tz")}),o(D,_),$()}ee(["click"]);export{de as default};
