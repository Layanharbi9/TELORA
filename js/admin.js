/* ============================================================
   TELORA admin console (Demo Mode, read-only).
   Five calm views, each answering one question in plain words:
     Overview   Is everything OK?
     Requests   What happened to each request?
     Customers  Who uses TELORA and what changed for them?
     Agents     Is each agent doing its job?
     Catalog    Which products do the agents use?
   Rules for this file: colour only for status, one sentence before any
   number, details stay folded until asked for. All data comes from
   /api/admin/* (SQLite); only the Runtime section reads LangSmith.
   ============================================================ */

const Admin = (() => {

/* ---------- vocabulary ---------- */
const OUT={applied:'Plan changed',awaiting:'Waiting for customer',applying:'Applying',in_progress:'In progress',
  no_fit:'No fitting solution',declined:'Declined',apply_failed:'Apply failed',failed:'Failed',stalled:'Stalled'};
const TONE={applied:'good',awaiting:'info',applying:'info',in_progress:'info',no_fit:'warn',declined:'mute',apply_failed:'bad',failed:'bad',stalled:'warn'};
/* the request filters the admin actually uses; rare outcomes fold into the closest one */
const GROUPS=[['all','All',null],['applied','Plan changed',['applied']],['awaiting','Waiting',['awaiting','applying','in_progress']],
  ['no_fit','No fit',['no_fit']],['declined','Declined',['declined']],['failed','Failed',['failed','stalled','apply_failed']]];
const TABS=[['overview','Overview'],['requests','Requests'],['customers','Customers'],['agents','Agents'],['catalog','Catalog']];
const HEAD={overview:['Overview','Is everything working?'],requests:['Requests','What happened to each request.'],
  customers:['Customers','Who uses TELORA and what changed for them.'],agents:['Agents','Is each agent doing its job?'],
  catalog:['Catalog','Which packages and add-ons the agents use.']};
const AGENT={search:['Search Agent','search'],design:['Solution Design Agent','puzzle'],guard:['Safety checks','shield'],customer:['Customer','user'],apply:['Apply Tool','check']};

const S={tab:'overview',data:{},err:{},at:{},drawer:null,
  req:{group:'all',q:''},cust:{filter:'all',q:''},cat:{kind:'packages',q:'',sort:'shortlisted',unused:false},
  sys:{data:null,error:null,days:7,loading:false,open:false}};
const root=()=>document.getElementById('root');
const pct=(n,d)=>d?Math.round(n/d*100):0;
const plural=(n,w,p)=>`${n} ${n===1?w:(p||w+'s')}`;
function ago(iso){const m=(Date.now()-new Date(iso))/6e4;if(isNaN(m))return '';if(m<1)return 'just now';if(m<60)return Math.round(m)+' min ago';
  const h=m/60;if(h<24)return Math.round(h)+' h ago';const d=Math.round(h/24);return d<60?(d===1?'1 day ago':d+' days ago'):dateTime(iso);}
const day=iso=>{const d=new Date(iso);return isNaN(d)?'':d.toLocaleDateString('en-GB',{day:'numeric',month:'short'});};
const sar=v=>v==null?'—':`${money(v)} SAR`;
const signed=v=>v==null?'—':`${v>0?'+':v<0?'−':''}${money(Math.abs(v))} SAR`;
const pill=o=>`<span class="st ${TONE[o]||'mute'}"><i></i>${OUT[o]||esc(o)}</span>`;
const needsText=ns=>(ns||[]).map(n=>`<span class="nd">${esc(n.need)} <span class="nlv">${esc(n.level)}</span></span>`).join('<span class="sep">·</span>');
const planText=p=>p&&p.name?`${esc(p.name)}${p.addons&&p.addons.length?` + ${p.addons.map(esc).join(', ')}`:''}`:'—';
const ai=`<span class="by-ai" title="Written by an AI agent. Read it as an opinion, not as data.">Written by AI</span>`;
const verified=t=>`<span class="ok-t">${svgIcon('check')}${t}</span>`;
const more=(label,html,open=false)=>`<details class="more"${open?' open':''}><summary>${label}</summary><div class="more-b">${html}</div></details>`;
const skel=()=>`<div class="skel" aria-busy="true"><span></span><span></span><span></span></div>`;
const errBox=(m,retry)=>`<div class="acard empty">${svgIcon('alert')}<h2>Couldn’t load this page</h2><p>${esc(m)}</p>${retry?`<button type="button" class="btn sm" data-ad-refresh>${svgIcon('reset')}Try again</button>`:''}</div>`;
const tile=(label,value,sub,tone='',go)=>`<${go?`button type="button" data-go-requests="${go}"`:'div'} class="tile ${tone}"><span class="t-l">${label}</span><b>${value}</b>${sub?`<span class="t-s">${sub}</span>`:''}</${go?'button':'div'}>`;
const health=(ok,text)=>`<p class="health ${ok===true?'good':ok===false?'bad':'warn'}"><i></i>${text}</p>`;

/* ---------- data ---------- */
const LOADERS={overview:()=>api.adminOverview(),requests:()=>api.adminRequests(),customers:()=>api.adminCustomers(),
  agents:()=>api.adminAgents(),catalog:()=>api.adminCatalog()};
async function load(tab=S.tab,force=false){
  if(S.data[tab]&&!force){renderBody();return;}
  S.err[tab]=null;if(force)S.data[tab]=null;renderBody();
  try{S.data[tab]=await LOADERS[tab]();S.at[tab]=new Date();}catch(e){S.err[tab]=e.message;}
  if(tab===S.tab)renderBody();
}

/* ============================================================ Overview */
function overview(d){
  const c=d.counts,rv=d.revenue,rec=d.recommendation,g=d.grounding;
  const failed=c.failed+c.stalled+c.apply_failed,waiting=c.awaiting+c.applying+c.in_progress;
  const verdict=failed?`${plural(failed,'request')} failed. Most are technical problems, not wrong answers from the agents.`
    :'No request failed. Everything is working.';
  return `
  <p class="lead">${esc(verdict)}</p>
  <div class="tiles four">
    ${tile('Requests',d.total,`from ${plural(d.customers,'customer')}${d.other_ids?` and ${d.other_ids} test IDs`:''}`,'','all')}
    ${tile('Plan changed',c.applied,`${pct(c.applied,d.total)}% of requests`,'good','applied')}
    ${tile('Waiting for customer',waiting,'solutions shown, no decision yet','info','awaiting')}
    ${tile('Failed',failed,'technical problems','bad','failed')}
  </div>
  <section class="acard"><h2>Where requests stop</h2><p class="hint">Each step shows how many requests got there, and why the rest did not.</p>${funnel(d.funnel)}</section>
  <div class="two">
    <section class="acard"><h2>Business</h2>
      <dl class="facts-list">
        <div><dt>Monthly revenue change</dt><dd class="${rv.monthly_change<0?'t-warn':rv.monthly_change>0?'t-good':''}">${signed(rv.monthly_change)}</dd>
          <p>${rv.applied?`From ${plural(rv.applied,'plan change')}: ${rv.upgrades} to a more expensive plan, ${rv.downgrades} to a cheaper one, ${rv.same} same price.`:'No plan changes yet.'}</p></div>
        <div><dt>Recommendation accepted</dt><dd>${rec.choices?pct(rec.accepted,rec.choices)+'%':'—'}</dd>
          <p>${rec.choices?`${rec.accepted} of the ${rec.choices} customers who chose a plan took the one TELORA recommended.`:'No customer has chosen yet.'}</p></div>
      </dl></section>
    <section class="acard"><h2>Trust</h2>
      <dl class="facts-list">
        <div><dt>Answers stayed inside the catalog</dt><dd class="${g.requests_with_issues?'t-bad':'t-good'}">${g.requests_checked?pct(g.requests_checked-g.requests_with_issues,g.requests_checked)+'%':'—'}</dd>
          <p>${g.requests_with_issues?`${plural(g.requests_with_issues,'request')} used a product or price that is not in the catalog.`:`Every product and price the agents gave (${g.requests_checked} requests) was checked against the catalog and matches.`}</p></div>
      </dl></section>
  </div>
  <div class="two">
    <section class="acard"><h2>Problems</h2><p class="hint">Grouped by cause. Open one to see an example.</p>
      ${d.attention_groups.length?`<ul class="rows">${d.attention_groups.map(g=>`<li><button type="button" class="row" data-journey="${esc(g.latest_request_id)}">
        <span class="acount bad">${g.count}</span><span class="row-t"><b>${esc(g.headline)}</b><small>${esc(g.customers.slice(0,3).join(', '))}${g.customers.length>3?` +${g.customers.length-3}`:''} · last ${ago(g.latest_at)}</small></span>${svgIcon('right')}</button></li>`).join('')}</ul>`
        :`<p class="calm-ok">${svgIcon('check')}No problems.</p>`}</section>
    <section class="acard"><h2>Waiting for the customer</h2><p class="hint">Longest waiting first.</p>
      ${d.waiting.length?`<ul class="rows">${d.waiting.slice(0,6).map(r=>`<li><button type="button" class="row" data-journey="${esc(r.request_id)}">
        <span class="av">${esc(initials(r.customer_name||r.customer_id))}</span><span class="row-t"><b>${esc(r.customer_name||r.customer_id)}</b><small>${plural(r.shown,'solution')} shown · ${ago(r.updated_at)}</small></span>${svgIcon('right')}</button></li>`).join('')}</ul>`
        :`<p class="calm-ok">${svgIcon('check')}Nobody is waiting.</p>`}</section>
  </div>
  <section class="acard"><h2>What customers ask for</h2><p class="hint">How many requests included each need.</p>${needsChart(d.needs)}</section>`;
}
function funnel(f){
  const max=f[0].count||1;
  return `<ol class="funnel">${f.map((s,i)=>`<li>
      <div class="fn-top"><span>${esc(s.label)}</span><b>${s.count}</b></div>
      <div class="fn-bar"><span style="--w:${s.count/max*100}%"></span></div>
      ${s.lost.length?`<ul class="fn-lost">${s.lost.map(l=>`<li><button type="button" data-go-requests="${esc(groupOf(l.outcome))}">${l.count} ${esc(l.label)}</button></li>`).join('')}</ul>`:''}
    </li>`).join('')}</ol>`;
}
const groupOf=o=>(GROUPS.find(([,,m])=>m&&m.includes(o))||['all'])[0];
function needsChart(list){
  if(!list.length)return '<p class="hint">No requests yet.</p>';
  const max=list[0].requests||1;
  return `<ul class="bars">${list.map(e=>`<li><span>${esc(e.need)}</span><span class="bar"><span style="--w:${e.requests/max*100}%"></span></span><b>${e.requests}</b></li>`).join('')}</ul>`;
}

/* ============================================================ Requests */
function inGroup(r){const g=GROUPS.find(([id])=>id===S.req.group);return !g[2]||g[2].includes(r.outcome);}
function filtered(d){const q=S.req.q.trim().toLowerCase();
  return d.requests.filter(r=>inGroup(r)&&(!q||[r.customer_name,r.customer_id,r.request_id].some(v=>(v||'').toLowerCase().includes(q))));}
function requests(d){
  const n=id=>{const g=GROUPS.find(x=>x[0]===id);return g[2]?d.requests.filter(r=>g[2].includes(r.outcome)).length:d.total;};
  return `<div class="bar-tools">
    <div class="tabs-sm" role="group" aria-label="Filter requests">${GROUPS.map(([id,l])=>`<button type="button" data-req-group="${id}" class="${S.req.group===id?'on':''}" aria-pressed="${S.req.group===id}">${l} <span>${n(id)}</span></button>`).join('')}</div>
    <label class="search ad-search">${svgIcon('search')}<input id="req-q" type="search" placeholder="Search by name or ID" autocomplete="off" value="${esc(S.req.q)}" aria-label="Search requests"></label></div>
  <div id="req-list">${reqList(filtered(d))}</div>`;
}
function reqList(list){
  if(!list.length)return `<div class="acard empty">${svgIcon('search')}<h2>No requests here</h2><p>Try another filter.</p></div>`;
  return `<div class="acard list"><div class="list-h"><span>Customer</span><span>Asked for</span><span>Result</span><span>Date</span></div>
  <ul>${list.map(r=>`<li><button type="button" class="req" data-journey="${esc(r.request_id)}">
    <span class="rq-c"><b>${esc(r.customer_name||r.customer_id)}</b><small>${esc(r.customer_id)}</small></span>
    <span class="rq-n">${needsText(r.needs)}<small>Budget ${sar(r.budget)}</small></span>
    <span class="rq-r">${pill(r.outcome)}<small>${esc(r.headline)}</small></span>
    <span class="rq-d">${day(r.created_at)}</span></button></li>`).join('')}</ul></div>`;
}

/* ---------- request journey (drawer) ---------- */
function journey(j){
  const s=j.search_step,d=j.design_step,dc=j.decision_step,ap=j.apply_step;
  const failedAt=st=>j.failure_stage===st&&['failed','stalled','apply_failed'].includes(j.outcome);
  const step=(k,state,title,body='')=>`<li class="step ${state}"><span class="sdot">${svgIcon(state==='fail'?'x':AGENT[k][1])}</span>
    <div class="step-b"><small>${AGENT[k][0]}</small><h3>${title}</h3>${body}</div></li>`;
  const fail=st=>failedAt(st)?`<div class="fail-box"><b>What went wrong</b><p>${esc(j.failure_cause||j.headline)}</p>
    ${j.customer_message?`<p class="quiet">The customer saw: “${esc(j.customer_message)}”</p>`:''}</div>`:'';

  const asked=step('customer','done','Asked for a new plan',
    `<p>${needsText(j.needs)}</p><p class="quiet">Budget ${sar(j.budget)} a month · plan at the time: ${planText(j.previous_plan)}${j.previous_plan&&j.previous_plan.total_price!=null?` (${sar(j.previous_plan.total_price)})`:''}</p>`);

  let search;
  if(!s.ran)search=step('search',failedAt('search')?'fail':'todo','Did not finish',fail('search')||`<p class="quiet">${esc(s.note)}</p>`);
  else{
    const all=[...s.packages,...s.addons],bad=all.filter(p=>!p.matches),ok=s.status==='SUCCESS';
    const uncovered=s.coverage.filter(n=>!n.pieces.length);
    search=step('search',ok?'done':'warn',ok?`Found ${plural(s.packages.length,'package')} and ${plural(s.addons.length,'add-on')}`:'Found no usable package',
      `<p>${bad.length?`<span class="t-bad">${bad.length} of them do not match the catalog.</span>`:verified('All taken from the catalog')}
        ${uncovered.length?` <span class="t-warn">Nothing found for ${uncovered.map(n=>esc(n.need)).join(', ')}.</span>`:''}</p>
      ${all.length?more('Show what it found',`${pieceList(s)}${s.notes.length?`<div class="note-ai"><p class="quiet">Agent’s note ${ai}</p>${s.notes.map(n=>`<p>${esc(n)}</p>`).join('')}</div>`:''}`):''}`);
  }

  let design;
  if(!d.ran)design=step('design',failedAt('design')?'fail':'todo','No solutions',fail('design')||`<p class="quiet">${esc(d.note)}</p>`);
  else{
    const shown=d.solutions.filter(x=>x.shown).length,blocked=d.solutions.filter(x=>x.guardrails.overall==='FAIL').length;
    design=step('design',shown?'done':'warn',shown?`Built ${plural(d.solutions.length,'solution')}, showed ${shown} to the customer`:`Built ${plural(d.solutions.length,'solution')}, none could be shown`,
      `${blocked?`<p class="t-warn">${blocked} ${blocked===1?'was':'were'} stopped by the safety checks.</p>`:''}
       <ul class="asols">${d.solutions.map(x=>solRow(x,j)).join('')}</ul>`);
  }

  let decision;
  if(!dc.reached)decision=step('customer','todo','Nothing to choose from');
  else if(dc.state==='chosen')decision=step('customer','done',dc.chose_recommended?'Chose the recommended solution':'Chose a different solution than recommended');
  else if(dc.state==='declined')decision=step('customer','done','Declined all solutions','<p class="quiet">The plan stayed the same.</p>');
  else decision=step('customer','wait','Has not decided yet',`<p class="quiet">Solutions shown ${ago(j.updated_at)}.</p>`);

  let apply;
  if(!ap.reached)apply=step('apply','todo','Plan not changed');
  else{const ch=ap.price_change;
    apply=step('apply',ap.order_status==='ACTIVE'?'done':failedAt('apply')?'fail':'wait',ap.order_status==='ACTIVE'?'Changed the plan':'Plan change not finished',
      `${fail('apply')}<div class="change"><span><small>Before</small>${planText(ap.previous_plan)}<em>${sar(ap.previous_plan&&ap.previous_plan.total_price)}</em></span>${svgIcon('right')}
        <span><small>After</small>${planText(ap.new_plan)}<em>${sar(ap.new_plan&&ap.new_plan.total_price)}</em></span></div>
       ${ch!=null?`<p class="quiet">${ch===0?'Same monthly price.':`The customer now pays ${money(Math.abs(ch))} SAR ${ch<0?'less':'more'} a month.`}</p>`:''}`);}

  return `<header class="dr-h"><div><h2 id="dr-title">${esc(j.customer_name||j.customer_id)}</h2>
      <p>${esc(j.customer_id)}${j.customer&&j.customer.city?` · ${esc(j.customer.city)}`:''} · ${dateTime(j.created_at)} · <button type="button" class="link" data-customer="${esc(j.customer_id)}">Customer profile</button></p></div>
      <button type="button" class="dr-x" data-close-drawer aria-label="Close">${svgIcon('x')}</button></header>
    <div class="dr-sum ${TONE[j.outcome]}">${pill(j.outcome)}<p>${esc(j.headline)}</p></div>
    <ol class="steps">${asked}${search}${design}${decision}${apply}</ol>
    <p class="ids">Request ${esc(j.request_id)}${j.order_id?` · Order ${esc(j.order_id)}`:''}</p>`;
}
function pieceList(s){
  const row=(p,kind)=>`<li><span><b>${esc(p.name)}</b><small>${kind} · ${esc(p.id)} · for ${p.matched_needs.map(esc).join(', ')||'—'}</small></span><span>${sar(p.price)}</span>${p.matches?`<span class="t-good" title="Matches the catalog">${svgIcon('check')}</span>`:`<span class="t-bad" title="${esc(p.issue)}">${svgIcon('alert')}</span>`}</li>`;
  return `<ul class="pieces-l">${s.packages.map(p=>row(p,'Package')).join('')}${s.addons.map(p=>row(p,'Add-on')).join('')}</ul>`;
}
function solRow(x,j){
  const blocked=x.guardrails.overall==='FAIL',chosen=j.selected_solution_id===x.solution_id,over=x.budget_difference;
  const checkable=x.coverage.some(c=>c.evidence.some(f=>f.verified!=null));
  const tags=[x.recommended&&'<span class="tg trec">Recommended</span>',chosen&&'<span class="tg chosen">Chosen</span>',blocked&&'<span class="tg blocked">Stopped by safety checks</span>',!blocked&&!x.shown&&'<span class="tg">Not shown</span>'].filter(Boolean).join('');
  const details=`
    <h4>How it covers each need</h4>
    <ul class="cover">${x.coverage.map(c=>`<li><span class="${c.covered?'t-good':'t-bad'}">${svgIcon(c.covered?'check':'x')}</span><span><b>${esc(c.need)}</b> <span class="nlv">${esc(c.level)}</span>
      <small>${c.evidence.map(f=>f.verified==null?esc(f.text):`${esc(f.piece)}: ${esc(f.field.replace(/_/g,' '))} = ${esc(f.value)}${f.verified?'':` <span class="t-bad">(catalog says ${esc(f.catalog_value)})</span>`}`).join(' · ')}</small></span></li>`).join('')}</ul>
    <p class="quiet">${checkable?verified('Every value above matches the catalog'):'This older request stored the reasons as free text, so they cannot be checked automatically.'}</p>
    <h4>Safety checks</h4>
    <ul class="checks-l">${x.guardrails.checks.map(c=>`<li class="${c.status==='PASS'?'t-good':'t-bad'}">${svgIcon(c.status==='PASS'?'check':'x')}<span>${esc(c.label)}${c.status!=='PASS'&&c.reason?`<small>${esc(c.reason)}</small>`:''}</span></li>`).join('')}</ul>
    ${x.intent?`<h4>Agent’s plan ${ai}</h4><p class="quiet">${esc(x.intent)}</p>`:''}`;
  return `<li class="sol-r${blocked?' is-blocked':''}${x.recommended?' is-rec':''}">
    <div class="sol-h"><div><b>${esc([x.package.name||x.package.id,...x.addons.map(a=>a.name||a.id)].join(' + '))}</b>
      <small>${sar(x.total_price)} a month · ${over==null?'':over>0?`${money(over)} SAR over budget`:over<0?`${money(-over)} SAR under budget`:'on budget'}</small></div><div class="tgs">${tags}</div></div>
    ${x.told_customer?`<div class="told"><p class="quiet">What TELORA told the customer ${ai}</p><p>“${esc(x.told_customer)}”</p></div>`:''}
    ${more('Why this solution',details)}</li>`;
}

/* ============================================================ Customers */
const CFILTERS=[['all','All',()=>true],['changed','Plan changed',c=>c.applied>0],['open','Waiting',c=>c.open>0],['problems','Had failures',c=>c.problems>0],['never','Never used',c=>!c.requests]];
function customers(list){
  const used=list.filter(c=>c.requests).length,changed=list.filter(c=>c.applied).length;
  return `<div class="tiles three">${tile('Customers',list.length,'in the dataset')}${tile('Used TELORA',used,`${pct(used,list.length)}% of customers`)}${tile('Plan changed',changed,'by TELORA','good')}</div>
  <div class="bar-tools">
    <div class="tabs-sm" role="group" aria-label="Filter customers">${CFILTERS.map(([id,l,fn])=>`<button type="button" data-cust-filter="${id}" class="${S.cust.filter===id?'on':''}" aria-pressed="${S.cust.filter===id}">${l} <span>${list.filter(fn).length}</span></button>`).join('')}</div>
    <label class="search ad-search">${svgIcon('search')}<input id="cust-q" type="search" placeholder="Search by name, ID or city" autocomplete="off" value="${esc(S.cust.q)}" aria-label="Search customers"></label></div>
  <div id="cust-list">${custList(list)}</div>`;
}
function custList(list){
  const q=S.cust.q.trim().toLowerCase(),fn=CFILTERS.find(x=>x[0]===S.cust.filter)[2];
  const rows=list.filter(c=>fn(c)&&(!q||[c.name,c.customer_id,c.city,c.plan&&c.plan.name].some(v=>(v||'').toLowerCase().includes(q))));
  if(!rows.length)return `<div class="acard empty">${svgIcon('user')}<h2>No customers here</h2><p>Try another filter.</p></div>`;
  return `<div class="acard list"><div class="list-h cu"><span>Customer</span><span>Current plan</span><span>Data use</span><span>Last request</span></div>
  <ul>${rows.map(c=>{const p=c.plan,cap=p&&!isUnl(p.data_gb)?+p.data_gb:null,use=c.latest_usage_gb,over=cap&&use!=null&&use>cap;
    return `<li><button type="button" class="req cu" data-customer="${esc(c.customer_id)}">
      <span class="rq-c"><b>${esc(c.name)}</b><small>${esc(c.customer_id)} · ${esc(c.city||'')}</small></span>
      <span class="rq-n">${p?`${esc(p.name)}${p.addons.length?` + ${p.addons.map(esc).join(', ')}`:''}<small>${sar(p.total_price)} a month${p.changed_by_telora?' · changed by TELORA':''}</small>`:'—'}</span>
      <span class="rq-n">${use!=null?`${money(use)} GB${cap?`<small class="${over?'t-warn':''}">${over?`${money(use-cap)} GB more than the plan gives`:`of ${money(cap)} GB`}</small>`:p&&isUnl(p.data_gb)?'<small>unlimited plan</small>':''}`:'—'}</span>
      <span class="rq-r">${c.last_request?`${pill(c.last_request.outcome)}<small>${ago(c.last_request.created_at)} · ${plural(c.requests,'request')}</small>`:'<span class="quiet">Never used TELORA</span>'}</span></button></li>`;}).join('')}</ul></div>`;
}
function customerDrawer(c){
  const p=c.plan_detail,cap=c.plan&&!isUnl(c.plan.data_gb)?+c.plan.data_gb:null;
  const maxU=Math.max(1,cap||0,...c.usage.map(u=>u.gb||0)),latest=c.latest_usage_gb;
  return `<header class="dr-h"><div><h2 id="dr-title">${esc(c.name)}</h2><p>${esc(c.customer_id)} · ${esc(c.segment||'')} · ${esc(c.city||'')} · customer for ${plural(c.tenure_months||0,'month')}</p></div>
      <button type="button" class="dr-x" data-close-drawer aria-label="Close">${svgIcon('x')}</button></header>
    <section class="acard flat"><h3>Current plan</h3>
      ${p?`<p class="big">${esc(p.package.name)}${p.addons.length?` + ${p.addons.map(a=>esc(a.name)).join(', ')}`:''}</p><p>${sar(p.total_price)} a month</p>
        <p class="quiet">${esc(p.package.features.join(' · '))}</p>
        <p class="quiet">${c.plan&&c.plan.changed_by_telora?`Changed by TELORA on ${dateTime(p.updated_at)}.`:'Original plan, never changed by TELORA.'}</p>`:'<p class="quiet">No plan on record.</p>'}</section>
    <section class="acard flat"><h3>Data use per month</h3>
      ${cap&&latest!=null?`<p class="${latest>cap?'t-warn':'quiet'}">${latest>cap?`Uses ${money(latest)} GB but the plan gives ${money(cap)} GB.`:`Uses ${money(latest)} GB of ${money(cap)} GB.`}</p>`:''}
      <div class="spark" role="img" aria-label="Data use per month">${c.usage.map(u=>`<div class="sp" title="${esc(u.month)}: ${u.gb==null?'no data':money(u.gb)+' GB'}"><span style="--h:${(u.gb||0)/maxU*100}%"></span><small>${esc(new Date(u.month+'-01').toLocaleDateString('en-GB',{month:'short'}))}</small></div>`).join('')}
        ${cap?`<i class="cap" style="--y:${cap/maxU*100}%"></i>`:''}</div>
      ${cap?'<p class="quiet sm">Dashed line = what the plan includes.</p>':''}</section>
    <h3 class="dr-sec">Requests (${c.requests.length})</h3>
    ${c.requests.length?`<ul class="rows">${c.requests.map(r=>`<li><button type="button" class="row" data-journey="${esc(r.request_id)}"><span class="row-t"><b>${needsText(r.needs)}</b><small>${esc(r.headline)}</small></span><span class="row-s">${pill(r.outcome)}<small>${day(r.created_at)}</small></span>${svgIcon('right')}</button></li>`).join('')}</ul>`:'<p class="quiet">No requests yet.</p>'}
    ${c.orders.length?`<h3 class="dr-sec">Plan changes (${c.orders.length})</h3><ul class="rows">${c.orders.map(o=>{const ch=o.previous_total_price!=null?Math.round((o.total_price-o.previous_total_price)*100)/100:null;
      return `<li><div class="row static"><span class="row-t"><b>${esc((o.previous_package_names||[])[0]||'—')} → ${esc([...(o.package_names||[]),...(o.addon_names||[])].join(' + '))}</b><small>${dateTime(o.applied_at||o.created_at)}</small></span>${ch!=null?`<span class="quiet">${ch===0?'same price':signed(ch)+' a month'}</span>`:''}</div></li>`;}).join('')}</ul>`:''}`;
}

/* ============================================================ Agents */
/* For every step the admin watches the same three things: did it run without technical
   errors, did it stay inside the catalog, and did it do its job. Each row answers one. */
const WATCH=[['errors','Runs without errors?'],['catalog','Stays inside the catalog?'],['job','Does its job?']];
function agents(a){
  const s=a.search,d=a.design,g=a.guardrails,r=a.recommendation,ap=a.apply;
  const causes=list=>list.length?`<ul class="causes">${list.map(c=>`<li><b>${c.requests}</b><span>${esc(c.cause)}</span></li>`).join('')}</ul>`:'';
  const row=(q,tone,answer,note='')=>`<li class="${tone}"><i></i><span class="w-q">${q}</span><span class="w-a">${answer}${note?`<small>${note}</small>`:''}</span></li>`;
  const card=(k,title,does,rows,details)=>`<section class="acard agent"><header><span class="ag-i">${svgIcon(k==='rec'?'star':AGENT[k][1])}</span><div><h2>${title}</h2><p class="hint">${does}</p></div></header>
    <ul class="watch">${rows.join('')}</ul>${details?more('Details',details):''}</section>`;
  const guardWrong=g.checks.filter(c=>c.key!=='budget_hard_limit').reduce((t,c)=>t+c.fail,0);
  const guardBudget=(g.checks.find(c=>c.key==='budget_hard_limit')||{fail:0}).fail;
  const ranSearch=s.found+s.no_results;
  return `<div class="acard intro"><h2>What you monitor here</h2>
    <p class="hint">Each request passes through these steps in order. For every step you check three things:</p>
    <ol class="flow">${['search','design','guard','customer','apply'].map(k=>`<li>${svgIcon(AGENT[k][1])}${AGENT[k][0]}</li>`).join('')}</ol>
    <dl class="watch-key">
      <div><dt>Runs without errors?</dt><dd>Did the step finish, or did it stop because of a technical problem such as the connection to OpenAI.</dd></div>
      <div><dt>Stays inside the catalog?</dt><dd>Is every product, price and fact it used really in the catalog. This is how you know the AI did not make anything up.</dd></div>
      <div><dt>Does its job?</dt><dd>Did it produce a useful result for the customer.</dd></div>
    </dl></div>
  <div class="agents-g">
  ${card('search','Search Agent','Looks in the catalog for packages and add-ons that match the customer’s needs.',[
    row(WATCH[0][1],s.errors?'bad':'good',s.errors?`No: ${s.errors} of ${s.runs} requests stopped here`:'Yes',s.errors?'Technical problems, not wrong answers. See Details for the causes.':''),
    row(WATCH[1][1],s.grounded_pieces===s.pieces?'good':'bad',s.grounded_pieces===s.pieces?'Yes: every product it picked is in the catalog':`No: ${s.pieces-s.grounded_pieces} of its ${s.pieces} picks do not match the catalog`,
      `Across ${s.shortlists} requests it used ${s.distinct_packages+s.distinct_addons} different products (${s.distinct_packages} packages, ${s.distinct_addons} add-ons), picking them ${s.pieces} times in total.`),
    row(WATCH[2][1],s.no_results?'warn':'good',`Found usable products for ${s.found} of ${ranSearch} requests`,s.no_results?`${s.no_results} found nothing usable for the needs and budget.`:''),
  ],`${s.errors?`<h4>Why requests stopped here</h4>${causes(s.error_causes)}`:''}
     <p class="quiet">On average it picks ${s.avg_packages??'—'} packages and ${s.avg_addons??'—'} add-ons per request.</p>
     ${s.unmatched_needs.length?`<p class="quiet">Needs the catalog could not serve: ${s.unmatched_needs.map(n=>`${esc(n.need)} (${n.requests})`).join(', ')}.</p>`:''}`)}
  ${card('design','Solution Design Agent','Builds up to three solutions from what Search found and explains how each need is covered.',[
    row(WATCH[0][1],d.errors?'bad':'good',d.errors?`No: ${d.errors} of ${d.runs} requests failed here`:'Yes',d.errors?'See Details for the causes.':''),
    row(WATCH[1][1],d.evidence_verified===d.evidence_facts?'good':'bad',d.evidence_verified===d.evidence_facts?'Yes: every fact it quoted matches the catalog':`No: ${d.evidence_facts-d.evidence_verified} of ${d.evidence_facts} facts it quoted do not match`,
      `It explained how each need is covered with ${d.evidence_facts} catalog facts (for example “data_gb = 15”) across ${d.completed} requests. Each one was checked.`),
    row(WATCH[2][1],'good',`Built solutions for ${d.completed} requests, about ${d.avg_designed??'—'} each`,`Top need covered in ${pct((d.coverage_by_priority[0]||{}).covered,(d.coverage_by_priority[0]||{}).total)}% of solutions.`),
  ],`${d.errors?`<h4>Why requests failed here</h4>${causes(d.error_causes)}`:''}
     <h4>How often each need is covered</h4><ul class="bars">${d.coverage_by_priority.map(c=>`<li><span>${['','1st','2nd','3rd'][c.priority]} need</span><span class="bar"><span style="--w:${pct(c.covered,c.total)}%"></span></span><b>${pct(c.covered,c.total)}%</b></li>`).join('')}</ul>`)}
  ${card('guard','Safety checks','Checks every solution before the customer sees it.',[
    row('Checked everything?','good',`Yes: ${g.solutions} solutions checked`),
    row('Caught catalog mistakes?',guardWrong?'warn':'good',guardWrong?`Stopped ${guardWrong} with a wrong product or price`:'None to catch: every solution used real products and correct prices'),
    row('Stopped over-budget solutions?','info',guardBudget?`Stopped ${guardBudget} more than 15% over budget`:'None were over the limit','Stopping them is this step doing its job, not an error.'),
  ],`<h4>The four checks</h4>
     <ul class="checks-l">${g.checks.map(c=>`<li class="${c.fail?'t-warn':'t-good'}">${svgIcon(c.fail?'alert':'check')}<span>${esc(c.label)}: ${c.fail?`stopped ${c.fail}`:`all ${c.pass} passed`}</span></li>`).join('')}</ul>
     ${g.stopped.length?`<h4>Solutions it stopped</h4><p class="quiet">None of these were shown to the customer.</p>
     <ul class="rows">${g.stopped.map(x=>`<li><button type="button" class="row" data-journey="${esc(x.request_id)}"><span class="row-t">
       <b>${esc(x.customer_name)}: ${sar(x.total_price)} against a limit of ${sar(x.limit)}</b>
       <small>Budget ${sar(x.budget)} + 15%${x.failed.some(f=>!f.startsWith('Within budget'))?` · ${esc(x.failed.join(', '))}`:''}${x.all_blocked?' · every solution in this request was stopped, so the customer saw “No fitting solution”':''} · ${day(x.created_at)}</small></span>${svgIcon('right')}</button></li>`).join('')}</ul>`:''}`)}
  ${card('rec','Recommendation','Part of the Solution Design Agent. Marks one solution as recommended and writes the reason the customer reads.',[
    row('Always recommends one?',r.missing?'warn':'good',
      r.missing?`No: ${r.missing} of ${r.requests_shown} requests showed solutions without a recommended one`:`Yes: all ${r.requests_shown} requests that showed solutions had one marked “Recommended”`,
      r.missing?'The recommendation step failed, so the customer saw the solutions with none highlighted.':'If this step fails, the customer still sees the solutions, just with none highlighted.'),
    row('Do customers take it?','info',r.choices?`${pct(r.accepted,r.choices)}%: ${r.accepted} of ${r.choices} customers chose the recommended one`:'No customer has chosen yet','This is the customer’s decision, not an error either way.'),
  ])}
  ${card('apply','Apply Tool','Changes the customer’s plan to the one they chose and saves the order.',[
    row(WATCH[0][1],ap.failed?'bad':'good',ap.failed?`No: ${ap.failed} of ${ap.orders} plan changes failed`:`Yes: ${ap.active} of ${ap.orders} plan changes completed`,ap.failed?'The old plan was kept each time.':''),
    row(WATCH[1][1],ap.price_matches_catalog===ap.orders?'good':'bad',`${ap.price_matches_catalog===ap.orders?'Yes':'No'}: ${ap.price_matches_catalog} of ${ap.orders} orders are priced exactly from the catalog`),
  ])}
  </div>
  <section class="acard"><header class="rt-h"><div><h2>Speed and cost</h2><p class="hint">How long the agents take on a customer request and what it costs in OpenAI usage. Loaded from LangSmith when you ask.</p></div>
    ${S.sys.open?`<div class="tabs-sm">${[[1,'24h'],[7,'7 days'],[30,'30 days']].map(([v,l])=>`<button type="button" data-sys-days="${v}" class="${S.sys.days===v?'on':''}">${l}</button>`).join('')}</div>`
      :`<button type="button" class="btn sm" data-sys-open>Load</button>`}</header>
    <div id="sys-body">${S.sys.open?systemBody():''}</div></section>`;
}
const secs=v=>v==null?'—':v<1?Math.round(v*1000)+' ms':v<60?v.toFixed(1)+' s':Math.floor(v/60)+'m '+Math.round(v%60)+'s';
const usd=v=>v==null?'—':'$'+(v===0?'0':v<0.01?v.toFixed(4):v<1?v.toFixed(3):v.toFixed(2));
/* Speed and cost, from LangSmith: only runs inside real customer requests are counted. */
function systemBody(){
  const d=S.sys.data;
  if(S.sys.error)return `<p class="t-bad">${esc(S.sys.error)}</p>`;
  if(!d||S.sys.loading)return skel();
  if(!d.configured)return `<p class="quiet">${esc(d.reason)}</p>`;
  const k=d.kpis,period=S.sys.days===1?'the last 24 hours':`the last ${S.sys.days} days`;
  if(!k.runs)return `<p class="quiet">No customer requests ran through the agents in ${period}.</p>`;
  return `<p class="sys-lead">In ${period}, <b>${plural(k.runs,'customer request')}</b> ran through the agents.
      On average the agents worked <b>${secs(k.avg_latency_s)}</b> on each request and it cost <b>${usd(k.avg_cost)}</b> in OpenAI usage.</p>
    <div class="tiles four sys-tiles">
      ${tile('Customer requests',k.runs,k.errors?`<span class="t-bad">${k.errors} ended with a technical error</span>`:'none ended with an error')}
      ${tile('Time per request',secs(k.avg_latency_s),`agents’ working time; slowest 5%: ${secs(k.p95_latency_s)}`)}
      ${tile('Cost per request',usd(k.avg_cost),'OpenAI usage for one request')}
      ${tile('Total cost',usd(k.total_cost),`all ${k.runs} requests together`)}
    </div>
    <h3 class="sys-h">Per step</h3>
    <div class="sys-tbl"><div class="st-h"><span>Step</span><span>Ran</span><span>Failed</span><span>Avg time</span><span>Avg cost</span></div>
      ${d.agents.map(a=>`<div class="st-r"><span><b>${esc(a.label)}</b></span>
        <span>${a.runs}</span><span class="${a.errors?'t-bad':''}"${a.last_error?` title="Last error: ${esc(a.last_error)}"`:''}>${a.errors}</span>
        <span>${a.runs?secs(a.avg_latency_s):'—'}</span><span>${a.key==='apply_tool'?'<span class="quiet">no cost</span>':a.runs?usd(a.avg_cost):'—'}</span></div>`).join('')}
    </div>
    <p class="quiet sm">Solution Design runs only when Search found products, and the Apply Tool only when a customer chose a plan, so their counts are lower.
      Time counts only the agents’ own work, not the time the customer took to decide.${d.excluded_agent_runs?` ${d.excluded_agent_runs} runs from tests or scripts are not counted.`:''}</p>
    <p class="quiet sm"><a class="link" href="${esc(d.project_url)}" target="_blank" rel="noopener">Open LangSmith for every run in detail</a></p>`;
}
async function loadSystem(){
  S.sys.open=true;S.sys.loading=true;renderBody();
  try{S.sys.data=await api.adminSystem(S.sys.days,true);S.sys.error=null;}catch(e){S.sys.error=e.message;}
  S.sys.loading=false;if(S.tab==='agents')renderBody();
}

/* ============================================================ Catalog */
function catalog(c){
  const k=S.cat.kind,t=c.totals,all=k==='packages'?c.packages:c.addons,unused=all.filter(p=>!p.shortlisted).length;
  return `<div class="tiles three">${tile('Packages used',`${t.packages_used} <i>of ${t.packages}</i>`,'picked by Search at least once')}${tile('Add-ons used',`${t.addons_used} <i>of ${t.addons}</i>`,'picked by Search at least once')}${tile('Never used',c.packages.length-t.packages_used+c.addons.length-t.addons_used,'products Search never picked','warn')}</div>
  <div class="bar-tools">
    <div class="tabs-sm" role="group" aria-label="Type">${[['packages','Packages'],['addons','Add-ons']].map(([v,l])=>`<button type="button" data-cat-kind="${v}" class="${k===v?'on':''}">${l}</button>`).join('')}
      <button type="button" data-cat-unused class="${S.cat.unused?'on':''}" aria-pressed="${S.cat.unused}">Never used <span>${unused}</span></button></div>
    <label class="asel"><span class="sr">Sort</span><select id="cat-sort">${[['shortlisted','Most used'],['applied','Most applied'],['active','Most customers'],['price','Cheapest first'],['name','A to Z']].map(([v,l])=>`<option value="${v}"${S.cat.sort===v?' selected':''}>${l}</option>`).join('')}</select>${svgIcon('chev')}</label>
    <label class="search ad-search">${svgIcon('search')}<input id="cat-q" type="search" placeholder="Search products" autocomplete="off" value="${esc(S.cat.q)}" aria-label="Search the catalog"></label></div>
  <div id="cat-list">${catList(c)}</div>`;
}
function catList(c){
  const k=S.cat.kind,q=S.cat.q.trim().toLowerCase(),key=S.cat.sort,all=k==='packages'?c.packages:c.addons,max=Math.max(1,...all.map(p=>p.shortlisted));
  const rows=all.filter(p=>(!S.cat.unused||!p.shortlisted)&&(!q||[p.name,p.id,p.type,p.segment,p.category].some(v=>(v||'').toLowerCase().includes(q))))
    .sort((a,b)=>key==='price'?a.price-b.price:key==='name'?a.name.localeCompare(b.name):(b[key]-a[key])||(b.shortlisted-a.shortlisted));
  if(!rows.length)return `<div class="acard empty">${svgIcon('search')}<h2>Nothing here</h2><p>Try another search.</p></div>`;
  return `<div class="acard list"><div class="list-h cat"><span>${k==='packages'?'Package':'Add-on'}</span><span>Price</span><span title="How many requests Search picked it for">Picked by Search</span><span class="n">Recommended</span><span class="n">Applied</span><span class="n">Customers now</span></div>
  <ul>${rows.map(p=>`<li><div class="req cat${p.shortlisted?'':' unused'}">
    <span class="rq-c"><b>${esc(p.name)}</b><small>${esc(p.features.slice(0,4).join(' · ')||p.category||'')}</small></span>
    <span>${sar(p.price)}</span>
    <span class="use"><span class="bar"><span style="--w:${p.shortlisted/max*100}%"></span></span><b>${p.shortlisted}</b></span>
    <span class="n">${p.recommended||'<span class="quiet">0</span>'}</span><span class="n">${p.applied||'<span class="quiet">0</span>'}</span><span class="n">${p.active||'<span class="quiet">0</span>'}</span></div></li>`).join('')}</ul></div>`;
}

/* ============================================================ shell */
function body(){
  if(S.err[S.tab])return errBox(S.err[S.tab],true);
  const d=S.data[S.tab];if(!d)return skel();
  return ({overview,requests,customers,agents,catalog})[S.tab](d);
}
function renderBody(){const el=document.getElementById('ad-body');if(el)el.innerHTML=body();sync();}
function sync(){const el=document.getElementById('ad-sync'),at=S.at[S.tab];if(el)el.textContent=at?`Updated ${at.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'})}`:'';}
const TAB_ICON={overview:'home',requests:'requests',customers:'user',agents:'puzzle',catalog:'plan'};
/* same sidebar as the customer view: logo, pages, then status and sign-out at the bottom */
function render(){
  const [h,p]=HEAD[S.tab];
  root().innerHTML=`<div class="adm-root"><div class="shell adm fade">
    <aside class="side">
      <div class="side-top">${logoHTML(true)}</div>
      <nav class="nav" aria-label="Admin pages"><span class="nav-ind" aria-hidden="true"></span>${TABS.map(([id,l])=>`<button type="button" data-ad-tab="${id}" class="${S.tab===id?'on':''}"${S.tab===id?' aria-current="page"':''}>${svgIcon(TAB_ICON[id])}<span>${l}</span></button>`).join('')}</nav>
      <div class="side-foot">
        <span class="adm-role">${svgIcon('shield')}Admin console</span>
        <span class="demo-badge" title="Demo Mode: the admin console has no sign-in. Production needs real authentication.">Demo Mode</span>
        <div class="side-act"><span class="quiet sm" id="ad-sync" aria-live="polite"></span><button type="button" class="icon-btn" data-ad-refresh title="Refresh" aria-label="Refresh">${svgIcon('reset')}</button></div>
        <button type="button" class="exit" data-exit>${svgIcon('logout')}<span>Sign out</span></button>
      </div>
    </aside>
    <main class="main"><div class="adm-main"><div class="adm-head" id="ad-head"><h1>${h}</h1><p>${p}</p></div><div id="ad-body">${body()}</div></div></main></div>
    <div class="drawer-back" id="drawer" hidden><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="dr-title"><div id="dr-body"></div></aside></div></div>`;
  sync();navInd(true);
}
/* the highlight slides to the active page, like the customer sidebar */
function navInd(instant){
  const ind=document.querySelector('.adm .nav-ind'),on=document.querySelector('.adm .nav button.on');if(!ind||!on)return;
  if(instant||RM)ind.classList.add('still');
  Object.assign(ind.style,{width:on.offsetWidth+'px',height:on.offsetHeight+'px',transform:`translate(${on.offsetLeft}px,${on.offsetTop}px)`});
  if(instant||RM)requestAnimationFrame(()=>requestAnimationFrame(()=>ind.classList.remove('still')));
}
window.addEventListener('resize',()=>navInd(true));
function go(tab){
  if(S.tab===tab)return;S.tab=tab;
  document.querySelectorAll('.adm .nav [data-ad-tab]').forEach(b=>{const on=b.dataset.adTab===tab;b.classList.toggle('on',on);on?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current');});
  const [h,p]=HEAD[tab];document.getElementById('ad-head').innerHTML=`<h1>${h}</h1><p>${p}</p>`;
  navInd();window.scrollTo({top:0});load(tab);
}

/* ---------- drawer ---------- */
let lastFocus=null;
async function openDrawer(kind,id){
  const back=document.getElementById('drawer'),el=document.getElementById('dr-body');if(!back)return;
  if(back.hidden)lastFocus=document.activeElement;
  S.drawer={kind,id};back.hidden=false;document.body.classList.add('no-scroll');
  const x=`<button type="button" class="dr-x" data-close-drawer aria-label="Close">${svgIcon('x')}</button>`;
  el.innerHTML=`<header class="dr-h"><div></div>${x}</header>${skel()}`;
  back.querySelector('.drawer').scrollTop=0;
  try{const data=kind==='journey'?await api.adminRequest(id):await api.adminCustomer(id);
    if(!S.drawer||S.drawer.id!==id)return;
    el.innerHTML=kind==='journey'?journey(data):customerDrawer(data);
  }catch(e){el.innerHTML=`<header class="dr-h"><div></div>${x}</header>${errBox(e.message)}`;}
  const b=el.querySelector('.dr-x');if(b)b.focus();
}
function closeDrawer(){const back=document.getElementById('drawer');if(!back||back.hidden)return;back.hidden=true;S.drawer=null;document.body.classList.remove('no-scroll');if(lastFocus&&lastFocus.focus)lastFocus.focus();}

/* ---------- events (only while the admin console is on screen) ---------- */
const active=()=>!!document.querySelector('.adm');
document.addEventListener('click',async e=>{
  if(!active())return;
  if(e.target.id==='drawer'){closeDrawer();return;}
  const t=e.target.closest('[data-ad-tab],[data-ad-refresh],[data-journey],[data-customer],[data-close-drawer],[data-go-requests],[data-req-group],[data-cust-filter],[data-cat-kind],[data-cat-unused],[data-sys-open],[data-sys-days]');
  if(!t)return;
  const d=t.dataset;
  if(d.adTab)go(d.adTab);
  else if('adRefresh' in d){t.disabled=true;await load(S.tab,true);if(S.tab==='agents'&&S.sys.open)loadSystem();t.disabled=false;}
  else if(d.journey)openDrawer('journey',d.journey);
  else if(d.customer)openDrawer('customer',d.customer);
  else if('closeDrawer' in d)closeDrawer();
  else if(d.goRequests){closeDrawer();S.req.group=d.goRequests;S.req.q='';if(S.tab==='requests')renderBody();else go('requests');}
  else if(d.reqGroup){S.req.group=d.reqGroup;renderBody();}
  else if(d.custFilter){S.cust.filter=d.custFilter;renderBody();}
  else if(d.catKind){S.cat.kind=d.catKind;renderBody();}
  else if('catUnused' in d){S.cat.unused=!S.cat.unused;renderBody();}
  else if('sysOpen' in d)loadSystem();
  else if(d.sysDays){const v=+d.sysDays;if(S.sys.days!==v){S.sys.days=v;S.sys.data=null;loadSystem();}}
});
/* searching re-renders only the list, so the input keeps focus */
document.addEventListener('input',e=>{
  if(!active())return;const id=e.target.id;
  if(id==='req-q'){S.req.q=e.target.value;document.getElementById('req-list').innerHTML=reqList(filtered(S.data.requests));}
  if(id==='cust-q'){S.cust.q=e.target.value;document.getElementById('cust-list').innerHTML=custList(S.data.customers);}
  if(id==='cat-q'){S.cat.q=e.target.value;document.getElementById('cat-list').innerHTML=catList(S.data.catalog);}
});
document.addEventListener('change',e=>{if(active()&&e.target.id==='cat-sort'){S.cat.sort=e.target.value;document.getElementById('cat-list').innerHTML=catList(S.data.catalog);}});
document.addEventListener('keydown',e=>{if(active()&&e.key==='Escape'&&S.drawer){e.stopPropagation();closeDrawer();}},true);

return {
  open(){Object.assign(S,{tab:'overview',data:{},err:{},at:{},drawer:null});S.req={group:'all',q:''};S.cust={filter:'all',q:''};
    S.cat={kind:'packages',q:'',sort:'shortlisted',unused:false};S.sys={data:null,error:null,days:7,loading:false,open:false};
    render();window.scrollTo({top:0});load('overview');}
};
})();
