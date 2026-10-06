/* ============================================================
   TELORA customer interface: entry, shell, pages.
   Three pages: My Plan (landing), Design My Solution (the core), Requests.
   Every page reads through api.js and is driven by the active
   customer session (customer_id), never by hard-coded data.
   ============================================================ */

const App = (() => {

const A={sort:'new',role:null,customers:[],me:null,plan:null,requests:[],page:'plan',entry:'roles',q:'',seen:new Set(),justApplied:null,logoDone:false};
/* first visit of a page for this customer plays its entrance once; later visits are still */
let FX={first:false,applied:null};
const PAGES=[['plan','My Plan','plan'],['design','Design My Solution','puzzle'],['requests','Requests','requests']];
const STATUS={processing:'Processing',awaiting_selection:'Awaiting selection',applying:'Applying',completed:'Completed',cancelled:'Cancelled',needs_adjustment:'Needs adjustment',failed:'Failed'};
const ST_ICON={processing:'reset',awaiting_selection:'puzzle',applying:'reset',completed:'check',cancelled:'minus',needs_adjustment:'alert',failed:'x'};
const nSols=n=>`${n} solution${n===1?'':'s'}`;
const $=s=>document.querySelector(s);
const root=()=>document.getElementById('root');
/* what the pages see: the live session data */
const V=()=>A;

/* ---------- shared bits ---------- */
const matches=(c,q)=>!q||c.name.toLowerCase().includes(q)||c.customer_id.toLowerCase().includes(q);
function customerRows(q,current){
  const list=A.customers.filter(c=>matches(c,q.trim().toLowerCase()));
  if(!list.length)return `<li class="c-empty">No customer matches “${esc(q)}”.</li>`;
  return list.map(c=>`<li><button type="button" class="c-row${c.customer_id===current?' on':''}" data-cust="${esc(c.customer_id)}"${c.customer_id===current?' aria-current="true"':''}><span class="av">${esc(initials(c.name))}</span><span class="cn">${esc(c.name)}</span><span class="cid">${esc(c.customer_id)}</span></button></li>`).join('');
}
const demoBadge=`<span class="demo-badge" title="Demo Mode lists every customer in the dataset. Production uses a real sign-in.">Demo Mode</span>`;
function statusPill(s){const ic=ST_ICON[s];return `<span class="status s-${s}">${ic?svgIcon(ic):''}${STATUS[s]||esc(s)}</span>`;}
function requestChange(r){
  if(r.status==='processing')return `<span class="muted">TELORA is building solutions</span>`;
  if(r.status==='applying')return `<span class="muted">Applying your selected plan</span>`;
  if(r.status==='awaiting_selection')return `<span class="muted">${nSols(r.solutions_count)} ready</span>`;
  if(r.status==='completed'&&r.new_plan)return `<span>${esc(r.previous_plan?r.previous_plan.name:'—')}</span><span class="arr" aria-label="changed to">${svgIcon('right')}</span><b>${esc(r.new_plan.name)}</b>`;
  return `<span class="muted">No change applied</span>`;
}

/* ---------- entry ---------- */
function renderEntry(){
  const roles=`<h1>Who’s signing in?</h1><p class="lede">Your needs. Your pieces. Your solution. Choose how you want to open TELORA.</p>
    <div class="roles">
      <button type="button" class="role" data-role="customer"><span class="r-ico">${svgIcon('user')}</span><b>Customer</b><small>Open a customer’s account</small></button>
      <button type="button" class="role" data-role="admin"><span class="r-ico">${svgIcon('shield')}</span><b>Admin</b><small>Operations console</small></button>
    </div>`;
  const cust=`<button type="button" class="back" data-entry="roles">${svgIcon('left')}Back</button>
    <h1>Choose a customer</h1><p class="lede">Pick a customer to see their pieces.</p>
    <label class="search">${svgIcon('search')}<input id="c-search" type="search" placeholder="Search by name or ID" autocomplete="off" value="${esc(A.q)}" aria-label="Search customers by name or ID"></label>
    <ul class="clist" id="clist" aria-label="Customers">${customerRows(A.q)}</ul>
    <p class="count-note">${A.customers.length} customers in the dataset</p>`;
  root().innerHTML=`<div class="entry"><div class="entry-card fade${A.logoDone||RM?'':' logo-in'}">
    <div class="entry-top">${logoHTML()}${demoBadge}</div>${A.entry==='roles'?roles:cust}</div></div>`;
  A.logoDone=true;
  if(A.entry==='customers')$('#c-search').focus();
}
/* ---------- shell ---------- */
/* brand illustration: three floating pieces above the line "Your needs. Your pieces. Your solution." */
function sloganArt(){
  const P=[[byId('data'),30,22,74,62,[1,1,0,1],-14,0],[{c:'#3FD1C4',l:'#DDF7F4',d:'#1FA89C',t:'#08403F'},132,70,68,60,[1,1,1,0],16,1],[{c:'#F9CF7A',l:'#FFF3D9',d:'#E0A93F',t:'#5A3C04'},62,134,72,60,[0,1,1,1],-8,2]];
  let out='';
  P.forEach(([col,x,y,w,h,e,rot,i])=>{const d=piecePath(x,y,w,h,e,15,20),p=piece3d(d,col,'sl'+i,x,y,w,h);
    out+=`<g class="fl fl${i}" style="--d:${i}"><g transform="rotate(${rot} ${x+w/2} ${y+h/2})">${p.under.replace('translate(0 12)','translate(0 9)')}${p.top}</g></g>`;});
  return `<svg viewBox="0 0 230 214" aria-hidden="true">
    <circle cx="30" cy="96" r="64" fill="#E6F4F1"/><circle cx="206" cy="176" r="44" fill="#FCEBDD"/><circle cx="196" cy="62" r="22" fill="#EEEAFB"/>
    <path d="M8 150 C 12 118, 30 104, 52 100" fill="none" stroke="#27AFB5" stroke-width="3" stroke-linecap="round"/>
    <path d="M150 188 C 168 196, 186 196, 200 186" fill="none" stroke="#8FD3BD" stroke-width="3" stroke-linecap="round"/>
    <path d="M168 26 l4 -14" stroke="#27AFB5" stroke-width="3.5" stroke-linecap="round"/><path d="M186 44 l14 -8" stroke="#F4A23A" stroke-width="3.5" stroke-linecap="round"/><path d="M192 64 l12 2" stroke="#F4A23A" stroke-width="3" stroke-linecap="round" opacity=".6"/>
    ${out}</svg>`;
}
function renderShell(){
  root().innerHTML=`<div class="shell">
    <aside class="side">
      <div class="side-top">${logoHTML(true)}</div>
      <nav class="nav" aria-label="Main"><span class="nav-ind" aria-hidden="true"></span>${PAGES.map(([id,label,ic])=>`<button type="button" data-nav="${id}" class="${A.page===id?'on':''}"${A.page===id?' aria-current="page"':''}>${svgIcon(ic)}<span>${label}</span></button>`).join('')}</nav>
      <div class="slogan"><div class="sl-art">${sloganArt()}</div><p class="sl-txt"><span>Your needs.</span><span>Your pieces.</span><span>Your solution.</span></p></div>
      <div class="who">
        ${demoBadge}
        <button type="button" class="who-btn" id="who-btn" aria-haspopup="true" aria-expanded="false" aria-controls="who-pop"><span class="av">${esc(initials(A.me.name))}</span><span class="who-t"><b>${esc(A.me.name)}</b><small>${esc(A.me.customer_id)}</small></span>${svgIcon('updown')}</button>
        <div class="pop" id="who-pop" hidden>
          <label class="search sm">${svgIcon('search')}<input id="w-search" type="search" placeholder="Search by name or ID" autocomplete="off" aria-label="Search customers by name or ID"></label>
          <ul class="clist sm" id="wlist" aria-label="Switch customer"></ul>
        </div>
        <button type="button" class="exit" data-exit aria-label="Exit demo">${svgIcon('logout')}<span>Exit demo</span></button>
      </div>
    </aside>
    <main class="main" id="page"></main>
  </div>`;
  renderPage();moveInd(true);
  if(navRO)navRO.observe($('.nav'));
}
/* the active-page highlight slides between nav items */
function moveInd(instant){
  const ind=$('.nav-ind'),on=$('.nav button.on');if(!ind||!on)return;
  if(!ind.style.width)instant=true;
  if(instant||RM)ind.classList.add('still');
  Object.assign(ind.style,{width:on.offsetWidth+'px',height:on.offsetHeight+'px',transform:`translate(${on.offsetLeft}px,${on.offsetTop}px)`});
  if(instant||RM)requestAnimationFrame(()=>requestAnimationFrame(()=>ind.classList.remove('still')));
}
window.addEventListener('resize',()=>moveInd(true));
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>moveInd(true));
const navRO=window.ResizeObserver?new ResizeObserver(()=>moveInd(true)):null;
/* numbers count up to their value (or from the old price after Apply) */
function countUps(el){
  if(RM)return;
  el.querySelectorAll('[data-count]').forEach(n=>{
    const to=+n.dataset.count,from=+(n.dataset.from||0),dec=Number.isInteger(to)?0:2,t0=performance.now(),D=from?1100:700;
    const fmt=v=>v.toLocaleString('en-US',{minimumFractionDigits:dec,maximumFractionDigits:dec});
    const f=now=>{const t=Math.min(1,(now-t0)/D),e=1-Math.pow(1-t,3);n.textContent=fmt(from+(to-from)*e);if(t<1)requestAnimationFrame(f);};
    n.textContent=fmt(from);requestAnimationFrame(f);});
}
function renderPage(anim=true){
  const el=document.getElementById('page');if(!el)return;
  document.querySelectorAll('.nav [data-nav]').forEach(b=>{const on=b.dataset.nav===A.page;b.classList.toggle('on',on);on?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current');});
  moveInd();
  if(A.page==='design'){Design.mount(el);return;}
  const key=A.me.customer_id+':'+A.page;
  FX={first:anim&&!RM&&!A.seen.has(key),applied:A.page==='plan'&&!RM?A.justApplied:null};
  A.seen.add(key);A.justApplied=null;
  el.innerHTML=`<div class="pg${anim?' fade':''}${FX.first?' fx':''}${FX.applied?' applied':''}">${({plan,requests})[A.page]()}</div>`;
  if(FX.first||FX.applied)countUps(el);
}
function go(page){A.page=page;closePop();renderPage();window.scrollTo({top:0});}

/* ---------- My Plan (landing page) ---------- */
function planPuzzle(p){
  const adds=p.active_addons||[],n=adds.length,H=200,BW=290,AW=250,g=3,k=34,x0=k*1.1+6,y0=14;
  const W=x0+BW+n*(AW+g)+k*1.1+6;let under='',out='';
  const bl=wrapWords(p.base_package.package_name,13),bx=x0,by=y0,id='pz'+(uid++);
  const bv='--d:0;--tx:-34px;--ty:-18px;--r:-5deg';
  /* knobs reach out on the outer left and right, like a piece waiting for the next one */
  const bp=piece3d(piecePath(bx,by,BW,H,[0,1,0,1],26,k),BASE_COL,id+'b',bx,by,BW,H);
  under+=`<g class="hp" style="${bv}">${bp.under}</g>`;
  out+=`<g class="hp" style="${bv}">${bp.top}${iconTile(bx+BW/2,by+54,'data',BASE_COL)}
    ${lines(bx+BW/2,by+H/2+(bl.length>1?16:32),bl,bl.length>1?22:26,BASE_COL.t,800)}${pill(bx+BW/2,by+H-34,'Base package',BASE_COL.t,15)}</g>`;
  adds.forEach((a,i)=>{const x=x0+BW+g+i*(AW+g),st=addonStyle(a.category),al=wrapWords(a.addon_name,14),v=`--d:${i+1};--tx:44px;--ty:${i%2?24:-24}px;--r:${i%2?-6:6}deg`;
    const ap=piece3d(piecePath(x,by,AW,H,[0,1,0,-1],26,k),st.col,id+'a'+i,x,by,AW,H);
    under+=`<g class="hp" style="${v}">${ap.under}</g>`;
    out+=`<g class="hp" style="${v}">${ap.top}${iconTile(x+AW/2+10,by+54,st.icon,st.col)}
      ${lines(x+AW/2+10,by+H/2+(al.length>1?16:32),al,al.length>1?20:24,st.col.t,800)}${pill(x+AW/2+10,by+H-34,'Add-on',st.col.t,15)}</g>`;});
  return `<svg viewBox="0 0 ${W} ${H+y0+26}" role="img" aria-label="Your plan: ${esc(planName(p))}">${under}${out}</svg>`;
}
/* soft decorative pieces (pure ornament) */
function decoPiece(c,w,h,e){return `<svg viewBox="-30 -30 ${w+60} ${h+60}" aria-hidden="true"><path d="${piecePath(0,0,w,h,e,22,30)}" fill="${c}"/></svg>`;}
/* after a TELORA plan change: what the customer paid before, what they pay now, and the difference */
function planChange(p){
  const u=p.updated_by,prev=u.previous_plan,c=prev?priceChange(prev.total_price,p.total_monthly_price):null;
  /* the price change sits where the request number used to be */
  const save=!c?'':c.tone==='same'?`<span class="pc-save same">Same monthly price</span>`
    :`<span class="pc-save ${c.tone}" role="status"><small>${c.tone==='down'?'You save':'You now pay'}</small><b>${money(c.abs)} SAR</b><span>${c.tone==='down'?'less':'more'} per month · ${c.pct}%</span></span>`;
  return `<p class="updated"><span>${svgIcon('check')}Updated by TELORA · ${dateTime(u.applied_at)}</span>${save}</p>`;
}
function plan(){
  const p=V().plan,b=p.base_package,adds=p.active_addons||[],ap=FX.applied;
  const stat=(ic,l,v)=>`<div class="stat"><span class="st-ico">${svgIcon(ic)}</span><span><small>${l}</small><b>${v}</b></span></div>`;
  const hr=new Date().getHours(),greet=hr<12?'Good morning':hr<18?'Good afternoon':'Good evening';
  return `<header class="mp-head">
    <span class="deco d1">${decoPiece('#F7C9B8',120,100,[1,1,0,-1])}</span>
    <span class="hello-av" aria-hidden="true">${esc(initials(A.me.name))}</span>
    <div class="hello-t"><h1>${greet}, ${esc(firstName(A.me.name))}</h1><p>These are the pieces of your plan today. When your needs change, TELORA builds your solution around them.</p></div>
  </header>
  ${p.updated_by?planChange(p):''}
  <section class="panel mp-over" aria-label="Your plan overview">
    <div class="ov-h">
      <span class="ov-ico">${svgIcon('plan')}</span>
      <div><h2>Your Plan Overview</h2><p>Your pieces: a base package and any add-ons.</p></div>
      <span class="live"><i></i>Active Plan</span>
    </div>
    <div class="ov-body">
      <div class="mp-art">${planPuzzle(p)}</div>
      <div class="mp-wallet">
        <span class="w-ico">${svgIcon('wallet')}</span>
        <div><small>Total Monthly Price</small>
        <p><b data-count="${p.total_monthly_price}"${ap?` data-from="${ap.from}"`:''}>${money(p.total_monthly_price)}</b><span>SAR / month</span></p></div>
      </div>
    </div>
  </section>
  <div class="mp-grid">
    <section class="panel">
      <h2 class="mp-h"><span class="h-ico base">${svgIcon('data')}</span>Base Package Details</h2>
      <div class="bp-head"><span class="bp-ico">${svgIcon('data')}</span><b>${esc(b.package_name)}</b><span class="bp-tag">Base package</span></div>
      <div class="stats">${stat('data','Data',qty(b.data_gb,'GB'))}${stat('calls','Calls',isUnl(b.voice_minutes)?'Unlimited':money(b.voice_minutes)+' min')}${stat('sms','SMS',qty(b.sms))}</div>
    </section>
    <section class="panel">
      <h2 class="mp-h"><span class="h-ico add">${svgIcon('puzzle')}</span>Active Add-ons</h2>
      ${adds.length?`<ul class="addons">${adds.map((a,i)=>{const st=addonStyle(a.category);return `<li style="--i:${i+1};--l:${st.col.l};--d:${st.col.d}"><span class="a-ico">${svgIcon(st.icon)}</span><span class="a-t"><b>${esc(a.addon_name)}</b><span class="a-p">${money(a.monthly_price_sar)} SAR / month</span><small>${esc(addonSummary(a))}</small></span></li>`;}).join('')}</ul>`
        :`<div class="add-empty"><b>No active add-ons</b><span>Add-on pieces join your plan when you apply a TELORA solution.</span></div>`}
    </section>
  </div>
  <button type="button" class="mp-banner" data-nav="design">
    <span class="bn-ico">${svgIcon('puzzle')}</span>
    <span class="bn-t">Needs changed?<b>Turn your needs into a new solution</b></span>
    <span class="bn-go">${svgIcon('right')}</span>
    <span class="deco d2">${decoPiece('#8FD3BD',130,110,[1,1,0,0])}</span>
    <span class="deco d3">${decoPiece('#F4B942',140,120,[1,0,0,-1])}</span>
  </button>`;
}

/* ---------- Requests ---------- */
const RQ_COL={processing:'#4BB0EE',awaiting_selection:'#8E7CE0',applying:'#4BB0EE',completed:'#3CC69C',cancelled:'#A09A8F',needs_adjustment:'#F4B942',failed:'#F27A6E'};
function requestLine(r){
  if(r.status==='processing')return `<p class="req-c muted">TELORA is building solutions</p>`;
  if(r.status==='applying')return `<p class="req-c muted">Applying your selected plan</p>`;
  if(r.status==='awaiting_selection')return `<p class="req-c">${nSols(r.solutions_count)} ready</p>
    <div class="req-actions"><button type="button" class="btn primary sm" data-view-request="${esc(r.request_id)}">View solutions ${svgIcon('right')}</button><button type="button" class="btn sm" data-cancel-req="${esc(r.request_id)}">Cancel request</button></div>`;
  if(r.status==='cancelled')return `<p class="req-c muted">${r.solutions_count?`${nSols(r.solutions_count)} generated · `:''}No change applied</p>`;
  if(r.status==='needs_adjustment')return `<p class="req-c muted">No fitting solution. Adjust your needs or budget.</p>`;
  if(r.status==='completed'&&r.new_plan){const c=r.previous_plan?priceChange(r.previous_plan.total_price,r.new_plan.total_price):null;
    return `<p class="req-c"><span>${esc(r.previous_plan?r.previous_plan.name:'—')}</span><span class="arr" aria-label="changed to">${svgIcon('right')}</span><b>${esc(r.new_plan.name)}</b>${c?`<span class="rq-diff ${c.tone}">${c.tone==='same'?'Same price':`${c.tone==='down'?'−':'+'}${money(c.abs)} SAR / month`}</span>`:''}</p>`;}
  if(r.status==='failed')return `<p class="req-c muted">Solution generation failed</p>`;
  return `<p class="req-c muted">${r.solutions_count?`${r.solutions_count} solution${r.solutions_count>1?'s':''} generated · `:''}No solution applied</p>`;
}
function requests(){
  const rs=[...V().requests].sort((a,b)=>(A.sort==='old'?1:-1)*(new Date(a.created_at)-new Date(b.created_at)));
  return `<header class="rq-head"><div><h1>Requests</h1><p>Every solution TELORA built from your needs and pieces.</p></div>
    ${rs.length>1?`<label class="rq-sort"><span class="sr">Sort requests</span><select id="rq-sort"><option value="new"${A.sort!=='old'?' selected':''}>Newest first</option><option value="old"${A.sort==='old'?' selected':''}>Oldest first</option></select>${svgIcon('chev')}</label>`:''}</header>
  ${rs.length?`<ol class="timeline">${rs.map(r=>`<li class="tl tl-${r.status}" style="--c:${RQ_COL[r.status]||'#A09A8F'}">
      <span class="tl-mark" aria-hidden="true"><svg viewBox="-3 -3 62 72"><path d="${piecePath(0,0,56,48,[0,0,1,0],13,18)}" fill="var(--c)"/><path d="${piecePath(0,0,56,48,[0,0,1,0],13,18)}" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="2"/></svg></span>
      <div class="tl-body">
        <div class="req-h"><b class="rq-id">#${esc(r.request_id)}</b><span class="muted">${dateTime(r.created_at)}</span>${statusPill(r.status)}</div>
        ${requestLine(r)}
      </div></li>`).join('')}</ol>`
    :`<section class="panel empty-state">${svgIcon('requests')}<h2>No requests yet</h2><p class="muted">Solutions you build with TELORA are listed here.</p></section>`}`;
}

/* ---------- customer switcher ---------- */
function openPop(){const p=$('#who-pop');if(!p)return;p.hidden=false;$('#who-btn').setAttribute('aria-expanded','true');const s=$('#w-search');s.value='';$('#wlist').innerHTML=customerRows('',A.me.customer_id);s.focus();}
function closePop(){const p=$('#who-pop');if(!p||p.hidden)return;p.hidden=true;$('#who-btn').setAttribute('aria-expanded','false');}

/* ---------- modal ---------- */
function modal(html,after){
  closeModal();const m=document.createElement('div');m.className='modal-back';m.id='modal';
  m.innerHTML=`<div class="modal" role="dialog" aria-modal="true" aria-labelledby="m-title">${html}</div>`;document.body.appendChild(m);
  m.addEventListener('click',e=>{if(e.target===m||e.target.closest('[data-close]'))closeModal();});
  after&&after();(m.querySelector('.btn.primary')||m.querySelector('button')).focus();
}
function closeModal(){const m=document.getElementById('modal');if(m)m.remove();}
/* Post-generation Cancel: explicit and confirmed only; leaving a page never cancels. */
function confirmCancel(id){
  if(!id)return;
  modal(`<h2 id="m-title">Cancel this request?</h2>
    <p class="m-lede">Your current plan will stay as it is.</p>
    <div class="m-err" id="m-err" role="alert" hidden></div>
    <div class="m-foot"><button class="btn" id="m-cancel">Cancel request</button><button class="btn primary" data-close>Keep request</button></div>`,
  ()=>{document.getElementById('m-cancel').addEventListener('click',async e=>{
    const b=e.currentTarget,er=document.getElementById('m-err');b.disabled=true;b.textContent='Cancelling…';er.hidden=true;
    try{await api.cancelRequest(id);closeModal();Design.closeRequest(id);A.requests=await api.myRequests();go('requests');}
    catch(err){er.innerHTML=`${svgIcon('alert')}<span>${esc(err.message)}</span>`;er.hidden=false;b.disabled=false;b.textContent='Try again';}
  });});
}

/* ---------- data loading ---------- */
async function openCustomer(id){
  await api.startDemoSession(id);
  const [me,plan,requests]=await Promise.all([api.me(),api.myPlan(),api.myRequests()]);
  Object.assign(A,{me,plan,requests});
  Design.reset();
}
async function refreshRequests(){try{A.requests=await api.myRequests();if(A.page==='requests')renderPage(false);}catch(e){}}

/* ---------- events ---------- */
document.addEventListener('click',async e=>{
  const t=e.target.closest('[data-role],[data-entry],[data-cust],[data-nav],[data-exit],#who-btn,[data-view-request],[data-cancel-req]');
  if(!t){if(!e.target.closest('#who-pop'))closePop();return;}
  if(t.dataset.viewRequest){const r=A.requests.find(x=>x.request_id===t.dataset.viewRequest);if(r){Design.open(r);go('design');}}
  else if(t.dataset.cancelReq)confirmCancel(t.dataset.cancelReq);
  else if(t.dataset.role==='customer'){A.entry='customers';A.q='';renderEntry();}
  else if(t.dataset.role==='admin'){A.role='admin';Admin.open();}
  else if(t.dataset.entry){A.entry=t.dataset.entry;renderEntry();}
  else if(t.dataset.cust){const inPop=!!t.closest('#who-pop');closePop();
    if(A.me&&t.dataset.cust===A.me.customer_id&&A.role)return;
    await openCustomer(t.dataset.cust);A.role='customer';
    if(!inPop)A.page='plan';renderShell();window.scrollTo({top:0});}
  else if(t.dataset.nav)go(t.dataset.nav);
  else if('exit' in t.dataset){await api.endSession();Design.reset();Object.assign(A,{role:null,me:null,entry:'roles',q:'',page:'plan'});closeModal();renderEntry();}
  else if(t.id==='who-btn'){$('#who-pop').hidden?openPop():closePop();}
});
document.addEventListener('input',e=>{
  if(e.target.id==='c-search'){A.q=e.target.value;$('#clist').innerHTML=customerRows(A.q);}
  if(e.target.id==='w-search')$('#wlist').innerHTML=customerRows(e.target.value,A.me.customer_id);
});
document.addEventListener('change',e=>{if(e.target.id==='rq-sort'){A.sort=e.target.value;renderPage(false);}});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){if(document.getElementById('modal'))closeModal();else if($('#who-pop')&&!$('#who-pop').hidden){closePop();$('#who-btn').focus();}}
  if(e.key==='Enter'&&(e.target.id==='c-search'||e.target.id==='w-search')){const first=e.target.closest('.entry-card,.pop').querySelector('[data-cust]');if(first){e.preventDefault();first.click();}}
});

/* ---------- boot ---------- */
(async()=>{A.customers=await api.listDemoCustomers();renderEntry();})();

return {
  go, modal, closeModal, refreshRequests, renderPage, confirmCancel,
  get state(){return V();},
  get raw(){return A;},
  async afterApply(){A.justApplied={from:A.plan.total_monthly_price};A.plan=await api.myPlan();A.requests=await api.myRequests();go('plan');}
};
})();
