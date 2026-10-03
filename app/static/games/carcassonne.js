(function registerCarcassonne() {
  "use strict";
  const NS = "http://www.w3.org/2000/svg", SIZE = 80;
  const NAMES = {city:"城市",road:"道路",field:"田地",monastery:"修道院"};
  const EDGE = {C:"城",R:"路",F:"田"};
  // Camera state belongs to a room, while move drafts still belong to a revision.
  const roomViews = new Map();
  let renderedRoom=null;
  function roomView(c) {
    const id=c.room.room_id;
    let view=roomViews.get(id);
    if(!view){
      const t=c.state.last_action||{x:0,y:0},zoom=innerWidth<500?.85:1;
      view={zoom,panX:-(t.x+.5)*SIZE*zoom,panY:-(t.y+.5)*SIZE*zoom};
    }
    roomViews.delete(id);roomViews.set(id,view);
    if(roomViews.size>20)roomViews.delete(roomViews.keys().next().value);
    return view;
  }
  const el = (tag, cls, text) => {const n=document.createElement(tag); if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const svgEl = (tag, attrs={}) => {const n=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));return n;};
  const act = c => c.canMove && !c.isTerminal && c.helpers.canMove() && !setup(c).busy;
  const pos = (x,y) => `${x},${y}`;
  function setup(c) {
    if (!document.getElementById("carcassonne-styles")) {
      const l=el("link");l.id="carcassonne-styles";l.rel="stylesheet";l.href="/static/games/carcassonne.css?v=4";document.head.append(l);
    }
    if (!c.uiState.carcassonne) {
      const {zoom,panX,panY}=roomView(c);
      c.uiState.carcassonne={rotation:0,selected:null,meeple:null,busy:false,error:"",zoom,panX,panY};
    }
    return c.uiState.carcassonne;
  }
  function redraw(c,key) {
    if(c.helpers.rerender()===false)return;
    if(key)document.querySelector(`[data-cc-key="${key}"]`)?.focus({preventScroll:true});
  }
  function button(c,text,key,run,disabled=false,primary=false) {
    const b=el("button",`pixel-btn compact cc-button${primary?" cc-primary":" secondary"}`,text);b.type="button";b.dataset.ccKey=key;b.disabled=disabled;
    b.addEventListener("click",event=>{if(!b.disabled)run(event);});return b;
  }
  function topology(c,t) {return c.state.topology?.[t.tile] || (c.state.current_tile?.tile===t.tile?c.state.current_tile:null);}
  function label(rid,region) {return `${NAMES[region.kind]}${region.kind==="monastery"?"":Number(rid.slice(1))+1}`;}
  // Original geometric terrain art. Paths correspond to explicit local regions;
  // decorations never merge field/road/city areas or cover their boundaries.
  function cityPath(ports) {
    if(ports.length===4)return "M0 0H100V100H0Z";
    if(ports.length===3)return "M0 0H100V100Q67 97 64 66H36Q33 97 0 100Z"; // N/E/W, field south
    if(ports.length===2 && ports.includes(1) && ports.includes(3))return "M0 0Q30 34 50 34Q70 34 100 0V100Q70 66 50 66Q30 66 0 100Z";
    if(ports.length===2)return "M0 0H100V100Q70 100 48 52Q0 30 0 0Z"; // N/E, connected
    // Tangents follow the outside edge near each corner. Adjacent but separate
    // cities (I) therefore leave a visible field channel instead of overlapping.
    return "M0 0H100C78 0 80 29 50 29S22 0 0 0Z";
  }
  function roadPath(ports,hasCity,monastery) {
    const p=[[50,0],[100,50],[50,100],[0,50]];
    if(ports.length===2) {
      const [a,b]=ports;
      return `M${p[a]} Q50 50 ${p[b]}`;
    }
    // S/T roads terminate at the city; A terminates at its monastery.
    const end=hasCity?[50,66]:monastery?[50,61]:[50,50];
    return `M${p[ports[0]]} L${end}`;
  }
  function anchor(name,rid,region) {
    const kind=region.kind;
    if(kind==="monastery")return [50,47];
    if(kind==="city") {
      if(region.ports.length===1){const p=[[50,13],[87,50],[50,87],[13,50]];return p[region.ports[0]];}
      if(region.ports.length===4)return [50,50];
      if(region.ports.length===3)return [50,24];
      return region.ports.includes(3)?[50,50]:[74,26];
    }
    if(kind==="road") {
      if(region.ports.length===1)return [[50,25],[78,50],[50,79],[22,50]][region.ports[0]];
      if(name==="D"||name==="U")return [50,50];
      return region.ports.includes(1)?[62.5,62.5]:[37.5,62.5];
    }
    const p=[[25,9],[75,9],[91,25],[91,75],[75,91],[25,91],[9,75],[9,25]];
    // Pick a point safely within each explicit field, not the arithmetic centre
    // (which may lie across a road or inside a monastery/city).
    const special={A:{f0:[24,35]},B:{f0:[24,35]},D:{f0:[50,38],f1:[50,78]},
      E:{f0:[50,65]},F:{f0:[50,13],f1:[50,87]},G:{f0:[50,13],f1:[50,87]},
      H:{f0:[50,50]},I:{f0:[40,60]},J:{f0:[24,60],f1:[80,80]},K:{f0:[76,60],f1:[20,80]},
      L:{f0:[50,38],f1:[80,80],f2:[20,80]},M:{f0:[27,74]},N:{f0:[27,74]},
      O:{f0:[38,58],f1:[18,82]},P:{f0:[38,58],f1:[18,82]},
      Q:{f0:[50,84]},R:{f0:[50,84]},S:{f0:[62,87],f1:[38,87]},T:{f0:[62,87],f1:[38,87]},
      U:{f0:[76,50],f1:[24,50]},V:{f0:[70,30],f1:[20,80]},
      W:{f0:[50,24],f1:[80,80],f2:[20,80]},X:{f0:[77,23],f1:[77,77],f2:[23,77],f3:[23,23]}};
    return special[name]?.[rid] || p[region.ports[0]] || [50,50];
  }
  function rotatedPoint(p,r) {for(let i=0;i<r;i++)p=[100-p[1],p[0]];return p;}
  function art(c,t,{choices=[],chosen=null,preview=false}={}) {
    const data=topology(c,t),svg=svgEl("svg",{viewBox:"0 0 100 100","aria-hidden":"true",focusable:"false"});
    if(!data)return svg;
    svg.append(svgEl("rect",{x:0,y:0,width:100,height:100,fill:"#c9dcad"}));
    const land=svgEl("g",{transform:`rotate(${t.rotation*90} 50 50)`});
    for(const region of Object.values(data.regions)) {
      if(region.kind!=="city")continue;
      const g=svgEl("g");
      if(region.ports.length===1)g.setAttribute("transform",`rotate(${region.ports[0]*90} 50 50)`);
      g.append(svgEl("path",{d:cityPath(region.ports),fill:"#dcb782",stroke:"#84613e","stroke-width":3,"stroke-linejoin":"round"}));land.append(g);
    }
    for(const region of Object.values(data.regions)) {
      if(region.kind!=="road")continue;
      const d=roadPath(region.ports,["S","T"].includes(t.tile),Boolean(data.regions.m0));
      land.append(svgEl("path",{d,fill:"none",stroke:"#88765b","stroke-width":10}),svgEl("path",{d,fill:"none",stroke:"#fff3d1","stroke-width":6}));
    }
    const roadCount=Object.values(data.regions).filter(r=>r.kind==="road").length;
    if(roadCount>2)land.append(svgEl("rect",{x:44,y:44,width:12,height:12,rx:2,fill:"#84613e",stroke:"#fff3d1","stroke-width":2}));
    if(data.regions.m0) {
      land.append(svgEl("rect",{x:37,y:38,width:26,height:25,fill:"#fbf2df",stroke:"#84613e","stroke-width":2}),
        svgEl("path",{d:"M32 39L50 24L68 39Z",fill:"#94615c",stroke:"#684b45","stroke-width":2}),
        svgEl("path",{d:"M46 63V51Q50 45 54 51V63",fill:"#84613e"}));
    }
    for(const [rid,r] of Object.entries(data.regions))if(r.shields) {
      const [x,y]=anchor(t.tile,rid,r);land.append(svgEl("path",{d:`M${x-6} ${y-8}h12v9l-6 6-6-6Z`,fill:"#faf2da",stroke:"#84613e","stroke-width":1.5}));
    }
    svg.append(land);
    choices.forEach(rid=>{
      const [x,y]=rotatedPoint(anchor(t.tile,rid,data.regions[rid]),t.rotation),g=svgEl("g");
      g.append(svgEl("circle",{cx:x,cy:y,r:9,fill:rid===chosen?"#58456f":"#fffaf3",stroke:"#58456f","stroke-width":1.5}));
      const text=svgEl("text",{x,y:y+3.5,"text-anchor":"middle",fill:rid===chosen?"white":"#58456f","font-size":10,"font-family":"system-ui","font-weight":700});text.textContent=choices.indexOf(rid)+1;g.append(text);svg.append(g);
    });
    const meeple=t.meeple || (preview&&chosen?{player_id:c.viewer.player_id,region:chosen}:null);
    if(meeple && data.regions[meeple.region] && !choices.length) {
      const r=data.regions[meeple.region],p=c.participants.find(p=>p.player_id===meeple.player_id),[x,y]=rotatedPoint(anchor(t.tile,meeple.region,r),t.rotation);
      const g=svgEl("g",{class:`cc-meeple seat-${p?.seat_index||0}`,transform:`translate(${x} ${y})`});
      g.append(svgEl("rect",{x:r.kind==="field"?-12:-8,y:r.kind==="field"?-6:-10,width:r.kind==="field"?24:16,height:r.kind==="field"?12:20,rx:5,fill:"var(--seat-ink)",stroke:"white","stroke-width":2}));
      const n=svgEl("text",{x:0,y:4,fill:"white","font-size":11,"font-family":"system-ui","font-weight":700,"text-anchor":"middle"});n.textContent=(p?.seat_index||0)+1;g.append(n);svg.append(g);
    }
    return svg;
  }
  function currentOption(c) {
    const ui=setup(c);return (c.privateState?.placements||[]).find(p=>ui.selected && p[0]===ui.selected[0]&&p[1]===ui.selected[1]&&p[2]===ui.rotation);
  }
  function recenter(c,point) {
    const ui=setup(c),t=point || c.state.last_action || {x:0,y:0};
    ui.panX=-(t.x+.5)*SIZE*ui.zoom;ui.panY=-(t.y+.5)*SIZE*ui.zoom;
  }
  function transform(c) {
    const ui=setup(c),world=c.board.querySelector(".cc-world");
    Object.assign(roomView(c),{zoom:ui.zoom,panX:ui.panX,panY:ui.panY});
    if(world)world.style.transform=`translate(${ui.panX}px,${ui.panY}px) scale(${ui.zoom})`;
  }
  // Coordinates are relative to the world origin at the viewport centre.
  function zoomAt(c,zoom,x=0,y=0) {
    const ui=setup(c),next=Math.min(1.8,Math.max(.6,zoom)),ratio=next/ui.zoom;
    ui.panX=x-(x-ui.panX)*ratio;ui.panY=y-(y-ui.panY)*ratio;ui.zoom=next;transform(c);
  }
  function mapGestures(c,viewport) {
    const ui=setup(c),pointers=new Map();let base=null,suppressClick=false;
    const local=(x,y)=>{const r=viewport.getBoundingClientRect();return {x:x-r.left-r.width/2,y:y-r.top-r.height/2};};
    const sample=()=>{
      const [a,b]=[...pointers.values()];
      return b?{x:(a.x+b.x)/2,y:(a.y+b.y)/2,d:Math.hypot(a.x-b.x,a.y-b.y)}:{...a,d:0};
    };
    const reset=()=>{base=pointers.size?{...sample(),panX:ui.panX,panY:ui.panY,zoom:ui.zoom}:null;};
    viewport.addEventListener("pointerdown",e=>{
      if(e.button!==0||e.target.closest(".cc-locate"))return;
      if(!pointers.size)suppressClick=false;
      pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});reset();
      if(pointers.size>1){
        suppressClick=true;
        for(const id of pointers.keys())viewport.setPointerCapture(id);
      }
    });
    viewport.addEventListener("pointermove",e=>{
      if(!pointers.has(e.pointerId))return;
      pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      const now=sample(),dx=now.x-base.x,dy=now.y-base.y;
      if(pointers.size>1){
        // Anchor the original world point to the moving two-finger midpoint.
        const origin=local(base.x,base.y),mid=local(now.x,now.y);
        ui.zoom=Math.min(1.8,Math.max(.6,base.zoom*now.d/Math.max(base.d,1)));
        const ratio=ui.zoom/base.zoom;
        ui.panX=mid.x-(origin.x-base.panX)*ratio;ui.panY=mid.y-(origin.y-base.panY)*ratio;
      }else{
        if(!suppressClick&&Math.hypot(dx,dy)<=6)return;
        suppressClick=true;viewport.setPointerCapture(e.pointerId);
        ui.panX=base.panX+dx;ui.panY=base.panY+dy;
      }
      transform(c);
    });
    const end=e=>{
      if(e.type==="lostpointercapture"&&e.target!==viewport)return;
      if(!pointers.has(e.pointerId))return;
      if(e.type!=="pointerup")suppressClick=true;
      pointers.delete(e.pointerId);reset();
      if(viewport.hasPointerCapture(e.pointerId))viewport.releasePointerCapture(e.pointerId);
    };
    viewport.addEventListener("pointerup",end);viewport.addEventListener("pointercancel",end);
    viewport.addEventListener("lostpointercapture",end);
    viewport.addEventListener("pointerleave",e=>{if(!viewport.hasPointerCapture(e.pointerId))end(e);});
    // A gesture must never become a placement. Keyboard-generated clicks remain usable.
    viewport.addEventListener("click",e=>{
      if(suppressClick&&e.detail!==0&&!e.target.closest(".cc-locate")){e.preventDefault();e.stopPropagation();}
    },true);
    viewport.addEventListener("wheel",e=>{
      e.preventDefault();
      const p=local(e.clientX,e.clientY),unit=e.deltaMode===1?16:e.deltaMode===2?viewport.clientHeight:1;
      zoomAt(c,ui.zoom*Math.exp(-Math.max(-500,Math.min(500,e.deltaY*unit))*.002),p.x,p.y);
      // If a wheel arrives during a held pointer, the next drag starts here.
      reset();
    },{passive:false});
    viewport.addEventListener("keydown",e=>{
      if(e.ctrlKey||e.metaKey||e.altKey||e.target.closest(".cc-locate"))return;
      if(["+","=","-","_"].includes(e.key)){
        e.preventDefault();zoomAt(c,ui.zoom+(["+","="].includes(e.key)?.15:-.15));reset();return;
      }
      if(e.target!==viewport)return;
      const dirs={ArrowLeft:[80,0],ArrowRight:[-80,0],ArrowUp:[0,80],ArrowDown:[0,-80]};
      if(dirs[e.key]){e.preventDefault();ui.panX+=dirs[e.key][0];ui.panY+=dirs[e.key][1];transform(c);reset();}
    });
  }
  function select(c,x,y) {
    if(!act(c))return;
    const ui=setup(c);ui.selected=[x,y];ui.meeple=null;ui.error="";redraw(c,`spot-${x}-${y}`);
  }
  function renderBoard(c) {
    if(renderedRoom!==c.room.room_id){
      const previous=roomViews.get(renderedRoom);if(previous)delete previous.focusAfterRevision;
      renderedRoom=c.room.room_id;
    }
    const ui=setup(c);c.helpers.setBoardLayout({rows:1,cols:1,large:true,ariaLabel:"卡卡颂拼图地图"});
    const root=el("div","cc-game"),head=el("div","cc-heading");
    head.append(el("strong","","拼图地图"),el("span","cc-meta",`已铺 ${c.state.board?.length||0} / 72 块 · 待抽 ${c.state.deck_count||0} 块`));root.append(head);
    const viewport=el("div","cc-viewport");viewport.tabIndex=0;viewport.setAttribute("role","region");viewport.setAttribute("aria-label","地图：拖动平移，双指或滚轮缩放；键盘用加减号缩放、方向键平移，Tab 选择合法落点");
    const world=el("div","cc-world");viewport.append(world);
    const board=c.state.board||[];
    for(const t of board) {
      const node=el("div","cc-tile"),latest=c.state.last_action?.x===t.x&&c.state.last_action?.y===t.y;
      if(latest)node.classList.add("is-latest");
      node.style.left=`${t.x*SIZE}px`;node.style.top=`${t.y*SIZE}px`;node.append(art(c,t));
      const data=topology(c,t),terrain=Object.values(data?.regions||{}).map(r=>NAMES[r.kind]);
      node.setAttribute("role","img");node.setAttribute("aria-label",`已铺板块 (${t.x},${t.y})：${[...new Set(terrain)].join("、")}${t.meeple?`；座位${(c.participants.find(p=>p.player_id===t.meeple.player_id)?.seat_index||0)+1}占据${label(t.meeple.region,data.regions[t.meeple.region])}`:""}`);
      if(latest)node.append(el("span","cc-new","新"));world.append(node);
    }
    const option=currentOption(c);
    if(act(c)&&c.state.current_tile)for(const p of c.privateState?.placements||[]) {
      if(p[2]!==ui.rotation)continue;
      const selected=option===p,b=el("button",`cc-spot${selected?" is-selected":""}`,selected?undefined:"＋");b.type="button";
      b.dataset.ccKey=`spot-${p[0]}-${p[1]}`;b.dataset.coordinate=pos(p[0],p[1]);
      b.style.left=`${p[0]*SIZE}px`;b.style.top=`${p[1]*SIZE}px`;
      b.setAttribute("aria-label",`合法落点 (${p[0]},${p[1]})${selected?"，预览未提交":""}`);b.setAttribute("aria-pressed",String(selected));
      if(selected){b.append(art(c,{tile:c.state.current_tile.tile,rotation:ui.rotation},{choices:p[3],chosen:ui.meeple,preview:true}),el("span","cc-draft","预览"));}
      b.addEventListener("focus",()=>{
        // Pointer focus must not move a target underneath the pending tap.
        if(!b.matches(":focus-visible"))return;
        // Tab through even distant late-game targets without scrolling the page.
        viewport.scrollLeft=0;viewport.scrollTop=0;
        const r=b.getBoundingClientRect(),locate=viewport.querySelector(".cc-locate")?.getBoundingClientRect();
        const covered=locate&&r.right>locate.left&&r.left<locate.right&&r.bottom>locate.top&&r.top<locate.bottom;
        const x=(p[0]+.5)*SIZE*ui.zoom+ui.panX,y=(p[1]+.5)*SIZE*ui.zoom+ui.panY;
        const margin=SIZE*ui.zoom/2+8;
        if(covered||Math.abs(x)>viewport.clientWidth/2-margin||Math.abs(y)>viewport.clientHeight/2-margin){recenter(c,{x:p[0],y:p[1]});transform(c);}
      });
      b.addEventListener("click",()=>select(c,p[0],p[1]));world.append(b);
    }
    const latest=button(c,"回到最新","latest",()=>{recenter(c);transform(c);});
    latest.className="cc-locate";latest.setAttribute("aria-label","回到最新板块");viewport.append(latest);
    mapGestures(c,viewport);
    root.append(viewport);
    const gestureHint=el("p","cc-meta cc-map-hint");
    gestureHint.append(el("span","cc-touch-hint","拖动地图 · 双指缩放"),el("span","cc-mouse-hint","拖动地图 · 滚轮缩放"));root.append(gestureHint);
    const events=c.state.last_scoring||[];
    const message=events.length?events.map(e=>`${NAMES[e.kind]} ${e.points}分 · ${e.players.map(id=>c.participants.find(p=>p.player_id===id)?.display_name||"玩家").join("、")}`).join("；"):"米色城墙 · 浅色道路 · 绿色田地；随从数字对应座位，横放的是农夫";
    const notice=el("p","cc-activity",message);notice.setAttribute("role","status");root.append(notice);
    if(c.isTerminal){const ids=c.state.result?.winning_player_ids||[],names=ids.map(id=>c.participants.find(p=>p.player_id===id)?.display_name||"玩家");root.append(el("p","cc-result",names.length?`${names.join("、")}${names.length>1?"并列获胜":"获胜"} · 已完成终局计分`:"本局结束"));}
    c.board.append(root);transform(c);
  }
  async function submit(c,keyboard=false) {
    const ui=setup(c),p=currentOption(c);if(!act(c)||!p)return;
    const move={action:"place",x:p[0],y:p[1],rotation:ui.rotation,meeple:ui.meeple};
    const panel=c.controls.querySelector(".cc-controls"),size=panel.getBoundingClientRect();
    // Keep the occupied action space across turns: shrinking it clamps scrollY
    // for players near the page bottom even when scroll anchoring is disabled.
    const view=roomView(c);view.controlsSize={width:size.width,height:size.height};
    if(keyboard)view.focusAfterRevision=c.room.revision;else delete view.focusAfterRevision;
    let submitted=false;
    ui.busy=true;ui.error="";redraw(c);
    try {submitted=await c.helpers.submitMove(move);if(!submitted)ui.error="未提交成功，请查看对局提示；刷新后可重新选择。";}
    catch(_){ui.error="连接中断，请刷新核对本回合是否已提交。";}
    finally {
      ui.busy=false;
      if(!submitted)redraw(c,"submit");
    }
  }
  function renderControls(c) {
    const ui=setup(c),panel=el("section","cc-controls");panel.setAttribute("aria-label","本回合操作");
    if(!c.isTerminal&&c.state.current_tile) {
      const row=el("div","cc-current"),preview=el("div","cc-current-tile");preview.append(art(c,{tile:c.state.current_tile.tile,rotation:ui.rotation}));
      const copy=el("div","cc-current-copy");copy.append(el("strong","",act(c)?"本回合 · 拼接一块":ui.busy?"正在提交…":"等待其他玩家拼接"));
      const edges=c.state.current_tile.edges,rotated=[0,1,2,3].map(i=>EDGE[edges[(i-ui.rotation+4)%4]]);
      copy.append(el("span","cc-meta",`北${rotated[0]} · 东${rotated[1]} · 南${rotated[2]} · 西${rotated[3]}`));
      const actions=el("div","cc-actions");
      actions.append(button(c,"旋转 ↻","rotate",()=>{
        if(!act(c))return;ui.rotation=(ui.rotation+1)%4;ui.meeple=null;
        if(!currentOption(c))ui.selected=null;redraw(c,"rotate");
      },!act(c)));
      copy.append(actions);row.append(preview,copy);panel.append(row);
      const p=currentOption(c),hint=el("p","cc-hint",p?"可选放随从，也可直接确认":act(c)?"点地图上的＋选择位置":"等待本回合完成");panel.append(hint);
      if(p) {
        const choices=el("div","cc-actions cc-regions");choices.setAttribute("role","group");choices.setAttribute("aria-label","可选随从区域");
        const no=button(c,"不放","none",()=>{if(act(c)){ui.meeple=null;redraw(c,"none");}},!act(c));no.className="cc-skip";no.setAttribute("aria-label","不放随从");no.setAttribute("aria-pressed",String(ui.meeple===null));
        p[3].forEach((rid,i)=>{const region=c.state.current_tile.regions[rid],b=button(c,`${i+1} · ${label(rid,region)}`,`region-${rid}`,()=>{if(act(c)){ui.meeple=ui.meeple===rid?null:rid;redraw(c,`region-${rid}`);}},!act(c));b.setAttribute("aria-pressed",String(ui.meeple===rid));choices.append(b);});
        panel.append(choices);
        if(ui.meeple?.startsWith("f"))panel.append(el("p","cc-meta","农夫留在田地，直到终局才计分、回收。"));
        if(!p[3].length)panel.append(el("p","cc-meta","此落点没有可放随从的区域；可直接确认拼接。"));
        const confirm=el("div","cc-confirm");
        const summary=el("span","cc-meta",ui.meeple?`随从：${label(ui.meeple,c.state.current_tile.regions[ui.meeple])}`:"本次不放随从");
        summary.setAttribute("role","status");confirm.append(no,summary,button(c,ui.busy?"提交中…":"确认放置","submit",event=>submit(c,event.detail===0),!act(c),true));panel.append(confirm);
      }
    }
    if(ui.error){const e=el("p","cc-error",ui.error);e.setAttribute("role","alert");panel.append(e);}
    const size=roomView(c).controlsSize;
    if(size&&Math.abs(size.width-c.controls.clientWidth)<1)panel.style.minHeight=`${size.height}px`;
    if(panel.childNodes.length)c.controls.append(panel);
    // Polling can render the new turn before submitMove resolves. Focus only
    // after the controls are restored: focusing mid-render can clamp page scroll.
    const view=roomView(c);
    if(view.focusAfterRevision!==undefined&&view.focusAfterRevision!==c.room.revision){
      delete view.focusAfterRevision;
      if(document.activeElement===document.body)c.board.querySelector(".cc-viewport")?.focus({preventScroll:true});
    }
  }
  window.DuelGameUI.register("carcassonne",{participantPresentation:"generic",usesStandardMoveConfirmation:false,ownsPrivateStatePresentation:true,renderBoard,renderControls});
}());
