/* ============================================================
   Design My Solution: the existing TELORA form (5 steps: 4 to shape the puzzle, 1 for the solution), with
   - the side panel turned into a top stepper
   - live build stages (driven by api.build events)
   - results, selection, confirm and Apply
   Form logic and interactions are kept from telora.html.
   ============================================================ */

const Design = (() => {

const STAGES=[['understanding','Your needs','Understanding what matters most to you.'],['finding','Finding pieces','Finding the right pieces for your needs and preferences.'],['checking','Checking pieces','Making sure the pieces are right for your solutions.'],['building','Building solutions','Bringing the right pieces together into tailored solutions.'],['done','Solutions ready','Your personalized solutions are ready to explore.']];
const fresh=()=>({step:1,slots:[null,null,null],lv:{},budget:180,view:'review',stages:{},error:null,errorKind:null,results:null,selected:null,requestId:null,open:{},sample:false,handle:null,reopened:false});
let S=fresh();
const root=()=>document.getElementById('design');

/* ---------- top stepper ---------- */
const STEPS=[['Your needs','Pick your top 3'],['Set levels','Adjust your usage'],['Your budget','Choose what fits'],['Your plan','Check your pieces'],['Your solution','Build and compare']];
function chrome(){
  const el=document.getElementById('stepper');if(!el)return;
  const placed=S.slots.filter(Boolean).length,busy=S.view==='building';
  const doneSub=[`${placed} selected`,'Levels set',`${S.budget} SAR / month`,'Puzzle complete',''];
  el.innerHTML=STEPS.map((s,i)=>{const n=i+1,st=n<S.step?'done':n===S.step?'active':'todo';
    const sub=st==='done'?doneSub[i]:n===4&&st==='active'?'Ready to build':n===5&&(st==='active'||S.results)?solSub():s[1];
    const inner=`<span class="dot">${st==='done'?svgIcon('check'):n}</span><span class="st-t"><b>${s[0]}</b><small>${sub}</small></span>`;
    const canGo=!busy&&(st==='done'||(n===5&&st==='todo'&&S.results));
    return `<li class="${st}"${st==='active'?' aria-current="step"':''}>${canGo?`<button type="button" data-go="${n}" aria-label="${st==='done'?'Back to':'Go to'} ${s[0]}">${inner}</button>`:`<div>${inner}</div>`}</li>`;}).join('');
}
/* the line under "Your solution" follows the build live */
function solSub(){
  if(S.view==='building'){if(S.stages.done==='done')return 'Solutions ready';const a=STAGES.find(([id])=>S.stages[id]==='active')||STAGES.find(([id])=>!S.stages[id]);return a?a[1]+'…':'Building…';}
  if(S.view==='error')return 'Build stopped';
  if(S.results){const sel=S.results.solutions&&S.results.solutions.find(x=>x.solution_id===S.selected);return sel?'Solution selected':'Solutions ready';}
  return 'Building…';
}
const tip=t=>`<div class="tip">${svgIcon('bulb')}<div>${t}</div></div>`;
const head=(h,l,t)=>`<div class="head"><div><h1>${h}</h1><p class="lede">${l}</p></div>${t?tip(t):''}</div>`;

/* ---------- step 1 ---------- */
function availPiece(n){
  const W=140,H=128,k=19;return `<svg viewBox="-10 -12 ${W+20} ${H+26}" width="100%" style="max-width:132px;height:auto" role="img" aria-hidden="true">${piece({x:14,y:10,w:W-28,h:H-26,e:n.e,col:n,k,r:18})}${iconG(n.icon,W/2,H/2-16,38,'#fff')}${lines(W/2,H/2+(n.lines.length>1?18:26),n.lines,n.lines[0].length>8?17:20,n.t,800)}</svg>`;
}
function boardSVG(){
  const vert=document.getElementById('card').clientWidth<620;
  const cols=[{f:'var(--slot1)',s:'var(--slot1s)',n:'var(--n1)'},{f:'var(--slot2)',s:'var(--slot2s)',n:'var(--n2)'},{f:'var(--slot3)',s:'var(--slot3s)',n:'var(--n3)'}];
  const txt=['Place your most important need here','Place your next important need here','Place your third need here'];
  let W,H,rects,E;
  if(!vert){W=884;H=222;const ws=[392,282,206];let x=0;rects=ws.map(w=>{const r=[x,6,w,200];x+=w+2;return r});E=[[0,1,0,0],[0,1,0,-1],[0,0,0,-1]];}
  else{W=360;H=548;const hs=[220,176,140];let y=6;rects=hs.map(h=>{const r=[6,y,348,h];y+=h+2;return r});E=[[0,0,1,0],[-1,0,1,0],[-1,0,0,0]];}
  let out='',under='';
  rects.forEach((r,i)=>{const [x,y,w,h]=r,cx=x+w/2,cy=y+h/2,id=S.slots[i],k=vert?24:34,km=vert?[.82,.5,.18,.5]:undefined;
    if(!id){const p=piecePath(x,y,w,h,E[i],20,k,km),ins=E[i].map(v=>v<0?k*1.35+12:14),ip=piecePath(x+ins[3],y+ins[0],w-ins[1]-ins[3],h-ins[0]-ins[2],[0,0,0,0],14),icx=x+ins[3]+(w-ins[1]-ins[3])/2,icy=y+ins[0]+(h-ins[0]-ins[2])/2;
      out+=`<g><path d="${p}" style="fill:${cols[i].f};stroke:${cols[i].s}" stroke-width="2"/><path d="${ip}" fill="none" style="stroke:${cols[i].s}" stroke-width="1.6" stroke-dasharray="7 6"/>
      <circle cx="${icx}" cy="${icy-40}" r="20" style="fill:${cols[i].n}"/><text x="${icx}" y="${icy-33}" text-anchor="middle" font-size="19" font-weight="700" fill="#fff">${i+1}</text>
      <text x="${icx}" y="${icy+6}" text-anchor="middle" font-size="${i==2&&!vert?19:21}" font-weight="700" style="fill:var(--ink)">Priority ${i+1}</text>
      ${(()=>{const words=txt[i].split(' ');const mx=vert?40:(i==0?30:i==1?18:12);let ls=[''];words.forEach(wd=>{if((ls[ls.length-1]+' '+wd).trim().length>mx)ls.push(wd);else ls[ls.length-1]=(ls[ls.length-1]+' '+wd).trim()});return `<text x="${icx}" y="${icy+32}" text-anchor="middle" font-size="${i==2&&!vert?15:16}" style="fill:var(--ink-2)">${ls.map((l,j)=>`<tspan x="${icx}" dy="${j?21:0}">${l}</tspan>`).join('')}</text>`})()}</g>`;}
    else{const n=byId(id);const fs=i==0?24:i==1?21:18;
      out+=`<g class="filled" data-remove="${i}" tabindex="0" role="button" aria-label="Remove ${n.name} from priority ${i+1}">${(under+=`<g data-ext="${i}">${piece({x,y,w,h,e:E[i],col:n,k,r:20,depth:8,km,layer:'ext'})}</g>`,'')}${piece({x,y,w,h,e:E[i],col:n,k,r:20,depth:8,km,layer:'top'})}
      ${iconG(n.icon,cx,cy-(vert?30:34),i==0?50:i==1?44:38,'#fff')}${lines(cx,cy+(vert?22:20)-(full(n).length>1?8:0),full(n),full(n).length>1?fs-3:fs,n.t,800)}${pill(cx,y+h-(vert?30:28),'Priority '+(i+1),n.t,14)}</g>`;}
  });
  return `<svg viewBox="0 0 ${W} ${H+10}" role="group" aria-label="Priority slots">${under}${out}</svg>`;
}
function step1(){
  const placed=S.slots.filter(Boolean).length;
  return `${head('What matters to you?','Your needs become your pieces. Tap them to set your priorities.','Place the need that matters most in the largest space, then the next, and so on.')}
  <h2 class="sect">Available features</h2>
  <div class="pieces">${NEEDS.filter(n=>!S.slots.includes(n.id)).map(n=>`<button class="pc" data-pick="${n.id}" aria-label="Add ${n.name}" title="${n.name}: ${n.sub}">${availPiece(n)}</button>`).join('')}</div>
  <div class="board"><div class="board-h"><h2 class="sect">Your priority order</h2><span class="count">${placed} / 3 placed</span></div><div class="slots" id="slots">${boardSVG()}</div></div>
  <div class="foot"><button class="btn" data-reset>${svgIcon('reset')}Reset</button><button class="btn primary" data-next ${placed<3?'disabled':''}>Continue to levels ${svgIcon('right')}</button></div>`;
}
/* ---------- step 2 ---------- */
function lvArt(n,lv){return `<svg viewBox="0 0 220 190" width="220" height="190" aria-hidden="true">${piece({x:40,y:36,w:140,h:128,e:[1,1,0,0],col:n,lv:LVF[lv],k:22,r:20,depth:9})}${iconG(n.icon,110,92,46,lv===2?'#fff':n.d)}${pill(110,140,LV[lv],n.t,15)}</svg>`;}
function step2(){
  return `${head('How much do you need?','Fill each piece to match how much you use it.','Move the slider to fill each piece. The more you fill, the higher the level.')}
  <div class="levels">${S.slots.map((id,i)=>{const n=byId(id),lv=S.lv[id];
    return `<div class="lv" style="background:${n.l};color:${n.t}"><span class="prio" style="color:${n.t}">Priority ${i+1}</span>
      <div class="lv-h"><span class="lv-ico" style="background:${n.c};color:#fff">${svgIcon(n.icon)}</span><div><h3>${n.name}</h3><p>${n.desc}</p></div></div>
      <div class="lv-art" id="art-${id}">${lvArt(n,lv)}</div>
      <input type="range" id="lv-${id}" min="0" max="2" step="1" value="${lv}" data-lv="${id}" aria-label="${n.name} usage level" aria-valuetext="${LV[lv]}" style="--trk:${n.c}55;--acc:${n.c}">
      <div class="ticks" id="tk-${id}">${LV.map((l,j)=>`<span class="${j===lv?'on':''}">${l}</span>`).join('')}</div>
</div>`}).join('')}</div>
  <div class="foot"><button class="btn" data-back>${svgIcon('left')}Back</button><button class="btn primary" data-next>Continue to budget ${svgIcon('right')}</button></div>`;
}
/* ---------- step 3 ---------- */
/* budget range: 50 to 900 SAR */
const B_MIN=50,B_MAX=900;
const pct=v=>((v-B_MIN)/(B_MAX-B_MIN)*100).toFixed(1)+'%';
const mix=(a,b,t)=>'#'+[1,3,5].map(i=>Math.round(parseInt(a.substr(i,2),16)*(1-t)+parseInt(b.substr(i,2),16)*t).toString(16).padStart(2,'0')).join('');
function walletTone(v){const t=Math.max(0,Math.min(1,(v-B_MIN)/(B_MAX-B_MIN)));return `--wbg:${mix('#F6EAD6','#EACB8F',t)};--wsh:${mix('#D9BE8E','#BD9352',t)}`;}
let bShown=180,bRaf=0;
function countTo(v){const el=document.getElementById('bnum');if(!el)return;cancelAnimationFrame(bRaf);
  if(RM){bShown=v;el.textContent=v;return;}
  const from=bShown,t0=performance.now(),D=420;
  const f=now=>{const t=Math.min(1,(now-t0)/D),e=1-Math.pow(1-t,3);bShown=Math.round(from+(v-from)*e);el.textContent=bShown;if(t<1)bRaf=requestAnimationFrame(f);};
  bRaf=requestAnimationFrame(f);}
function step3(){
  return `${head('Set your budget','Your budget is the last piece. How much would you like to spend each month?','Pick a comfortable amount. TELORA builds your solution around it.')}
  <div class="remind"><b>Building for</b>${S.slots.map(id=>{const n=byId(id);return `<span class="chip" style="background:${n.l};color:${n.t}"><i style="background:${n.c}"></i>${n.name}<em>${LV[S.lv[id]]}</em></span>`}).join('')}<button class="edit" data-go="1">Edit</button></div>
  <div class="budget">
    <div class="wallet" id="wallet" style="${walletTone(S.budget)}">${svgIcon('wallet')}<div><div class="w-t">Your preferred budget</div><div class="w-n" id="bnum">${S.budget}</div><div class="w-u">SAR / month</div></div></div>
    <div class="bslider"><span>50</span><input type="range" id="brange" min="${B_MIN}" max="${B_MAX}" step="10" value="${S.budget}" style="--p:${pct(S.budget)}" aria-label="Preferred monthly budget in SAR"><span style="white-space:nowrap">${B_MAX} SAR</span></div>
    <div class="quick" id="quick"><span>Quick select</span>${[100,300,500,700,900].map(v=>`<button data-q="${v}" class="${S.budget==v?'on':''}">${v} SAR</button>`).join('')}</div>
  </div>
  <div class="foot"><button class="btn" data-back>${svgIcon('left')}Back</button><button class="btn primary" data-next>Complete your puzzle ${svgIcon('right')}</button></div>`;
}
/* ---------- step 4: review · step 5: building / error / results ---------- */
const ENT=['--d:0;--tx:-80px;--ty:-70px;--r:-8deg;--ex:-34px;--ey:-26px;--sp:-8deg','--d:1;--tx:80px;--ty:-70px;--r:8deg;--ex:34px;--ey:-26px;--sp:7deg','--d:2;--tx:-80px;--ty:70px;--r:6deg;--ex:-34px;--ey:26px;--sp:6deg','--d:3;--tx:90px;--ty:80px;--r:-7deg;--ex:34px;--ey:26px;--sp:-7deg'];
function finalSVG(){
  const w=250,h=224,g=2,x0=12,y0=26,k=30;
  const pos=[[x0,y0,[0,1,-1,0]],[x0+w+g,y0,[0,0,1,-1]],[x0,y0+h+g,[1,-1,0,0]],[x0+w+g,y0+h+g,[-1,0,0,1]]];
  let out='',under='';const sid='s'+(uid++);
  pos.forEach((p,i)=>{const [x,y,e]=p,cx=x+w/2,cy=y+h/2;
    if(i<3){const n=byId(S.slots[i]),lv=S.lv[n.id];
      under+=`<g class="fu" data-pi="${i}" style="${ENT[i]}">${piece({x,y,w,h,e,col:n,k,depth:9,r:22,layer:'ext'})}</g>`;out+=`<g class="fp" data-pi="${i}" data-need="${n.id}" style="${ENT[i]}">${piece({x,y,w,h,e,col:n,lv:LVF[lv],k,r:22,depth:9,layer:'top'})}${iconG(n.icon,cx,cy-46,40,lv===2?'#fff':n.d)}${lines(cx,cy+(full(n).length>1?-2:8),full(n),21,n.t,800)}${pill(cx,cy+(full(n).length>1?46:40),LV[lv],n.t,15)}<text x="${x+22}" y="${y+30}" font-size="13" font-weight="700" fill="${n.t}" fill-opacity=".8">Priority ${i+1}</text></g>`;}
    else{const lv=.2+.8*(S.budget-B_MIN)/(B_MAX-B_MIN);
      under+=`<g class="fu" data-pi="3" style="${ENT[i]}">${piece({x,y,w,h,e,col:BUDGET,k,depth:9,r:22,layer:'ext'})}</g>`;
      out+=`<g class="fp" data-pi="3" data-need="budget" style="${ENT[i]}">${piece({x,y,w,h,e,col:BUDGET,lv,k,r:22,depth:9,layer:'top'})}${iconG('wallet',cx,cy-40,38,BUDGET.t)}${lines(cx,cy+12,['Budget'],21,BUDGET.t,800)}${pill(cx,cy+46,'Around '+S.budget+' SAR',BUDGET.t,15)}
      <clipPath id="c${sid}"><path d="${piecePath(x,y,w,h,e,22,k)}"/></clipPath><g clip-path="url(#c${sid})"><polygon class="shine" points="${x-150},${y-40} ${x-70},${y-40} ${x-120},${y+h+40} ${x-200},${y+h+40}" fill="url(#g${sid})"/></g></g>`;}
  });
  return `<svg viewBox="0 0 ${x0*2+w*2+g} ${y0+h*2+g+40}" role="img" aria-label="Your completed puzzle"><defs><linearGradient id="g${sid}" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".8"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>${under}${out}</svg>`;
}
function stagesHTML(){
  return STAGES.map(([id,label,desc])=>{const st=S.stages[id]||'todo';
    const ico=st==='done'?svgIcon('check'):st==='failed'?svgIcon('alert'):'';
    return `<li class="stage ${st}"${st==='active'?' aria-current="step"':''}><span class="s-dot">${ico}</span><span class="s-t"><b>${label}</b><small>${desc}</small></span></li>`;}).join('');
}
/* each finished stage locks one piece of the puzzle into place; the last one sends the shine across */
let litBefore=new Set();
function litPieces(){
  const c=document.getElementById('card');if(!c||!c.classList.contains('building'))return;
  const now=new Set();
  STAGES.slice(0,4).forEach(([id],i)=>{const st=S.stages[id];
    c.querySelectorAll(`.final [data-pi="${i}"]`).forEach(g=>{g.classList.toggle('lit',st==='done');g.classList.toggle('now',st==='active');});
    if(st==='done')now.add(i);});
  litBefore=now;
  const ready=S.stages.done==='done';
  if(ready&&!c.classList.contains('ready'))assemble(c);else if(!ready)c.classList.remove('ready','shine-go');
}
/* the orbiting pieces glide home from wherever they are, click together, then the shine runs */
function assemble(c){
  const gs=[...c.querySelectorAll('.final .fp,.final .fu')],from=gs.map(g=>getComputedStyle(g).transform);
  c.classList.add('ready');
  if(RM||!gs.length){c.classList.add('shine-go');return;}
  gs.forEach((g,i)=>{if(from[i]&&from[i]!=='none')g.animate([{transform:from[i]},{transform:'none'}],{duration:1100,easing:'cubic-bezier(.3,1.3,.5,1)'});});
  setTimeout(()=>c.classList.add('shine-go'),1000);
}
function readyHTML(){return S.stages.done==='done'&&S.results?`<button class="btn primary view-sols" data-view-results>View my solutions ${svgIcon('right')}</button>`:'';}
function updateStages(){const el=document.getElementById('stages');if(el)el.innerHTML=stagesHTML();const r=document.getElementById('ready');if(r)r.innerHTML=readyHTML();litPieces();chrome();}
function step5(){
  if(S.view==='results')return resultsView();
  if(S.view==='error')return errorView();
  if(S.view==='building')return `${head('Building your solution…','TELORA is turning your pieces into your solution.','Follow each stage below. Your needs are saved while TELORA works.')}
    <div class="build-wrap"><div class="final">${finalSVG()}</div><div class="stage-col"><ol class="stages" id="stages" aria-label="Build progress">${stagesHTML()}</ol><div id="ready" class="ready-slot">${readyHTML()}</div></div></div>`;
  return S.results?resultsView():errorView();
}
function step4(){
  return `${head('Your puzzle is complete','Your needs, your pieces. Now TELORA builds your solution.','Check your pieces. Go back to change anything, or build your solution.')}
  <div class="final">${finalSVG()}</div>
  <div class="foot"><button class="btn" data-back>${svgIcon('left')}Back</button><button class="btn primary" data-build>Build my solution ${svgIcon('right')}</button></div>`;
}
function errorView(msg){
  const adjustBtn=primary=>`<button class="btn${primary?' primary':''}" data-adjust>${svgIcon('left')}Adjust my puzzle</button>`;
  const tryBtn=primary=>`<button class="btn${primary?' primary':''}" data-build>Try again ${svgIcon('reset')}</button>`;
  const requestsBtn=primary=>`<button class="btn${primary?' primary':''}" data-nav="requests">Check my requests ${svgIcon('right')}</button>`;
  // Technical/connection failures are never caused by the customer's needs,
  // so only "Try again" is offered there -- "Adjust my puzzle" stays only
  // for the business/no-fit case, where changing input is actually meaningful.
  const foot=S.errorKind==='business'?adjustBtn(true)+tryBtn(false)
    :S.errorKind==='connection'?requestsBtn(true)+tryBtn(false)
    :tryBtn(true);
  const tipText=S.errorKind==='business'?'Adjust your pieces, or try again.':'Try again in a moment.';
  return `${head('We couldn’t build your solution','Your needs and pieces are safe. Nothing was lost.',tipText)}
  <div class="err" role="alert">${svgIcon('alert')}<p>${esc(msg||S.error)}</p></div>
  ${Object.keys(S.stages).length?`<ol class="stages" aria-label="Where the build stopped">${stagesHTML()}</ol>`:''}
  <div class="foot">${foot}</div>`;
}

/* ---------- results ---------- */
function delta(price){const d=Math.round((price-S.budget)*100)/100;
  // Exact preferred-budget comparison, matching whyItFits() -- no banding, no tolerance read-back.
  if(d===0)return `<span class="delta at">Right at your budget</span>`;
  return d<0?`<span class="delta under">${money(-d)} SAR under your budget</span>`:`<span class="delta over">${money(d)} SAR over your preferred budget</span>`;}
/* how this solution compares with what the customer pays for their plan today */
function vsCurrent(price){
  const cur=App.state.plan,c=cur?priceChange(cur.total_monthly_price,price):null;if(!c)return '';
  const txt=c.tone==='same'?'Same as you pay today':`${money(c.abs)} SAR ${c.tone==='down'?'less':'more'} than you pay today`;
  return `<p class="vs-now ${c.tone}">${svgIcon(c.tone==='up'?'alert':'check')}<span>${txt} <small>(${money(cur.total_monthly_price)} SAR)</small></span></p>`;
}
function coverage(sol,withDetail){
  const cov=new Map((sol.needs_covered||[]).map(x=>[x.need,x])),part=new Map((sol.needs_not_fully_covered||[]).map(x=>[x.need,x]));
  const order=S.slots.filter(Boolean).map((id,i)=>({n:byId(id),p:i+1}));
  [...cov.keys(),...part.keys()].forEach(name=>{if(!order.some(o=>o.n.name===name))order.push({n:needByName(name),p:null});});
  return `<ul class="covers" aria-label="How your needs are covered">${order.map(({n,p})=>{const c=cov.get(n.name),pt=part.get(n.name),st=c?'full':pt?'part':'none',x=c||pt;
    const mark=st==='full'?svgIcon('check'):st==='part'?'<span class="half" aria-hidden="true"></span>':svgIcon('x');
    const label=st==='full'?'Covered':st==='part'?'Not fully covered':'Not covered';
    return `<li class="cov ${st}"${withDetail&&n.id?` data-need="${n.id}" tabindex="0"`:''} style="--c:${n.c};--l:${n.l};--t:${n.t}" title="${esc(n.name)}: ${label}${x&&x.detail?' · '+esc(x.detail):''}"><span class="mk">${mark}</span><b>${esc(n.name)}</b>${p?`<em>P${p}</em>`:''}${withDetail&&x&&x.detail?`<small>${esc(x.detail)}</small>`:''}<span class="sr">${label}</span></li>`;}).join('')}</ul>`;
}
/* which catalogue item (an add-on's own category, else the base package) actually covers a need */
function coveringName(sol,needName){
  const found=NEEDS.find(n=>n.name===needName),needId=found?found.id:null;
  const addon=needId&&(sol.addons||[]).find(a=>ADDON_NEED[a.category]===needId);
  return addon?addon.name:sol.base_package.name;
}
/* needs/priorities are already shown by the chips above; this is just the headline verdict */
function coversLine(sol){
  if(!(sol.needs_covered||[]).length)return '';
  const all=!(sol.needs_not_fully_covered&&sol.needs_not_fully_covered.length);
  return `<p class="covers-line">${svgIcon('check')}<span>${all?'Covers all your needs':'Covers your needs'}</span></p>`;
}
/* the customer's own highest-priority High-level need, if this solution actually covers it --
   drawn from the wizard's own state (S.slots/S.lv), which is the real source of need/level/
   priority for this exact request; never invented. */
function standoutNeed(sol){
  const covered=new Set((sol.needs_covered||[]).map(x=>x.need));
  for(const id of S.slots){
    if(!id)continue;
    const n=byId(id);
    if(LV[S.lv[id]]==='High'&&covered.has(n.name))return n.name;
  }
  return null;
}
/* one concise, personalized sentence: standout need (if any) + coverage quality + budget
   position -- no catalog/agent wording, no repeated need list, no cross-solution comparison. */
function whyItFits(sol){
  const someCovered=(sol.needs_covered||[]).length>0;
  const allCovered=someCovered&&!(sol.needs_not_fully_covered&&sol.needs_not_fully_covered.length);
  const diff=Math.round((sol.total_monthly_price-S.budget)*100)/100;
  // Exact preferred-budget comparison -- the +15% guardrail tolerance decides eligibility
  // elsewhere and must not be read back into this customer-facing wording.
  const budgetPart=diff===0?'right at your budget'
    :diff<0?`staying ${money(-diff)} SAR under your budget`:`landing ${money(diff)} SAR over your preferred budget`;
  const standout=standoutNeed(sol);
  let sentence;
  if(standout&&allCovered)sentence=`strongly supports your high ${standout} need while covering your other priorities and ${budgetPart}`;
  else if(standout)sentence=`strongly supports your high ${standout} need while ${budgetPart}`;
  else if(allCovered)sentence=`matches the level of service you need across your priorities while ${budgetPart}`;
  else if(someCovered)sentence=`covers your top priorities while ${budgetPart}`;
  else sentence=budgetPart;
  return sentence.charAt(0).toUpperCase()+sentence.slice(1)+'.';
}
/* a real, deterministic numeric edge of `a` over `b` -- currently just data allowance */
function moreDataThan(a,b){
  const av=(a.base_package.capabilities||{}).data_gb,bv=(b.base_package.capabilities||{}).data_gb;
  if(isUnl(av)&&!isUnl(bv))return true;
  if(isUnl(av)||isUnl(bv))return false;
  const an=+av,bn=+bv;
  return Number.isFinite(an)&&Number.isFinite(bn)&&an>bn;
}
/* "Why recommended over the other presented options" -- this is the
   Recommendation stage's own grounded rationale (recommendation_reason from
   the backend), never a frontend-invented comparison. The backend validates
   it server-side to never reference internal solution IDs, so it is safe to
   render directly. Empty when unavailable (no recommendation, or this isn't
   the recommended solution), so the caller hides the callout instead of
   inventing a reason. */
function recommendedBecause(sol){
  return (sol.recommendation_reason||'').trim();
}
/* Which solution `sol` should be compared against in Worth Considering. Alternatives always
   compare against the recommended solution. The recommended solution compares against the
   single alternative closest in price to it (the most comparable real trade-off a customer
   would actually weigh), ties broken by rank. Never a full pairwise report. */
function comparisonReference(sol,rec,sols){
  if(!rec)return null;
  if(sol.solution_id!==rec.solution_id)return rec;
  const alts=sols.filter(x=>x.solution_id!==rec.solution_id);
  if(!alts.length)return null;
  alts.sort((a,b)=>Math.abs(a.total_monthly_price-rec.total_monthly_price)-Math.abs(b.total_monthly_price-rec.total_monthly_price)||(a.rank??99)-(b.rank??99));
  return alts[0];
}
/* only ever real, grounded facts: this solution's own coverage gaps, plus (when there is a
   comparison reference and more than one solution) a genuine, symmetric price/data comparison
   against that reference -- shown from `sol`'s own perspective either way. Never invented,
   never a manufactured downside just to fill the section. */
function worthConsidering(sol,ref,total){
  const notes=(sol.needs_not_fully_covered||[]).map(x=>`${x.need} isn’t fully covered by this plan.`);
  if(total>1&&ref&&sol.solution_id!==ref.solution_id){
    const refLabel=sol.recommended?'the alternative':'the recommended option';
    const diff=Math.round((sol.total_monthly_price-ref.total_monthly_price)*100)/100;
    const solMoreData=moreDataThan(sol,ref),refMoreData=moreDataThan(ref,sol);
    const priceClause=Math.abs(diff)>=0.01
      ?(diff<0?{desirable:true,text:`costs ${money(-diff)} SAR less`}:{desirable:false,text:`costs ${money(diff)} SAR more`})
      :null;
    const dataClause=solMoreData?{desirable:true,text:'offers more data'}:refMoreData?{desirable:false,text:'offers less data'}:null;
    const clauses=[priceClause,dataClause].filter(Boolean).sort((a,b)=>(b.desirable-a.desirable));
    const cap=t=>t.charAt(0).toUpperCase()+t.slice(1);
    if(clauses.length===1){
      notes.push(`${cap(clauses[0].text)} than ${refLabel}.`);
    } else if(clauses.length===2){
      const joiner=clauses[0].desirable===clauses[1].desirable?'and':'but';
      notes.push(`${cap(clauses[0].text)}, ${joiner} ${clauses[1].text} than ${refLabel}.`);
    }
  }
  return notes;
}
/* [ PACKAGE ] + [ ADD-ON ] + [ ADD-ON ] -- compact components being assembled into the solution */
function piecesRow(sol){
  const items=[{label:'Package',name:sol.base_package.name,price:sol.base_package.price,addon:false},
    ...(sol.addons||[]).map(a=>({label:'Add-on',name:a.name,price:a.price,addon:true}))];
  return items.map((it,i)=>(i?`<span class="piece-join">+</span>`:'')+
    `<div class="piece-mini${it.addon?' addon':''}"><small>${it.label}</small><b>${esc(it.name)}</b><span>${money(it.price)} SAR</span></div>`).join('');
}
/* Need -> covering component, one scannable row per fully covered need. */
function needsRows(sol){
  return (sol.needs_covered||[]).map(x=>{
    const n=needByName(x.need);
    return `<div class="need-row"><span class="need-ico" style="background:${n.l};color:${n.t}">${svgIcon(n.icon)}</span><b>${esc(x.need)}</b><span class="need-by">Covered by ${esc(coveringName(sol,x.need))}</span><span class="need-ok">${svgIcon('check')}</span></div>`;
  }).join('');
}
function details(sol,total,rec,sols){
  const isRec=!!rec&&sol.solution_id===rec.solution_id;
  const recBecause=isRec?recommendedBecause(sol):'';
  const worth=worthConsidering(sol,comparisonReference(sol,rec,sols),total);
  return `<div class="details">
    <div class="fit-block"><span class="fit-ico">${svgIcon('bulb')}</span><p>${esc(whyItFits(sol))}</p></div>
    <div class="pieces-row">${piecesRow(sol)}</div>
    <div class="pieces-total"><span>Total</span><b>${money(sol.total_monthly_price)} SAR / month</b></div>
    <div class="needs-rows"><span class="eyebrow">Needs Covered</span>${needsRows(sol)}</div>
    ${worth.length?`<div class="worth-callout"><span class="worth-ico">${svgIcon('scale')}</span><div><b>Worth Considering</b>${worth.map(w=>`<p>${esc(w)}</p>`).join('')}</div></div>`:''}
    ${recBecause?`<div class="rec-block"><span class="rec-ico">${svgIcon('star')}</span><div><b>Recommended Because</b><p>${esc(recBecause)}</p></div></div>`:''}
  </div>`;
}
const selBtn=(sol,primary)=>S.selected===sol.solution_id
  ?`<span class="sel-note">${svgIcon('check')}Selected</span>`
  :`<button class="btn${primary?' primary':''}" data-select="${esc(sol.solution_id)}">Select</button>`;
/* Solution-card visual identity: fixed, deterministic colour families by presented
   position (#1/#2/#3), completely separate from BASE_COL/addonStyle (which stay
   need-category-driven for My Plan and for Needs Covered elsewhere on this page).
   {c,l,d,t} follows the same fill/soft-tint/dark/text shape as NEEDS/BASE_COL.
   `cls` must match the .sol-v1/.sol-v2/.sol-v3 blocks in styles.css, which expose
   the same l/d tones as CSS variables so the expanded mini-cards reuse them too. */
const SOL_VARIANTS=[
  {cls:'sol-v1',pkg:{c:'#5DB5B2',l:'#DDF1F0',d:'#2F8C89',t:'#07393A'},addon:{c:'#7C8CE0',l:'#E8EBFB',d:'#5A6BC4',t:'#232F63'}},
  {cls:'sol-v2',pkg:{c:'#E2984A',l:'#FBEEDC',d:'#B8752A',t:'#4A2F0A'},addon:{c:'#57BE9A',l:'#E1F6EE',d:'#358F73',t:'#123D31'}},
  {cls:'sol-v3',pkg:{c:'#5B8FE0',l:'#E5EEFC',d:'#3E6BB8',t:'#132C52'},addon:{c:'#E28C95',l:'#FCE9EB',d:'#BE6169',t:'#551E22'}},
];
const solVariant=pos=>SOL_VARIANTS[pos%SOL_VARIANTS.length];
/* the solution drawn as its own puzzle: base package + one piece per add-on */
function solPuzzle(sol,variant){
  const adds=sol.addons||[],n=adds.length,H=120,BW=170,AW=124,g=3,k=24,x0=k*1.1+4,y0=6,id='sp'+(uid++);
  const W=x0+BW+n*(AW+g)+k*1.1+4;let under='',out='';
  const bp=piece3d(piecePath(x0,y0,BW,H,[0,1,0,1],20,k),variant.pkg,id+'b',x0,y0,BW,H);
  under+=bp.under;out+=bp.top+iconTile(x0+BW/2,y0+H/2-12,'plan',variant.pkg)+pill(x0+BW/2,y0+H-22,'Base',variant.pkg.t,12);
  adds.forEach((a,i)=>{const x=x0+BW+g+i*(AW+g),icon=addonStyle(a.category).icon;
    const ap=piece3d(piecePath(x,y0,AW,H,[0,1,0,-1],20,k),variant.addon,id+'a'+i,x,y0,AW,H);
    under+=ap.under;out+=ap.top+iconTile(x+AW/2+8,y0+H/2-12,icon,variant.addon)+pill(x+AW/2+8,y0+H-22,'Add-on',variant.addon.t,12);});
  return `<svg viewBox="0 0 ${W} ${H+y0+20}" aria-hidden="true">${under}${out}</svg>`;
}
function solCard(sol,rec,sols,pos){
  const id=sol.solution_id,open=!!S.open[id],sel=S.selected===id,isRec=!!rec&&sol.solution_id===rec.solution_id,variant=solVariant(pos);
  return `<article class="sol ${variant.cls}${isRec?' rec':''}${sel?' sel':''}" aria-label="${isRec?'Recommended solution':'Solution'} ${esc(solName(sol))}">
    <div class="sol-top">${isRec?`<span class="badge">${svgIcon('check')}Recommended</span>`:'<span></span>'}</div>
    <div class="sol-art">${solPuzzle(sol,variant)}</div>
    <h3>${esc(sol.base_package.name)}${(sol.addons||[]).map(a=>`<span>+ ${esc(a.name)}</span>`).join('')}</h3>
    <div class="sol-price"><p class="price"><b>${money(sol.total_monthly_price)}</b>SAR / month</p>${delta(sol.total_monthly_price)}</div>
    ${vsCurrent(sol.total_monthly_price)}
    ${coverage(sol,false)}
    ${coversLine(sol)}
    ${open?`<div class="sol-more">${details(sol,sols.length,rec,sols)}</div>`:''}
    <div class="sol-f">${selBtn(sol,isRec)}<button class="linkbtn" data-open="${esc(id)}" aria-expanded="${open}">${open?'Hide details':'View details'} ${svgIcon('chev','chev'+(open?' up':''))}</button></div>
  </article>`;
}
function resultsView(){
  const sols=[...((S.results&&S.results.solutions)||[])].sort((a,b)=>(a.rank??99)-(b.rank??99));
  if(!sols.length)return errorView('No solutions came back for this puzzle. Try adjusting a piece.');
  /* recommendation_available===false: TELORA produced no recommendation, so none is implied. */
  const recAvailable=S.results.recommendation_available!==false;
  const rec=recAvailable?(sols.find(x=>x.recommended)||sols[0]):null;
  const ordered=rec?[rec,...sols.filter(x=>x!==rec)]:sols;
  const sel=sols.find(x=>x.solution_id===S.selected);
  const lede=rec?'The recommended one fits your needs and budget best.':'Compare them and choose the one that suits you.';
  return `<div class="head"><div><h1>Your solution is ready</h1><p class="lede">${sols.length===1?'One solution':sols.length+' solutions'} built from your pieces. ${lede}${S.sample?` <span class="sample-tag">${svgIcon('eye')}Sample content</span>`:''}</p></div></div>
  <div class="sols">${ordered.map((x,i)=>solCard(x,rec,sols,i)).join('')}</div>
  ${S.requestId&&!S.sample?`<div class="sol-cancel"><button type="button" class="linkbtn" data-cancel-request>Cancel request</button><small>Not right for you? Cancelling keeps your current plan as it is.</small></div>`:''}
  ${sel?`<div class="applybar" role="region" aria-label="Selected plan"><div><small>Selected</small><b>${esc(solName(sel))}</b><span>${money(sel.total_monthly_price)} SAR / month</span></div><button class="btn primary" data-apply>Apply ${svgIcon('right')}</button></div>`:''}
`;
}

/* ---------- apply: confirm modal ---------- */
function openApply(){
  const sol=S.results.solutions.find(x=>x.solution_id===S.selected);if(!sol)return;
  const cur=App.state.plan,diff=Math.round((sol.total_monthly_price-cur.total_monthly_price)*100)/100;
  const dTxt=diff===0?'Same monthly price':`${diff>0?'+':'−'}${money(Math.abs(diff))} SAR / month`;
  App.modal(`<h2 id="m-title">Apply this plan?</h2>
    <p class="m-lede">Your current plan will be replaced right away.</p>
    <div class="swap">
      <div class="sw from"><small>Current plan</small><b>${esc(planName(cur))}</b><span>${money(cur.total_monthly_price)} SAR / month</span></div>
      <div class="sw-arrow" aria-hidden="true">${svgIcon('right')}</div>
      <div class="sw to"><small>New plan</small><b>${esc(solName(sol))}</b><span>${money(sol.total_monthly_price)} SAR / month</span></div>
    </div>
    <p class="m-diff ${diff>0?'up':diff<0?'down':''}">${dTxt}</p>
    <div class="m-err" id="m-err" role="alert" hidden></div>
    <div class="m-foot"><button class="btn" data-close>Cancel</button><button class="btn primary" id="m-apply">Apply plan</button></div>`,
  ()=>{document.getElementById('m-apply').addEventListener('click',async e=>{
    const b=e.currentTarget,er=document.getElementById('m-err');b.disabled=true;b.textContent='Applying…';er.hidden=true;
    try{const res=await api.apply(S.requestId,sol.solution_id);App.closeModal();S=fresh();App.afterApply(res);}
    catch(err){er.innerHTML=`${svgIcon('alert')}<span>${esc(err.message)}</span>`;er.hidden=false;b.disabled=false;b.textContent='Try again';}
  });});
}

/* ---------- build ---------- */
function requirements(){return {needs:S.slots.map((id,i)=>({need:byId(id).name,priority:i+1,level:LV[S.lv[id]]})),preferred_budget_sar:S.budget};}
function startBuild(){
  if(S.handle)S.handle.cancel();
  Object.assign(S,{step:5,view:'building',stages:{},error:null,errorKind:null,results:null,selected:null,requestId:null,open:{},sample:false});render();toTop();
  S.handle=api.build(requirements(),onEvent);
}
function onEvent(ev){
  if(ev.request_id)S.requestId=ev.request_id;
  if(ev.status==='cancelled'||ev.status==='completed'){
    // The request already ended (e.g. in another tab): nothing to select any more.
    S=fresh();App.refreshRequests();App.go('requests');return;
  }
  const idx=STAGES.findIndex(s=>s[0]===ev.stage);
  STAGES.forEach((s,i)=>{if(i<idx)S.stages[s[0]]='done';});
  if(ev.status==='failed'){
    // Terminal state, not an incremental patch: the reported stage is failed,
    // everything before it is done, and everything after it must never be
    // left active/done from earlier (possibly stale) events.
    S.stages[ev.stage]='failed';
    STAGES.forEach((s,i)=>{if(i>idx)S.stages[s[0]]='todo';});
    S.errorKind=ev.category==='connection'?'connection':'technical';
    S.error=ev.message||'Something went wrong while building your plan.';
    S.view='error';S.handle=null;render();toTop();App.refreshRequests();return;
  }
  if(ev.stage==='done'){
    S.handle=null;
    if(!ev.data?.solutions?.length){
      S.error='TELORA could not find a complete solution for these needs. Adjust a piece and try again.';
      S.errorKind='business';
      S.view='error';
      render();toTop();App.refreshRequests();return;
    }
    STAGES.forEach(s=>S.stages[s[0]]='done');S.results=ev.data;
    if(S.reopened){S.view='results';render();toTop();}else updateStages();
    App.refreshRequests();return;
  }
  S.stages[ev.stage]=ev.status==='done'?'done':'active';updateStages();
}
function toTop(){window.scrollTo({top:0,behavior:RM?'auto':'smooth'});}

/* ---------- render ---------- */
function render(anim=true){
  const c=document.getElementById('card');if(!c)return;
  c.classList.toggle('building',S.view==='building'&&S.step===5);
  const entering=S.step===4&&S.view==='review'&&!RM;c.classList.toggle('enter',entering);
  c.innerHTML=[step1,step2,step3,step4,step5][S.step-1]();
  if(S.step===3)bShown=S.budget;
  if(entering)c.querySelectorAll('.final .wv').forEach((p,i)=>{const [x,y,w,h,k]=p.dataset.g.split(',').map(Number),empty=y+h+k*1.7;
    p.setAttribute('d',wave(x,y,w,h,0,k,0,0,empty));setTimeout(()=>slosh(p,empty,1300),(i*.3+.15+.4)*1000);});
  if(anim){c.classList.remove('fade');void c.offsetWidth;c.classList.add('fade');}
  if(S.step===1)document.getElementById('slots').innerHTML=boardSVG();
  litBefore=new Set(STAGES.slice(0,4).map(([id],i)=>S.stages[id]==='done'?i:-1).filter(i=>i>=0));litPieces();
  chrome();
}
function rects(){const m={};document.querySelectorAll('.pc').forEach(el=>m[el.dataset.pick]=el.getBoundingClientRect());return m;}
function flip(before){if(RM)return;document.querySelectorAll('.pc').forEach(el=>{const b=before[el.dataset.pick];if(!b)return;const a=el.getBoundingClientRect(),dx=b.left-a.left,dy=b.top-a.top;
  if(Math.abs(dx)+Math.abs(dy)>1)el.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'none'}],{duration:420,easing:'cubic-bezier(.2,.8,.2,1)'});});}
function snapEl(el){if(!el||RM)return;el.classList.remove('snap');void el.getBoundingClientRect();el.classList.add('snap');el.addEventListener('animationend',()=>el.classList.remove('snap'),{once:true});}
function pulse(el){if(!el||RM)return;el.animate([{transform:'scale(1)'},{transform:'scale(1.05)'},{transform:'scale(1)'}],{duration:420,easing:'ease-out'});}
/* piece flies along a small arc, then the target clicks into place */
function fly(html,from,toEl,hide,done){
  if(RM||!toEl){done&&done();return;}
  hide.forEach(el=>el&&(el.style.opacity=0));
  const to=toEl.getBoundingClientRect(),d=document.createElement('div');d.className='flyer';d.innerHTML=html;
  Object.assign(d.style,{left:from.left+'px',top:from.top+'px',width:from.width+'px',height:from.height+'px'});document.body.appendChild(d);
  const dx=to.left+to.width/2-(from.left+from.width/2),dy=to.top+to.height/2-(from.top+from.height/2),sc=Math.max(.4,Math.min(to.width/from.width,to.height/from.height)*.8),lift=Math.min(90,Math.abs(dy)*.35+30);
  const a=d.animate([{transform:'translate(0,0) scale(1) rotate(0deg)'},{transform:`translate(${dx*.5}px,${dy*.5-lift}px) scale(${(1+sc)/2*1.06}) rotate(${dx>0?-7:7}deg)`,offset:.5},{transform:`translate(${dx}px,${dy}px) scale(${sc}) rotate(0deg)`,opacity:.85}],{duration:560,easing:'cubic-bezier(.45,.05,.35,1)',fill:'forwards'});
  const land=()=>{d.remove();hide.forEach(el=>{if(!el)return;el.style.opacity='';if(el.tagName==='BUTTON')el.animate([{transform:'scale(.82)'},{transform:'scale(1)'}],{duration:420,easing:'cubic-bezier(.34,1.7,.64,1)'});else snapEl(el);});done&&done();};
  a.onfinish=land;a.oncancel=land;
}
function go(n){if(n===5&&!S.results)n=4;S.step=n;if(S.view!=='building')S.view=n===5?'results':'review';render();toTop();}

/* ---------- events (scoped to the Design page) ---------- */
document.addEventListener('click',e=>{
  if(!e.target.closest('#design'))return;
  const t=e.target.closest('[data-pick],[data-remove],[data-reset],[data-next],[data-back],[data-go],[data-q],[data-build],[data-select],[data-adjust],[data-restart],[data-open],[data-apply],[data-view-results],[data-cancel-request]');if(!t)return;
  if('viewResults' in t.dataset){S.view='results';render();toTop();return;}
  if(t.dataset.pick){const i=S.slots.indexOf(null);if(i>-1){const id=t.dataset.pick,src=t.querySelector('svg'),from=src.getBoundingClientRect(),html=src.outerHTML,before=rects();
      S.slots[i]=id;if(S.lv[id]==null)S.lv[id]=1;render(false);flip(before);
      const tg=[document.querySelector(`[data-remove="${i}"]`),document.querySelector(`[data-ext="${i}"]`)];
      fly(html,from,tg[0],tg,()=>{if(i===2)pulse(document.querySelector('[data-next]'));});}}
  else if(t.dataset.remove!=null){const i=+t.dataset.remove,id=S.slots[i],from=t.getBoundingClientRect(),before=rects();
      S.slots[i]=null;const f=S.slots.filter(Boolean);S.slots=[...f,null,null,null].slice(0,3);render(false);flip(before);
      const back=document.querySelector(`.pc[data-pick="${id}"]`);
      for(let j=i;j<3;j++)if(S.slots[j])[document.querySelector(`[data-remove="${j}"]`),document.querySelector(`[data-ext="${j}"]`)].forEach(snapEl);
      if(back)fly(availPiece(byId(id)),from,back.querySelector('svg'),[back]);}
  else if('reset' in t.dataset){S.slots=[null,null,null];render(false);}
  else if('next' in t.dataset){if(S.step===1&&S.slots.includes(null))return;S.view='review';go(S.step+1);}
  else if('back' in t.dataset)go(S.step-1);
  else if(t.dataset.go)go(+t.dataset.go);
  else if(t.dataset.q){setBudget(+t.dataset.q);document.getElementById('brange').value=S.budget;}
  else if('build' in t.dataset)startBuild();
  else if(t.dataset.select){S.selected=t.dataset.select;render(false);}
  else if(t.dataset.open){S.open[t.dataset.open]=!S.open[t.dataset.open];render(false);}
  else if('apply' in t.dataset)openApply();
  else if('cancelRequest' in t.dataset)App.confirmCancel(S.requestId);
  else if('adjust' in t.dataset){S.view='review';S.step=1;render();toTop();}
  else if('restart' in t.dataset){S=fresh();render();toTop();}
});
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('#design')&&e.target.matches('[data-remove]')){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});
function setBudget(v){S.budget=v;countTo(v);document.getElementById('wallet').setAttribute('style',walletTone(v));const r=document.getElementById('brange');r.style.setProperty('--p',pct(v));
  document.querySelectorAll('#quick button').forEach(b=>b.classList.toggle('on',+b.dataset.q===v));chrome();}
document.addEventListener('input',e=>{
  const t=e.target;if(!t.closest('#design'))return;
  if(t.dataset.lv){const id=t.dataset.lv,lv=+t.value,n=byId(id);if(S.lv[id]===lv)return;
    const art=document.getElementById('art-'+id),old=art.querySelector('.wv'),oldTop=(()=>{const [x,y,w,h,k]=old.dataset.g.split(',').map(Number);return topOf(y,h,+old.dataset.fl,k)})();
    S.lv[id]=lv;t.setAttribute('aria-valuetext',LV[lv]);
    art.innerHTML=lvArt(n,lv);slosh(art.querySelector('.wv'),oldTop);
    document.querySelectorAll('#tk-'+id+' span').forEach((s,j)=>s.classList.toggle('on',j===lv));}
  if(t.id==='brange')setBudget(+t.value);
});
let lastV=null;window.addEventListener('resize',()=>{if(S.step!==1||!root())return;const v=document.getElementById('card').clientWidth<620;if(v!==lastV){lastV=v;document.getElementById('slots').innerHTML=boardSVG();}});

return {
  mount(el){
    el.innerHTML=`<div id="design"><nav class="stepper-wrap" aria-label="Design steps"><ol class="stepper" id="stepper"></ol></nav><section class="card" id="card" aria-live="polite"></section></div>`;
    render(false);lastV=document.getElementById('card').clientWidth<620;
  },
  reset(){if(S.handle)S.handle.cancel();S=fresh();},
  /* View solutions: restore a request's persisted solutions from the server (never regenerated). */
  open(r){
    if(S.handle)S.handle.cancel();
    S=fresh();
    (r.needs||[]).forEach(n=>{const p=NEEDS.find(x=>x.name===n.need);
      if(p&&n.priority>=1&&n.priority<=3){S.slots[n.priority-1]=p.id;S.lv[p.id]=Math.max(0,LV.indexOf(n.level));}});
    if(r.budget)S.budget=r.budget;
    Object.assign(S,{step:5,view:'building',requestId:r.request_id,reopened:true});
    S.handle=api.reopen(r.request_id,onEvent);
  },
  /* the request was cancelled: close its solutions if they are the ones on screen */
  closeRequest(id){if(S.requestId===id){if(S.handle)S.handle.cancel();S=fresh();}},
  get state(){return S;},
  STAGES
};
})();
