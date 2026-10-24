/* Local UI-only projections. No live requests or game moves. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),out=process.env.FOUR_COLOR_ARTIFACTS;
assert.ok(out && process.env.FOUR_COLOR_NEUTRAL_FIXTURES && process.env.FOUR_COLOR_BASELINE_CSS);
fs.mkdirSync(out,{recursive:true});
const collisionScript=fs.readFileSync(path.join(root,'tests/check_four_color_browser.js'),'utf8');
const collisionStart=collisionScript.indexOf('const collisions=await');
const collisionBody=collisionScript.slice(collisionScript.indexOf('table=>{',collisionStart),collisionScript.indexOf('\n          });',collisionStart))+'}';
const collisionProbe=(0,eval)('('+collisionBody+')');
(async()=>{let browser;const results=[];try{
 browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 for(const file of process.env.FOUR_COLOR_NEUTRAL_FIXTURES.split(',')) for(const width of (process.env.FOUR_COLOR_WIDTHS||'360,430,1280').split(',').map(Number)){
 const source=JSON.parse(fs.readFileSync(file,'utf8')),fixture=structuredClone(source.fixture || source);
 const seats=fixture.room.board_state.participant_order.length;
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

 assert.equal(seats,5);
 assert.equal(await page.locator('.fc-seat-public').count(),5,'all five public areas render');

 assert.ok(await page.locator('.fc-history').evaluateAll(ns=>ns.every(n=>getComputedStyle(n).display==='flex' && n.clientHeight===21 && getComputedStyle(n).clipPath==='none')),'single row retained');
 assert.deepEqual(await page.locator('.fc-table').evaluate(collisionProbe),[]);
 const geometry=await page.locator('.fc-table').evaluate(table=>{
  const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom};};
  const paint=port=>[...port.querySelectorAll('.fc-card,.fc-meta')].map(n=>{const r=rect(n),p=rect(port);return Math.min(r.b,p.b)});
  const top=[...table.querySelectorAll('.fc-position-north-west .fc-seat-public,.fc-position-north-east .fc-seat-public')];
  const side=table.querySelector('.fc-position-west .fc-seat-identity');
  const south=table.querySelector('.fc-position-south .fc-seat-public');
  const ring=[...table.querySelectorAll('.fc-history')].map(rect);
  return {table:rect(table),upperGap:rect(side).y-Math.max(...top.flatMap(paint)),lowerGap:rect(south).y-Math.max(...ring.map(r=>r.b)),public:[...table.querySelectorAll('.fc-seat-public')].map(p=>({position:p.closest('.fc-seat').dataset.position,...rect(p),cw:p.clientWidth,sw:p.scrollWidth,ch:p.clientHeight,sh:p.scrollHeight}))};
 });
 console.log(JSON.stringify({width,revision:fixture.room.revision,geometry}));
 await page.screenshot({path:path.join(out,`H3-${width}.png`),fullPage:true});
 assert.ok(geometry.upperGap>=8,'upper cards clear side identities');
 assert.ok(geometry.lowerGap>=8,'pentagon clears south public cards');
 // Compare the entire translation against production: no changes to face sizes,
 // horizontal seat geometry, or public scrollport dimensions.
 const measure=()=>page.locator('.fc-table,.fc-seat-identity,.fc-seat-public,.fc-card').evaluateAll(ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,cls:n.className,side:!!n.closest('.fc-position-west,.fc-position-east'),south:!!n.closest('.fc-position-south'),inTable:!!n.closest('.fc-table')};}));
 const after=await measure(),neutral=await page.locator('.fc-neutral-pile').count();
 await page.locator('#four-color-styles').evaluate(n=>n.disabled=true);
 const baseline=await page.addStyleTag({content:fs.readFileSync(process.env.FOUR_COLOR_BASELINE_CSS,'utf8')});
 const before=await measure();
 await baseline.evaluate(n=>n.remove());await page.locator('#four-color-styles').evaluate(n=>n.disabled=false);
 assert.equal(before[0].h-after[0].h,neutral?0:40);
 for(let i=1;i<after.length;i++){
  for(const k of ['x','w','h'])assert.ok(Math.abs(after[i][k]-before[i][k])<.01,'horizontal positions and face sizes unchanged');
  if(after[i].inTable && (after[i].side||after[i].south))assert.ok(Math.abs(before[i].y-after[i].y-(neutral?0:40))<.01);
 }
 // Test both rail ends and public-area extremes, retaining painted collisions.
 for(const end of [false,true]){
  await page.locator('.fc-discard-history,.fc-seat-public').evaluateAll((ns,end)=>ns.forEach(n=>{n.scrollLeft=end?n.scrollWidth:0;n.scrollTop=end?n.scrollHeight:0;}),end);
  assert.deepEqual(await page.locator('.fc-table').evaluate(collisionProbe),[]);
 }
 assert.equal(await page.evaluate(()=>JSON.stringify(room)),stateBefore);assert.equal(JSON.stringify(fixture),original);assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
 // Private UI fixtures only: exercise the real renderer without submitting moves.
 if(process.env.FOUR_COLOR_BUTTON_CASES){
  const held=fixture.room.private_state.hand[0].id;
  for(const [name,actions] of Object.entries({draw:{draw:'翻牌'},take:{take:'取上家弃牌',pass:'不取，翻牌'},discard:{['discard:'+held]:'打出', ['listen:'+held]:'打出并听牌'}})){
   served=structuredClone(fixture);served.room.private_state.legal_actions=Object.keys(actions).map(action_id=>({action:'act',action_id}));served.room.private_state.action_labels=actions;
   await page.reload();await page.locator('.fc-table').waitFor();
   const dismiss=page.locator('#dismissWaitModeModalButton');if(await dismiss.isVisible())await dismiss.click();
   if(name==='discard'){
    assert.ok(await page.locator('.fc-controls button').isDisabled());
    const disabled=await page.locator('.fc-controls button').evaluate(n=>({opacity:getComputedStyle(n).opacity,bg:getComputedStyle(n).backgroundColor}));
    assert.equal(disabled.opacity,'0.45');
    await page.screenshot({path:path.join(out,`disabled-${width}.png`),fullPage:true});
    await page.locator(`.fc-hand [data-card-id="${held}"]`).click();
   }
   const buttons=await page.locator('.fc-controls button').evaluateAll(ns=>ns.map(n=>{const s=getComputedStyle(n),r=n.getBoundingClientRect();return {text:n.textContent,disabled:n.disabled,size:s.fontSize,weight:s.fontWeight,color:s.color,bg:s.backgroundColor,opacity:s.opacity,x:r.x,y:r.y,w:r.width,h:r.height,sw:n.scrollWidth,cw:n.clientWidth};}));
   assert.equal(buttons.length,name==='draw'?1:2);
   for(const b of buttons){assert.ok(!b.disabled);assert.equal(b.size,'16px');assert.equal(b.weight,'600');assert.equal(b.opacity,'1');assert.equal(b.color,'rgb(92, 84, 99)');assert.equal(b.bg,'rgb(230, 223, 240)');assert.ok(b.h>=44&&b.h<=48);assert.ok(b.x>=0&&b.x+b.w<=width);assert.ok(b.sw<=b.cw);}
   if(buttons.length===2)assert.equal(buttons[0].y,buttons[1].y,'two actions fit on one row');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:true});
   results.push({width,name,buttons});
   assert.equal(await page.evaluate(()=>JSON.stringify(room)),JSON.stringify(served.room),'selection does not mutate room state');
  }
 }
 assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);
 await page.close();console.log('PASS five compact',width);
 }
fs.writeFileSync(path.join(out,'buttons.json'),JSON.stringify(results,null,2));
}finally{await browser?.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
