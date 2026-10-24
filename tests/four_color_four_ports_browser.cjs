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
 const four=structuredClone(fixture);four.room.revision+=10;
 const state=four.room.board_state;
 state.participant_order=state.participant_order.slice(0,4);
 four.room.participants=four.room.participants.filter(p=>state.participant_order.includes(p.player_id));
 state.discards=[];state.discard_sources={};state.offer=null;state.broken={};
 for(const [i,pid] of state.participant_order.entries()){
  state.melds[pid]=[0,1].map(g=>({cards:Array.from({length:4},(_,c)=>`${'RYGW'[i]}${g}-four-${c}`)}));
  state.listening[pid]=[];state.broken[pid]=[];
  for(let c=0;c<8;c++){const id=`${'RYGW'[i]}${c%7}-discard-${c}`;state.discards.push(id);state.discard_sources[id]=pid;}
 }
 for(let c=0;c<12;c++){const id=`R${c%7}-neutral-${c}`;state.discards.push(id);state.discard_sources[id]=null;}
 served=four;await page.evaluate(f=>renderGame(f.room,'',f.timeline),four);
 if(process.env.FOUR_COLOR_BASELINE_CSS){
  const signature=()=>page.locator('.fc-seat-identity,.room-participant-avatar,.fc-hand,.fc-hand .fc-card').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect();return [n.className,r.x,r.y,r.width,r.height];}));
  const after=await signature();
  await page.locator('#four-color-styles').evaluate(n=>{n.disabled=true;});
  const baseline=await page.addStyleTag({content:fs.readFileSync(process.env.FOUR_COLOR_BASELINE_CSS,'utf8')});
  assert.deepEqual(await signature(),after,'avatar positions, hand layout and card dimensions unchanged');
  await baseline.evaluate(n=>n.remove());
  await page.locator('#four-color-styles').evaluate(n=>{n.disabled=false;});
 }
 const measure=()=>page.locator('.fc-table').evaluate(t=>{
  const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
  return Object.fromEntries(['.fc-position-west .fc-seat-public','.fc-position-east .fc-seat-public','.fc-neutral-pile','.fc-table-center'].map(q=>[q,rect(t.querySelector(q))]));
 });
 const metrics=await measure();console.log(width,JSON.stringify(metrics));
 await page.screenshot({path:path.join(out,`${width}-four-initial.png`),fullPage:true});
 const west=metrics['.fc-position-west .fc-seat-public'],east=metrics['.fc-position-east .fc-seat-public'],neutral=metrics['.fc-neutral-pile'];
 assert.equal(west.width,east.width,'left and right public ports have equal visible width');
 assert.ok(neutral.x>=west.right && neutral.right<=east.x,'neutral history stays between public ports');
 const neutralPort=page.locator('.fc-neutral-pile .fc-discard-history');
 assert.ok(await neutralPort.evaluate(n=>n.scrollWidth>n.clientWidth));
 await neutralPort.evaluate(n=>{n.scrollLeft=n.scrollWidth;});
 assert.ok(await neutralPort.evaluate(n=>n.scrollLeft>0));
 await page.screenshot({path:path.join(out,`${width}-four-end.png`),fullPage:true});
 results.push({width,metrics});
 assert.equal(await page.locator('.fc-tutorial').count(),0);
 assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);assert.equal(JSON.stringify(fixture),original);
 await page.close();
 }
 fs.writeFileSync(path.join(out,'four-measurements.json'),JSON.stringify(results,null,2));
}finally{await browser?.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
