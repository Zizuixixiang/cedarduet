/* Local UI-only projections. No live requests or game moves. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=process.env.FOUR_COLOR_ARTIFACTS;
assert.ok(out && process.env.FOUR_COLOR_NEUTRAL_FIXTURES && process.env.FOUR_COLOR_BASELINE_CSS);
fs.mkdirSync(out,{recursive:true});
(async()=>{let browser;const results=[];try{
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 for(const file of process.env.FOUR_COLOR_NEUTRAL_FIXTURES.split(',')) for(const width of (process.env.FOUR_COLOR_WIDTHS||'360,430,1280').split(',').map(Number)){
 const source=JSON.parse(fs.readFileSync(file,'utf8')),fixture=structuredClone(source);
 const seats=fixture.room.board_state.participant_order.length;
 // Legacy projection only: existing cards with missing provenance, no new cards/melds.
 if(seats===5){const state=fixture.room.board_state;const id=state.discards.find(id=>id!==state.offer?.card);delete state.discard_sources[id];}
 const original=JSON.stringify(fixture);
 const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<500,isMobile:width<500});const errors=[],posts=[];
 page.on('pageerror',e=>errors.push(e.message));
 let served=fixture;
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.hostname!=='four-color.test')return route.abort();
  if(p==='/'||p.startsWith('/static/')){const file=path.join(root,'app/static',p==='/'?'index.html':p.slice(8));return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html'});}
  if(req.method()==='POST' && p.startsWith('/api/rooms'))posts.push(p);
  let data={ok:true,notifications:[],rooms:[],timeline:[]};
  if(p==='/api/whoami')data={...data,bound:true,human_player_id:fixture.room.viewer.player_id,human_name:'玩家',identity_label:'玩家',machines:[],wallet:{balance:0},games:[{game_type:fixture.room.game_type,display_name:'四色牌·群胡',category:'card',allowed_player_counts:[3,4,5],recommended_players:5,supports_stakes:false}]};
  else if(p===`/api/rooms/${fixture.room.room_id}`)data=served;
  return route.fulfill({json:data});
 });
 await page.goto(`http://four-color.test/?room=${fixture.room.room_id}`);await page.locator('.fc-table').waitFor();
 assert.equal(await page.locator('.fc-tutorial').count(),0);
 const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
 if(process.env.FOUR_COLOR_CSS) await page.addStyleTag({content:fs.readFileSync(process.env.FOUR_COLOR_CSS,'utf8')});
 const stateBefore=await page.evaluate(()=>JSON.stringify(room));
 const label=page.locator('.fc-neutral-label');
 assert.equal(await label.count(),1);assert.equal(await label.innerText(),'公共弃牌');
 assert.equal(await page.locator('.fc-history .fc-neutral-label').count(),0);
 const rails=()=>page.locator('.fc-history,.fc-neutral-pile .fc-discard-history').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect();return [n.dataset.historyKey,r.x,r.y,r.width,r.height];}));
 const after=await rails();
 await page.locator('#four-color-styles').evaluate(n=>n.disabled=true);
 const oldStyle=await page.addStyleTag({content:fs.readFileSync(process.env.FOUR_COLOR_BASELINE_CSS,'utf8')+'\n.fc-neutral-label { display: none; }'});
 assert.deepEqual(await rails(),after,'all history coordinates unchanged from single-row patch');
 await oldStyle.evaluate(n=>n.remove());await page.locator('#four-color-styles').evaluate(n=>n.disabled=false);
 const checkLabel=()=>label.evaluate(label=>{
  const box=n=>{const r=n.getBoundingClientRect();return {l:r.left,t:r.top,r:r.right,b:r.bottom};};
  const a=box(label),bad=[];
  const overlap=b=>Math.min(a.r,b.r)-Math.max(a.l,b.l)>.5 && Math.min(a.b,b.b)-Math.max(a.t,b.t)>.5;
  for(const n of document.querySelectorAll('.fc-table .fc-card,.fc-stock,.fc-turn,.fc-seat-identity,.fc-offer > .fc-meta')){
   let r=box(n);const clip=n.closest('.fc-seat-public,.fc-discard-history');
   if(clip){const c=box(clip);r={l:Math.max(r.l,c.l),t:Math.max(r.t,c.t),r:Math.min(r.r,c.r),b:Math.min(r.b,c.b)};}
   if(r.r>r.l && r.b>r.t && overlap(r))bad.push({class:n.className,box:r});
  }
  return {label:a,bad,font:getComputedStyle(label).fontSize};
 });
 for(const end of [false,true]){
  await page.locator('.fc-discard-history').evaluateAll((ns,end)=>ns.forEach(n=>{n.scrollLeft=end?n.scrollWidth:0;n.scrollTop=end?n.scrollHeight:0;}),end);
  const measured=await checkLabel();assert.deepEqual(measured.bad,[],'public label must not overlap visible cards or identities/hints');assert.equal(measured.font,'11px');
  results.push({seats,width,end,...measured});
  await page.screenshot({path:path.join(out,`${seats}-${width}-${end?'end':'start'}.png`),fullPage:true});
 }
 assert.equal(await page.evaluate(()=>JSON.stringify(room)),stateBefore);assert.equal(JSON.stringify(fixture),original);assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
 // Removing the label's only source removes the entire neutral group.
 const empty=structuredClone(fixture);empty.room.revision++;
 const state=empty.room.board_state;state.discards=state.discards.filter(id=>state.participant_order.includes(state.discard_sources[id]));
 served=empty;await page.reload();await page.locator('.fc-table').waitFor();
 assert.equal(await page.locator('.fc-neutral-label,.fc-neutral-pile').count(),0);
 await page.close();console.log('PASS neutral label',seats,width);
 }
 fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(results,null,2));
}finally{await browser?.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
