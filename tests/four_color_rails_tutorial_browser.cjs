/* Actual page and viewer projections; SQLite is temporary, every request intercepted.
 * Dense overflow projections are explicitly visual-only, never engine inputs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const out = process.env.FOUR_COLOR_ARTIFACTS || '/tmp/four-color-rails-tutorial-evidence';
fs.mkdirSync(out, {recursive:true});
const temp = fs.mkdtempSync('/tmp/four-color-tutorial-db-');
const key = 'cedarduet.four_color.tutorial.v1';
let sequence = 0, db;
function backend(request) {
  if(request.action === 'seed') db = path.join(temp, `${++sequence}.db`);
  const r = spawnSync(process.env.FOUR_COLOR_PYTHON || path.join(root,'.venv/bin/python'), [path.join(__dirname,'four_color_ui_fixture.py')], {
    cwd:root, env:{...process.env,PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1',DUEL_DB_PATH:db},
    input:JSON.stringify(request),encoding:'utf8',timeout:45000,
  });
  assert.equal(r.status,0,r.stderr); const data=JSON.parse(r.stdout); assert.equal(data.ok,true,JSON.stringify(data)); return data;
}
async function swipe(page, selector, axis) {
  const n = page.locator(selector).first(); await n.scrollIntoViewIfNeeded();
  await n.evaluate(n=>{n.scrollTop=0;n.scrollLeft=0;});
  const r=await n.boundingBox(), cdp=await page.context().newCDPSession(page);
  const x=r.x+(axis==='x'?r.width-15:Math.min(25,r.width/2)), y=r.y+(axis==='y'?r.height-12:Math.min(25,r.height/2));
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(let i=1;i<=8;i++) {
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-(axis==='x'?i*12:0),y:y-(axis==='y'?i*10:0)}]});
    await page.waitForTimeout(20);
  }
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForTimeout(180);
  assert.ok(await n.evaluate((n,a)=>a==='x'?n.scrollLeft>0:n.scrollTop>0,axis),`${selector}: real touch must scroll ${axis}`);
  await cdp.detach();
}
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']}); const measurements=[];
  try {
    for(const [mode,count] of [['tenhu',4],['qunhu',5]]) for(const width of [360,430,1280]) {
      const page=await browser.newPage({viewport:{width,height:900},hasTouch:true});
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      let fixture=backend({action:'seed',game_type:`four_color_${mode}`,count,invited:true,fixture:'waiting'});
      const waiting=JSON.stringify(fixture); const posts=[];
      await page.route('**/*',async route=>{
        const req=route.request(), u=new URL(req.url()), p=u.pathname;
        if(u.hostname!=='four-color.test') return route.abort();
        if(p==='/' || p.startsWith('/static/')) {
          const rel=p==='/'?'index.html':p.slice(8),file=path.join(root,'app/static',rel);
          return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
        }
        if(req.method()==='POST')posts.push(p);
        let data={ok:true,rooms:[],timeline:[],notifications:[]};
        if(p==='/api/whoami')data={...data,bound:true,human_player_id:'p0',human_name:'玩家0',identity_label:'玩家0',machines:[],wallet:{balance:0},games:['tenhu','qunhu'].map(m=>({game_type:`four_color_${m}`,display_name:`四色牌·${m}`,category:'card',allowed_player_counts:m==='tenhu'?[2,3,4]:[3,4,5],recommended_players:4,supports_stakes:false}))};
        else if(p===`/api/rooms/${fixture.room.room_id}`)data=fixture;
        else if(p.endsWith('/start')) {fixture=backend({action:'start',room_id:fixture.room.room_id});data=fixture;}
        return route.fulfill({json:data});
      });
      const shot=async label=>page.screenshot({path:path.join(out,`${mode}-${width}-${label}.png`),fullPage:true});
      const reload=async()=>{await page.reload();await page.locator('#inviteRoomPanel:not(.hidden)').waitFor();};
      await page.goto(`http://four-color.test/?room=${fixture.room.room_id}`);
      await page.locator('.fc-tutorial[open]').waitFor();
      assert.equal(await page.locator('#board .fc-card').count(),0,'waiting has no real cards');
      await shot('welcome');
      const panelBefore=await page.locator('#inviteRoomPanel').boundingBox();
      await page.getByRole('button',{name:'查看教程',exact:true}).click();
      await swipe(page,'.fc-tutorial-board [data-tutorial-target]','x');
      await shot('horizontal');
      const port=page.locator('.fc-tutorial-board [data-tutorial-target]').first();
      await port.evaluate(n=>n.scrollLeft=0); await port.focus(); await page.keyboard.press('ArrowRight');
      await page.waitForFunction(()=>document.querySelector('.fc-tutorial-board [data-tutorial-target]').scrollLeft>0);
      await page.getByRole('button',{name:'下一步',exact:true}).focus(); await page.keyboard.press('Tab');
      assert.ok(await page.evaluate(() => !!document.activeElement.closest('.fc-tutorial')));

      await page.getByRole('button',{name:'下一步',exact:true}).click();
      await swipe(page,'.fc-tutorial-board [data-tutorial-target]','y');
      await shot('vertical');
      await page.getByRole('button',{name:'上一步',exact:true}).click();
      assert.equal(await page.locator('.fc-tutorial').getAttribute('data-step'),'1');
      assert.deepEqual(await page.locator('#inviteRoomPanel').boundingBox(),panelBefore,'overlay never shifts waiting panel');
      await page.getByRole('button',{name:'关闭本次教程',exact:true}).click();
      assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
      assert.equal(await page.locator('.fc-tutorial-board').count(),0);
      assert.equal(JSON.stringify(fixture),waiting,'demo must not change room projection');
      assert.deepEqual(posts,[],'practice does not submit anything');
      await reload();await page.locator('.fc-tutorial[open]').waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.fc-tutorial').count(),0);
      await reload();await page.getByRole('button',{name:'不再提示',exact:true}).click();
      assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),'dismissed');
      await reload();assert.equal(await page.locator('.fc-tutorial').count(),0);
      await page.evaluate(k=>localStorage.removeItem(k),key);await reload();
      await page.getByRole('button',{name:'查看教程',exact:true}).click();
      for (let i = 0; i < 2; i++) await page.getByRole('button',{name:'下一步',exact:true}).click();
      await page.getByRole('button',{name:'完成',exact:true}).click();
      assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),'completed');
      await reload();assert.equal(await page.locator('.fc-tutorial').count(),0);
      await shot('waiting-dismissed');
      await page.evaluate(k=>localStorage.removeItem(k),key);await reload();
      await page.getByRole('button',{name:'查看教程',exact:true}).click();
      // Server-driven start while the learner's modal is still open.
      fixture=backend({action:'start',room_id:fixture.room.room_id});
      await page.evaluate(f=>renderGame(f.room,"",f.timeline),fixture);
      assert.equal(await page.locator('.fc-tutorial, .fc-tutorial-board').count(),0,'start removes demo synchronously');
      assert.equal(await page.locator('.fc-table').count(),1);
      await shot('started');
      assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null,'forced close does not pretend completion');
      await page.evaluate(k=>localStorage.setItem(k,'completed'),key);
      // Real replay for representative gameplay, no injected game moves.
      fixture=backend({action:'seed',game_type:`four_color_${mode}`,count,fixture:'dense'});
      fixture.room.room_kind='invite';
      await page.evaluate(f=>renderGame(f.room,"",f.timeline),fixture);
      await shot('real-game');
      // Visual-only overflow projection: long names and many groups in every outer rail.
      fixture.room.participants.forEach((p,i)=>{p.display_name=i===1?'Codex_杉星_0828':`玩家${i}长昵称`;});
      for(const [i,pid] of fixture.room.board_state.participant_order.entries()) {
        fixture.room.board_state.melds[pid]=Array.from({length:18},(_,j)=>({cards:[0,1,2].map(r=>`${'RYGW'[(j+i)%4]}${r}-1`)}));
      }
      fixture.room.revision++;
      await page.evaluate(f=>renderGame(f.room,"",f.timeline),fixture);
      await shot('overflow-projection');
      const geometry=await page.locator('.fc-table').evaluate(t=>{
        const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
        return {table:rect(t),seats:[...t.querySelectorAll('.fc-seat')].map(n=>({position:n.dataset.position,identity:rect(n.querySelector('.fc-seat-identity')),rail:rect(n.querySelector('.fc-seat-public'))})),center:rect(t.querySelector('.fc-table-center'))};
      });
      const overlap=(a,b)=>Math.min(a.right,b.right)-Math.max(a.x,b.x)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1;
      for(const s of geometry.seats) {
        assert.ok(!overlap(s.identity,s.rail),'identity must not obscure own rail');
        for(const other of geometry.seats)assert.ok(!overlap(s.identity,other.rail),`${s.position} identity obscures ${other.position} rail`);
        assert.ok(!overlap(s.rail,geometry.center),`${s.position} rail obscures center`);
        for(const other of geometry.seats)if(s!==other)assert.ok(!overlap(s.rail,other.rail),'outer rails must not overlap');
        if(s.position==='north'||s.position==='south')assert.ok(s.rail.width>=geometry.table.width-24,'outer horizontal rail uses side whitespace');
      }
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      for(const s of geometry.seats)await swipe(page,`.fc-position-${s.position} .fc-seat-public`,/^(east|west)$/.test(s.position)?'y':'x');
      // Keyboard scrolling and focus loop, no rules-page tutorial entry.
      assert.equal(await page.locator('#rulesText button').filter({hasText:/教程/}).count(),0);
      assert.deepEqual(errors,[]);
      measurements.push({mode,count,width,...geometry,errors});
      fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(measurements,null,2));
      console.log(`${mode}/${width}: passed`);await page.close();
    }
  } finally {await browser.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
