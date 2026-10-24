/* Local UI-only projections. No live requests or game moves. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=process.env.FOUR_COLOR_ARTIFACTS;
assert.ok(out && process.env.FOUR_COLOR_PROJECTION,'explicit private local projection and artifact paths required');
const fixture=JSON.parse(fs.readFileSync(process.env.FOUR_COLOR_PROJECTION,'utf8'));
const original=JSON.stringify(fixture);fs.mkdirSync(out,{recursive:true});
(async()=>{let browser;const results=[];try{
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 for(const width of (process.env.FOUR_COLOR_WIDTHS||'360,430,1280').split(',').map(Number)){
 const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<500,isMobile:width<500});const errors=[],posts=[];
 page.on('pageerror',e=>errors.push(e.message));
 let served=fixture;
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.hostname!=='four-color.test')return route.abort();
  if(p==='/'||p.startsWith('/static/')){const file=path.join(root,'app/static',p==='/'?'index.html':p.slice(8));return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html'});}
  if(req.method()==='POST' && p.startsWith('/api/rooms'))posts.push(p);
  let data={ok:true,notifications:[],rooms:[],timeline:[]};
  if(p==='/api/whoami')data={...data,bound:true,human_player_id:fixture.room.viewer.player_id,human_name:'玩家',identity_label:'玩家',machines:[],wallet:{balance:0},games:[{game_type:'four_color_qunhu',display_name:'四色牌·群胡',category:'card',allowed_player_counts:[3,4,5],recommended_players:5,supports_stakes:false}]};
  else if(p===`/api/rooms/${fixture.room.room_id}`)data=served;
  return route.fulfill({json:data});
 });
 await page.goto(`http://four-color.test/?room=${fixture.room.room_id}`);await page.locator('.fc-table[data-seats="5"]').waitFor();
 assert.equal(await page.locator('.fc-tutorial').count(),0);
 const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
 if(process.env.FOUR_COLOR_CSS) await page.addStyleTag({content:fs.readFileSync(process.env.FOUR_COLOR_CSS,'utf8')});
 if(process.env.FOUR_COLOR_BASELINE_CSS){
  const signature=()=>page.locator('.fc-seat-identity,.room-participant-avatar,.fc-hand,.fc-hand .fc-card').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect();return [n.className,r.x,r.y,r.width,r.height];}));
  const after=await signature();
  await page.locator('#four-color-styles').evaluate(n=>{n.disabled=true;});
  const baseline=await page.addStyleTag({content:fs.readFileSync(process.env.FOUR_COLOR_BASELINE_CSS,'utf8')});
  assert.deepEqual(await signature(),after,'avatar positions, hand layout and card dimensions unchanged');
  await baseline.evaluate(n=>n.remove());
  await page.locator('#four-color-styles').evaluate(n=>{n.disabled=false;});
 }
 const stateBefore=await page.evaluate(()=>JSON.stringify(room));
 await page.screenshot({path:path.join(out,`${width}-initial.png`),fullPage:true});
 const inspect=()=>page.locator('.fc-table').evaluate(table=>{
  const rect=n=>{let r=n.getBoundingClientRect();
   if(n.matches('.fc-seat-name, .fc-turn')) {const range=document.createRange();range.selectNodeContents(n);const text=range.getBoundingClientRect();r={left:Math.max(r.left,text.left),right:Math.min(r.right,text.right),top:Math.max(r.top,text.top),bottom:Math.min(r.bottom,text.bottom)};}
return [[r.left,r.top],[r.right,r.top],[r.right,r.bottom],[r.left,r.bottom]];};
  const overlap=(a,b)=>{
   for(const q of [a,b])for(let i=0;i<q.length;i++){
    const p=q[i],n=q[(i+1)%q.length],axis=[p[1]-n[1],n[0]-p[0]],len=Math.hypot(...axis);
    const proj=s=>s.map(v=>(v[0]*axis[0]+v[1]*axis[1])/len),u=proj(a),v=proj(b);
    if(Math.min(Math.max(...u),Math.max(...v))-Math.max(Math.min(...u),Math.min(...v))<.5)return false;
   }return true;
  };
  const obstacles=[...table.querySelectorAll('.room-participant-avatar,.fc-seat-name,.fc-seat-count,.fc-dealer,.fc-turn-indicator,.fc-stock,.fc-turn,.fc-offer,.fc-offer > .fc-meta,.fc-seat-public,.fc-neutral-pile')].map(n=>({name:n.className,points:rect(n)}));
  const rails=[...table.querySelectorAll('.fc-history')].map(n=>{
   const r=n.getBoundingClientRect(),m=new DOMMatrix(getComputedStyle(n).transform),cx=r.x+r.width/2,cy=r.y+r.height/2;
   const world=(x,y)=>[cx+m.a*(x-n.clientWidth/2)+m.c*(y-n.clientHeight/2),cy+m.b*(x-n.clientWidth/2)+m.d*(y-n.clientHeight/2)];
   const cards=[...n.children].map(c=>{
    const shift=new DOMMatrix(getComputedStyle(c).transform).m41;
    const x=c.offsetLeft+shift-n.scrollLeft,y=c.offsetTop-n.scrollTop,l=Math.max(0,x),t=Math.max(0,y),right=Math.min(n.clientWidth,x+c.offsetWidth),b=Math.min(n.clientHeight,y+c.offsetHeight);
    const h=c.querySelector('strong'),hr=h.getBoundingClientRect(),dx=hr.x+hr.width/2-cx,dy=hr.y+hr.height/2-cy;
    const hx=m.a*dx+m.b*dy+n.clientWidth/2,hy=m.c*dx+m.d*dy+n.clientHeight/2;
    const inset=0;
    const readable=hx>=inset+6.9&&hx<=n.clientWidth-inset-6.9&&hy>=6.9&&hy<=n.clientHeight-6.9&&c.contains(document.elementFromPoint(hr.x+hr.width/2,hr.y+hr.height/2));
    const pieces=[[0,0,n.clientWidth,n.clientHeight]].map(([cl,ct,cr,cb])=>{
     const pl=Math.max(l,cl),pt=Math.max(t,ct),pr=Math.min(right,cr),pb=Math.min(b,cb);
     return pr>pl&&pb>pt?[[pl,pt],[pr,pt],[pr,pb],[pl,pb]].map(([x,y])=>world(x,y)):[];
    }).filter(p=>p.length);
    return {hx,hy,x,y,w:c.offsetWidth,h:c.offsetHeight,readable,visible:pieces.some(p=>Math.hypot(p[1][0]-p[0][0],p[1][1]-p[0][1])>=2),pieces};
   });
   return {position:n.dataset.position,width:n.clientWidth,height:n.clientHeight,scrollWidth:n.scrollWidth,left:n.scrollLeft,readable:cards.filter(c=>c.readable).length,visible:cards.filter(c=>c.visible).length,cards};
  });
  const bad=[];
  const center=obstacles.filter(n=>['fc-stock','fc-turn','fc-turn fc-meta','fc-offer','fc-meta'].includes(n.name));
  center.forEach((a,i)=>center.slice(i+1).forEach(b=>{if(overlap(a.points,b.points))bad.push([a.name,b.name]);}));
  rails.forEach((a,i)=>a.cards.forEach(c=>c.pieces.forEach(points=>{
   obstacles.forEach(b=>{if(overlap(points,b.points))bad.push([a.position,b.name]);});
   rails.slice(i+1).forEach(b=>b.cards.forEach(d=>d.pieces.forEach(other=>{if(overlap(points,other))bad.push([a.position,b.position]);})));
  })));
  return {rails,bad,obstacles};
 });
 const geometry=await inspect();

 fs.writeFileSync(path.join(out,`${width}-initial-geometry.json`),JSON.stringify(geometry,null,2));
 assert.deepEqual(geometry.bad,[],`initial collisions at ${width}`);
 geometry.rails.forEach(r=>{
  assert.equal(r.height,21,'original single-row scrollport');
  assert.ok(r.readable>=3,'single-row heads remain readable');
  assert.ok(r.scrollWidth>r.width,'native history overflow remains scrollable');
  r.cards.forEach((c,i)=>{assert.equal(c.y,r.cards[0].y,'all cards share one row');if(i)assert.equal(c.x-r.cards[i-1].x,20,'original overlap pitch');});
 });
 assert.ok(await page.locator('.fc-history').evaluateAll(ns=>ns.every(n=>getComputedStyle(n).display==='flex' && getComputedStyle(n).clipPath==='none')),'no double-row grid or mask');
 const samples=[{scene:'initial',...geometry}];
 // The same public/listening pressure as ringOnly, only in a disposable projection.
 const pressure=structuredClone(fixture);
 pressure.room.revision++;
 const state=pressure.room.board_state,order=state.participant_order;
 order.forEach((pid,i)=>{
  state.melds[pid]=[0,1].map(g=>({cards:[0,1].map(c=>`${'RYGW'[i%4]}${g}-pressure-${i}-${g}-${c}`)}));
  state.listening[pid]=Array.from({length:i===1||i===4?4:2},(_,j)=>`${'RYGW'[i%4]}${j}-listen-${i}-${j}`);
 });
 const offer=structuredClone(pressure);offer.room.revision++;
 offer.room.board_state.offer={card:'Y6-offer',source:order[1],from_deck:false};
 offer.room.board_state.discards.push('R1-neutral','G2-neutral','W3-neutral');
 for(const [scene,projection] of [['actual',fixture],['pressure',pressure],['offer',offer]]){
  served=projection;
  // Reload cancels requests belonging to the previous disposable scene.
  await page.reload();
  await page.waitForFunction(rev=>window.room?.revision===rev || (typeof room!=='undefined' && room?.revision===rev),projection.room.revision);
  await page.locator('.fc-table[data-seats="5"]').waitFor();
  if(await dismiss.isVisible()) await dismiss.click();
  if(scene!=='actual')assert.equal(await page.locator('.fc-seat-public .fc-meld').count(),10,'pressure must actually render');
  if(scene==='offer')assert.equal(await page.locator('.fc-offer').count(),1);
  if(scene==='actual' && width<500){
   const cdp=await page.context().newCDPSession(page);
   for(const port of await page.locator('.fc-history').all()){
    await port.evaluate(n=>{n.scrollLeft=0;});
    const gesture=await port.evaluate(n=>{
     const r=n.getBoundingClientRect(),m=new DOMMatrix(getComputedStyle(n).transform);
     const world=(x,y)=>({x:r.x+r.width/2+m.a*(x-n.clientWidth/2)+m.c*(y-n.clientHeight/2),y:r.y+r.height/2+m.b*(x-n.clientWidth/2)+m.d*(y-n.clientHeight/2)});
     const start=world(n.clientWidth/2,10);
     return {start,end:{x:start.x-60*Math.sign(m.a),y:start.y},pageY:scrollY,position:n.dataset.position,overflow:n.scrollWidth-n.clientWidth,hit:n.contains(document.elementFromPoint(start.x,start.y))};
    });
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[gesture.start]});
    for(let i=1;i<=10;i++){
     await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:gesture.start.x+(gesture.end.x-gesture.start.x)*i/10,y:gesture.start.y+(gesture.end.y-gesture.start.y)*i/10}]});
     await page.waitForTimeout(20);
    }
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await page.waitForTimeout(180);
    assert.ok(await port.evaluate(n=>n.scrollLeft>5),JSON.stringify({message:'touch must scroll',...gesture,after:await port.evaluate(n=>n.scrollLeft)}));
    assert.equal(await page.evaluate(()=>scrollY),gesture.pageY,'page must not consume the swipe');
   }
   await cdp.detach();
  }
  await page.screenshot({path:path.join(out,`${width}-${scene}-before-scroll.png`),fullPage:true});
  const unreachable=await page.locator('.fc-history').evaluateAll(async ports=>{
   const bad=[];
   for(const port of ports)for(const slot of port.querySelectorAll('.fc-history-card')){
    if(!port.isConnected || !port.clientWidth) throw new Error('Measured port detached or hidden during scene');
    const inset=0,shift=new DOMMatrix(getComputedStyle(slot).transform).m41;
    const x=slot.offsetLeft+shift;
    port.scrollLeft=Math.max(0,x-inset-(port.clientWidth-2*inset-slot.offsetWidth)/2);
    await new Promise(requestAnimationFrame);
    const left=x-port.scrollLeft;
    if(left<inset-.5||left+slot.offsetWidth>port.clientWidth-inset+.5)bad.push({position:port.dataset.position,index:[...port.children].indexOf(slot),left,width:port.clientWidth,scrollWidth:port.scrollWidth,inset});
    const head=slot.querySelector('strong'),r=head.getBoundingClientRect();
    if(!slot.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)))bad.push({position:port.dataset.position,index:[...port.children].indexOf(slot),hit:false,occluder:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,200)});
   }
   return bad;
  });
  assert.deepEqual(unreachable,[],`${scene}: every card can be scrolled within its row and its head read`);
  for(const end of [0,1]){
   await page.locator('.fc-history').evaluateAll((ns,end)=>ns.forEach(n=>{n.scrollLeft=end*n.scrollWidth;n.scrollTop=0;}),end);
   await page.waitForTimeout(50);
   const measured=await inspect();
   fs.writeFileSync(path.join(out,`${width}-${scene}-${end}-geometry.json`),JSON.stringify(measured));
   samples.push({scene:`${scene}-${end?'end':'start'}`,...measured});
   assert.deepEqual(measured.bad,[],`${scene}/${end} collisions at ${width}`);
   await page.screenshot({path:path.join(out,`${width}-${scene}-${end?'end':'start'}.png`),fullPage:true});
  }
 }
 results.push({width,samples});
 served=fixture;
 await page.evaluate(f=>renderGame(f.room,'',f.timeline),fixture);
 assert.equal(await page.evaluate(()=>JSON.stringify(room)),stateBefore);assert.equal(JSON.stringify(fixture),original);assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
 await page.close();console.log('captured',width);
 }
 fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(results,null,2));
}finally{await browser?.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
