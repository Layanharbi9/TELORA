/* ============================================================
   TELORA core: icons, need colours, puzzle drawing, formatters.
   Shared by every page. No data access here.
   ============================================================ */

const IC = {
  data:{d:"M6 19v-5 M12 19v-9 M18 19v-14",w:3.2},
  calls:{d:"M5 4h4l2 5l-2.5 1.5a11 11 0 0 0 5 5l1.5 -2.5l5 2v4a2 2 0 0 1 -2 2a16 16 0 0 1 -15 -15a2 2 0 0 1 2 -2"},
  gaming:{d:"M4 6h16a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-8a2 2 0 0 1 2 -2z M6 12h4 M8 10v4 M15 11v.01 M18 13v.01"},
  streaming:{d:"M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-10a2 2 0 0 1 2 -2z M10 9l5 3l-5 3z"},
  social:{d:"M3 20l1.3 -3.9c-2.324 -3.437 -1.426 -7.872 2.1 -10.374c3.526 -2.501 8.59 -2.296 11.845 .48c3.255 2.777 3.695 7.266 1.029 10.501c-2.666 3.235 -7.615 4.215 -11.574 2.293l-4.7 1 M8 12v.01 M12 12v.01 M16 12v.01"},
  roaming:{d:"M16 10h4a2 2 0 0 1 0 4h-4l-4 7h-3l2 -7h-4l-2 2h-3l2 -4l-2 -4h3l2 2h4l-2 -7h3z"},
  intl:{d:"M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0 M3.6 9h16.8 M3.6 15h16.8 M11.5 3a17 17 0 0 0 0 18 M12.5 3a17 17 0 0 1 0 18"},
  multi:{d:"M14 8h6a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-6a1 1 0 0 1 -1 -1v-10a1 1 0 0 1 1 -1z M18 8v-3a1 1 0 0 0 -1 -1h-13a1 1 0 0 0 -1 1v12a1 1 0 0 0 1 1h9 M16 9h2"},
  share:{d:"M3 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0 M15 6a3 3 0 1 0 6 0a3 3 0 1 0 -6 0 M15 18a3 3 0 1 0 6 0a3 3 0 1 0 -6 0 M8.7 10.7l6.6 -3.4 M8.7 13.3l6.6 3.4"},
  wallet:{d:"M17 8v-3a1 1 0 0 0 -1 -1h-10a2 2 0 0 0 0 4h12a1 1 0 0 1 1 1v3 M19 16v3a1 1 0 0 1 -1 1h-12a2 2 0 0 1 -2 -2v-12 M20 12v4h-4a2 2 0 0 1 0 -4h4"},
  bulb:{d:"M3 12h1 M12 3v1 M20 12h1 M5.6 5.6l.7 .7 M18.4 5.6l-.7 .7 M9 16a5 5 0 1 1 6 0a3.5 3.5 0 0 0 -1 3a2 2 0 0 1 -4 0a3.5 3.5 0 0 0 -1 -3 M9.7 17h4.6"},
  check:{d:"M5 12l5 5l10 -10",w:2.6},
  left:{d:"M15 6l-6 6l6 6",w:2.4}, right:{d:"M5 12h14 M13 18l6 -6 M13 6l6 6",w:2.2},
  reset:{d:"M20 11a8.1 8.1 0 0 0 -15.5 -2m-.5 -4v4h4 M4 13a8.1 8.1 0 0 0 15.5 2m.5 4v-4h-4",w:2.2},
  home:{d:"M5 12h-2l9 -9l9 9h-2 M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-7 M9 21v-6a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v6"},
  plan:{d:"M6 3h8.5l4.5 4.5v12.5a1 1 0 0 1 -1 1h-12a1 1 0 0 1 -1 -1v-16a1 1 0 0 1 1 -1z M9 11h6v6h-6z M12 11v6"},
  usage:{d:"M3 13a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1z M15 9a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1z M9 5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1z"},
  puzzle:{d:"M4 7h3a1 1 0 0 0 1 -1v-1a2 2 0 0 1 4 0v1a1 1 0 0 0 1 1h3a1 1 0 0 1 1 1v3a1 1 0 0 0 1 1h1a2 2 0 0 1 0 4h-1a1 1 0 0 0 -1 1v3a1 1 0 0 1 -1 1h-3a1 1 0 0 1 -1 -1v-1a2 2 0 0 0 -4 0v1a1 1 0 0 1 -1 1h-3a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1h1a2 2 0 0 0 0 -4h-1a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1"},
  requests:{d:"M13 5h8 M13 9h5 M13 15h8 M13 19h5 M4 4h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1z M4 14h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1z"},
  user:{d:"M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0 M6 21v-2a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v2"},
  shield:{d:"M12 3a12 12 0 0 0 8.5 3a12 12 0 0 1 -8.5 15a12 12 0 0 1 -8.5 -15a12 12 0 0 0 8.5 -3"},
  search:{d:"M3 10a7 7 0 1 0 14 0a7 7 0 1 0 -14 0 M21 21l-6 -6"},
  updown:{d:"M8 9l4 -4l4 4 M16 15l-4 4l-4 -4"},
  chev:{d:"M6 9l6 6l6 -6"},
  logout:{d:"M14 8v-2a2 2 0 0 0 -2 -2h-7a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h7a2 2 0 0 0 2 -2v-2 M9 12h12l-3 -3 M18 15l3 -3"},
  x:{d:"M18 6l-12 12 M6 6l12 12",w:2.4},
  minus:{d:"M5 12h14",w:2.6},
  alert:{d:"M12 9v4 M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636 -2.87l-8.106 -13.536a1.914 1.914 0 0 0 -3.274 0z M12 16h.01"},
  sms:{d:"M8 9h8 M8 13h6 M18 4a3 3 0 0 1 3 3v8a3 3 0 0 1 -3 3h-5l-5 3v-3h-2a3 3 0 0 1 -3 -3v-8a3 3 0 0 1 3 -3h12z"},
  eye:{d:"M10 12a2 2 0 1 0 4 0a2 2 0 0 0 -4 0 M21 12c-2.4 4 -5.4 6 -9 6c-3.6 0 -6.6 -2 -9 -6c2.4 -4 5.4 -6 9 -6c3.6 0 6.6 2 9 6"},
  star:{d:"M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873z"},
  scale:{d:"M12 3v18 M8 21h8 M5 7l-3 6a3 3 0 0 0 6 0z M19 7l-3 6a3 3 0 0 0 6 0z M5 7h14 M12 3l3 4h-6z"}
};
const svgIcon=(k,cls='')=>`<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${IC[k].w||2}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${IC[k].d}"/></svg>`;

const LOGO_MARK=`<svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true" style="margin:0 1px"><rect x="1" y="1" width="15" height="15" rx="5" fill="#8E7CF3"/><rect x="18" y="1" width="15" height="15" rx="5" fill="#3CC69C"/><rect x="1" y="18" width="15" height="15" rx="5" fill="#F4B942"/><rect x="18" y="18" width="15" height="15" rx="5" fill="#4BB0EE"/><circle cx="17" cy="8.5" r="3.2" fill="#8E7CF3"/><circle cx="25.5" cy="17" r="3.2" fill="#3CC69C"/><circle cx="8.5" cy="17" r="3.2" fill="#F4B942"/></svg>`;
const logoHTML=(tag=true)=>`<div class="logo" aria-label="TELORA, pieced around you"><div class="word">TEL${LOGO_MARK}RA</div>${tag?'<div class="tag">PIECED AROUND YOU</div>':''}</div>`;

/* ---------- needs (the nine puzzle pieces of the form) ---------- */
const NEEDS=[
 {id:'data',name:'Data',lines:['Data'],sub:'Stay connected everywhere',desc:'For browsing, apps, and downloads',icon:'data',c:'#8E7CF3',l:'#ECE8FF',d:'#6A57D6',t:'#2A1F78',e:[0,1,0,0]},
 {id:'calls',name:'Calls',lines:['Calls'],sub:'Talk without limits',desc:'For local calls and minutes',icon:'calls',c:'#3CC69C',l:'#DDF7EE',d:'#1F9C77',t:'#0A4A38',e:[0,1,0,-1]},
 {id:'gaming',name:'Gaming',lines:['Gaming'],sub:'Play without the lag',desc:'For online play and low latency',icon:'gaming',c:'#6A93F0',l:'#E4ECFF',d:'#456FCC',t:'#15306E',e:[1,0,0,0]},
 {id:'streaming',name:'Streaming',lines:['Streaming'],sub:'Enjoy your favorite content',desc:'For video, music, and entertainment',icon:'streaming',c:'#F27A6E',l:'#FFE7E3',d:'#D0564B',t:'#661B14',e:[0,1,0,1]},
 {id:'social',name:'Social Media',lines:['Social'],sub:'Stay close on social apps',desc:'For chats, feeds, and stories',icon:'social',c:'#4DB1EE',l:'#DFF2FD',d:'#2B8CCB',t:'#0D3D5C',e:[0,0,1,1]},
 {id:'roaming',name:'Roaming',lines:['Roaming'],sub:'Use your plan abroad',desc:'For staying connected while traveling',icon:'roaming',c:'#F4B942',l:'#FFF3D9',d:'#CF9120',t:'#5A3C04',e:[1,1,0,0]},
 {id:'intl',name:'International Calls',lines:['Intl Calls'],sub:'Reach family worldwide',desc:'For calling numbers in other countries',icon:'intl',c:'#27AFB5',l:'#DAF5F5',d:'#178A8F',t:'#08403F',e:[0,1,0,-1]},
 {id:'multi',name:'Multi-device / Extra SIM',lines:['Extra SIM'],sub:'One plan, more devices',desc:'For watches, tablets, and extra lines',icon:'multi',c:'#E57FB6',l:'#FDE6F2',d:'#C25691',t:'#5A173C',e:[0,0,0,1]},
 {id:'share',name:'Data Sharing',lines:['Sharing'],sub:'Share with the people you love',desc:'For sharing data with family',icon:'share',c:'#7DC45C',l:'#E8F6DF',d:'#579E3A',t:'#244512',e:[1,0,0,-1]}
];
const BUDGET={c:'#E8C98F',l:'#F7ECDA',d:'#C7A15F',t:'#4E3A18'};
const BASE_COL={c:'#5DB5B2',l:'#DDF1F0',d:'#2F8C89',t:'#07393A'};
const LV=['Low','Medium','High'],LVH=['Occasional use','Regular use','Heavy use'],LVF=[.32,.6,1];
const FULL={social:['Social','Media'],intl:['International','Calls'],multi:['Multi-device','Extra SIM'],share:['Data','Sharing']};
const full=n=>FULL[n.id]||n.lines;
const byId=id=>NEEDS.find(n=>n.id===id);
const needByName=name=>NEEDS.find(n=>n.name===name||n.id===name)||{c:'#B9B2A6',l:'#EFEAE2',t:'#3E3A33',d:'#8F887C',icon:'check',name};
/* add-on category -> colour/icon family, so an add-on reads as the need it serves */
const ADDON_NEED={'Social':'social','Streaming':'streaming','Gaming':'gaming','Roaming':'roaming','International':'intl','Multi-SIM':'multi','Data sharing':'share','Youth':'social'};
function addonStyle(cat){const n=byId(ADDON_NEED[cat]);return n?{col:n,icon:n.icon}:{col:BUDGET,icon:'wallet'};}

/* ---------- puzzle geometry ---------- */
let uid=0;
function piecePath(x,y,w,h,e,r=16,k=20,km=[.5,.5,.5,.5]){
  const C=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]];let d='';
  const f=p=>p[0].toFixed(1)+' '+p[1].toFixed(1);
  for(let i=0;i<4;i++){
    const a=C[i],b=C[(i+1)%4],c=C[(i+2)%4];
    const L=Math.hypot(b[0]-a[0],b[1]-a[1]),u=[(b[0]-a[0])/L,(b[1]-a[1])/L],n=[u[1],-u[0]];
    const P=(s,t)=>[a[0]+u[0]*s+n[0]*t,a[1]+u[1]*s+n[1]*t];
    if(i===0)d+='M'+f(P(r,0));
    const t=e[i];
    if(t){const m=L*km[i],R=k*.56,nw=k*.3,hc=k*.8,y0=hc-Math.sqrt(R*R-nw*nw),fl=k*.18;
      d+=' L'+f(P(m-nw-fl,0))+' Q'+f(P(m-nw,0))+' '+f(P(m-nw,t*fl))+' L'+f(P(m-nw,t*y0))+` A${R.toFixed(1)} ${R.toFixed(1)} 0 1 ${t>0?1:0} `+f(P(m+nw,t*y0))+' L'+f(P(m+nw,t*fl))+' Q'+f(P(m+nw,0))+' '+f(P(m+nw+fl,0));}
    const L2=Math.hypot(c[0]-b[0],c[1]-b[1]),u2=[(c[0]-b[0])/L2,(c[1]-b[1])/L2];
    d+=' L'+f(P(L-r,0))+' Q'+f(b)+' '+f([b[0]+u2[0]*r,b[1]+u2[1]*r]);
  }
  return d+'Z';
}
const RM=matchMedia('(prefers-reduced-motion: reduce)').matches;
const topOf=(y,h,lv,k)=>lv>=1?y-k*1.7:y+h-lv*h;
function wave(x,y,w,h,lv,k,amp,ph=0,top){
  const pad=k*1.7;if(top==null)top=topOf(y,h,lv,k);if(amp==null)amp=lv>=1?0:4;
  const x0=x-pad,x1=x+w+pad,N=30;let d='';
  for(let i=0;i<=N;i++){const X=x0+(x1-x0)*i/N,Y=top+amp*Math.sin(i/N*Math.PI*3+ph);d+=(i?' L':'M')+X.toFixed(1)+' '+Y.toFixed(1);}
  return d+` L${x1} ${y+h+pad} L${x0} ${y+h+pad}Z`;
}
/* liquid settling: fill glides to its new level, then the surface sloshes and calms */
function slosh(path,fromTop,dur=1500){
  if(!path)return;const [x,y,w,h,k]=path.dataset.g.split(',').map(Number),lv=+path.dataset.fl;
  const toTop=topOf(y,h,lv,k),rest=lv>=1?0:4;
  if(RM){path.setAttribute('d',wave(x,y,w,h,lv,k));return;}
  cancelAnimationFrame(path._raf);const t0=performance.now();
  const f=now=>{const t=Math.min(1,(now-t0)/dur),p=1-Math.pow(1-Math.min(1,t/.55),3);
    const top=fromTop+(toTop-fromTop)*p,amp=rest+9*Math.exp(-3.4*t)*(1-t);
    path.setAttribute('d',wave(x,y,w,h,lv,k,amp,t*Math.PI*6,top));
    if(t<1)path._raf=requestAnimationFrame(f);else path.setAttribute('d',wave(x,y,w,h,lv,k));};
  path._raf=requestAnimationFrame(f);
}
function iconG(k,cx,cy,s,col){const sc=s/24;return `<g transform="translate(${cx-s/2} ${cy-s/2}) scale(${sc})" fill="none" stroke="${col}" stroke-width="${IC[k].w||2}" stroke-linecap="round" stroke-linejoin="round"><path d="${IC[k].d}"/></g>`;}
function piece(o){
  const {x,y,w,h,e,col,lv=1,k=20,r=16,depth=7,km}=o;const id='cp'+(uid++);const p=piecePath(x,y,w,h,e,r,k,km);
  const ext=`<path d="${p}" transform="translate(0 ${depth})" fill="${col.d}"/>`;
  if(o.layer==='ext')return ext;
  return (o.layer==='top'?'':ext)+`<clipPath id="${id}"><path d="${p}"/></clipPath>
   <path d="${p}" fill="${col.l}"/>
   <path d="${wave(x,y,w,h,lv,k)}" fill="${col.c}" clip-path="url(#${id})" class="wv" data-g="${x},${y},${w},${h},${k}" data-fl="${lv}"/>
   <path d="${p}" fill="none" stroke="#fff" stroke-opacity=".4" stroke-width="2"/>`;
}
function pill(cx,cy,txt,col,fs=15){const w=txt.length*fs*.56+26;return `<rect x="${cx-w/2}" y="${cy-fs*.95}" width="${w}" height="${fs*1.9}" rx="${fs*.95}" fill="#fff" fill-opacity=".92"/><text x="${cx}" y="${cy+fs*.35}" text-anchor="middle" font-size="${fs}" font-weight="700" fill="${col}">${txt}</text>`;}
function lines(cx,y,ls,fs,col,fw=700){return `<text x="${cx}" y="${y}" text-anchor="middle" font-size="${fs}" font-weight="${fw}" fill="${col}">${ls.map((l,i)=>`<tspan x="${cx}" dy="${i?fs*1.1:0}">${esc(l)}</tspan>`).join('')}</text>`;}
function wrapWords(s,max){const ls=[''];String(s).split(' ').forEach(w=>{const t=(ls[ls.length-1]+' '+w).trim();if(t.length>max&&ls[ls.length-1])ls.push(w);else ls[ls.length-1]=t;});return ls;}

/* ---------- formatters ---------- */
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>{const n=+v;return Number.isInteger(n)?n.toLocaleString('en-US'):n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});};
/* monthly price change from one plan to another: tone is 'down' (pays less), 'up' (pays more) or 'same' */
const priceChange=(from,to)=>{if(from==null||to==null)return null;const d=Math.round((+to-+from)*100)/100,pc=+from?Math.round(Math.abs(d)/+from*100):0;
  return {diff:d,abs:Math.abs(d),pct:pc,tone:d<0?'down':d>0?'up':'same'};};
const isUnl=v=>String(v).toLowerCase()==='unlimited';
const qty=(v,unit)=>isUnl(v)?'Unlimited':`${money(v)}${unit?' '+unit:''}`;
const dateTime=iso=>{const d=new Date(iso);if(isNaN(d))return esc(iso);return d.toLocaleString('en-GB',{day:'numeric',month:'short',year:'numeric'})+', '+d.toLocaleString('en-US',{hour:'numeric',minute:'2-digit',hour12:true});};
const initials=name=>String(name).split(/[\s-]+/).filter(Boolean).filter(w=>!/^al$/i.test(w)).map(w=>w[0]).slice(0,2).join('').toUpperCase();
const firstName=name=>String(name).split(' ')[0];
const planName=p=>[p.base_package.package_name,...(p.active_addons||[]).map(a=>a.addon_name)].join(' + ');
const solName=s=>[s.base_package.name,...(s.addons||[]).map(a=>a.name)].join(' + ');

/* package benefits shown under "Included" (contract length and package type are out of scope) */
function packageBenefits(p){
  const out=[],has=v=>v!=null&&v!==0&&v!=='0'&&v!=='None'&&v!=='No'&&v!=='';
  if(has(p.social_data_gb))out.push({icon:'social',t:isUnl(p.social_data_gb)?'Unlimited social data':`${money(p.social_data_gb)} GB social data`});
  if(has(p.streaming_benefit))out.push({icon:'streaming',t:p.streaming_benefit==='OTT Benefit'?'Streaming apps included':'Streaming data'});
  if(has(p.gaming_benefit))out.push({icon:'gaming',t:p.gaming_benefit==='Gaming Data'?'Gaming data':'Gaming benefit'});
  if(has(p.roaming_data_gb))out.push({icon:'roaming',t:`${money(p.roaming_data_gb)} GB roaming data`});
  if(has(p.international_minutes))out.push({icon:'intl',t:isUnl(p.international_minutes)?'Unlimited international minutes':`${money(p.international_minutes)} international minutes`});
  if(p.multi_sim==='Yes')out.push({icon:'multi',t:'Extra SIM for another device'});
  if(p.data_sharing==='Yes')out.push({icon:'share',t:'Share data with family'});
  return out;
}
/* one-line description of an add-on, built from its catalogue columns */
function addonSummary(a){
  const has=v=>v!=null&&v!==0&&v!=='None'&&v!=='No',o=[];
  if(has(a.data_gb))o.push(`${money(a.data_gb)} GB ${a.gaming_benefit==='Gaming Data'?'gaming ':''}data`);
  if(has(a.social_data_gb))o.push(isUnl(a.social_data_gb)?'Unlimited social data':`${money(a.social_data_gb)} GB social data`);
  if(has(a.roaming_data_gb))o.push(`${money(a.roaming_data_gb)} GB roaming data`);
  if(has(a.international_minutes))o.push(`${money(a.international_minutes)} international minutes`);
  if(a.multi_sim==='Yes')o.push('One extra SIM');
  if(has(a.streaming_benefit))o.push(a.streaming_benefit==='OTT Benefit'?'Streaming apps included':'Streaming data');
  return o.join(' · ')||a.category;
}

/* ---------- 3D puzzle pieces (My Plan and solution cards) ---------- */
const tint=(hex,t)=>'#'+[1,3,5].map(i=>Math.round(parseInt(hex.substr(i,2),16)*(1-t)+255*t).toString(16).padStart(2,'0')).join('');
/* one 3D puzzle piece: extruded side, lit face, soft gloss, bright rim */
function piece3d(d,col,id,x,y,w,h){
  return {under:`<path d="${d}" transform="translate(0 12)" fill="${col.d}"/>`,
    top:`<defs><linearGradient id="g${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${tint(col.c,.3)}"/><stop offset=".55" stop-color="${col.c}"/><stop offset="1" stop-color="${col.c}"/></linearGradient><clipPath id="k${id}"><path d="${d}"/></clipPath></defs>
      <path d="${d}" fill="url(#g${id})"/>
      <g clip-path="url(#k${id})"><ellipse cx="${x+w*.3}" cy="${y}" rx="${w*.75}" ry="${h*.34}" fill="#fff" opacity=".2"/></g>
      <path d="${d}" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width="2.5"/>`};
}
/* raised white tile with the icon in the piece's own colour */
function iconTile(cx,cy,icon,col){
  const s=48,x=cx-s/2,y=cy-s/2;
  return `<rect x="${x}" y="${y+5}" width="${s}" height="${s}" rx="15" fill="${col.d}" opacity=".55"/><rect x="${x}" y="${y}" width="${s}" height="${s}" rx="15" fill="#fff"/><rect x="${x+1.5}" y="${y+1.5}" width="${s-3}" height="${s/2}" rx="13" fill="${tint(col.c,.85)}" opacity=".7"/>${iconG(icon,cx,cy,26,col.d)}`;
}
