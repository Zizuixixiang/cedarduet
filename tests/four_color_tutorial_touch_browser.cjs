/* Tutorial-only regression on the actual page. All HTTP requests intercepted;
   fixtures use disposable SQLite and never contact a production room. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const out = process.env.FOUR_COLOR_ARTIFACTS || '/tmp/four-color-tutorial-evidence';
const tmp = fs.mkdtempSync('/tmp/fc-tutorial-touch-');
const key = 'cedarduet.four_color.tutorial.v1';
fs.mkdirSync(out, {recursive:true});
function backend(request, db) {
  const r = spawnSync(process.env.FOUR_COLOR_PYTHON || path.join(root,'.venv/bin/python'),
    [path.join(__dirname,'four_color_ui_fixture.py')], {cwd:root,encoding:'utf8',input:JSON.stringify(request),timeout:30000,
      env:{...process.env,PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1',DUEL_DB_PATH:db}});
  assert.equal(r.status,0,r.stderr);const result=JSON.parse(r.stdout);assert.ok(result.ok,JSON.stringify(result));return result;
}
async function swipe(page, port, axis) {
  await port.evaluate(n => { n.scrollLeft = 0; n.scrollTop = 0; });
  const r=await port.boundingBox();
  const before=await port.evaluate(n=>({x:n.scrollLeft,y:n.scrollTop,parent:n.closest('dialog').scrollTop,page:scrollY}));
  const cdp=await page.context().newCDPSession(page);
  const x=r.x+(axis==='x'?r.width-20:r.width/2), y=r.y+(axis==='y'?r.height-20:r.height/2);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(let i=1;i<=10;i++) {
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-(axis==='x'?i*12:0),y:y-(axis==='y'?i*12:0)}]});
    await page.waitForTimeout(20);
  }
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForTimeout(250);await cdp.detach();
  const after=await port.evaluate(n=>({x:n.scrollLeft,y:n.scrollTop,parent:n.closest('dialog').scrollTop,page:scrollY}));
  assert.ok(after[axis]>before[axis]+20,`touch must scroll ${axis}: ${JSON.stringify({before,after})}`);
  assert.equal(after.parent,before.parent,'dialog must not consume the gesture');
  assert.equal(after.page,before.page,'page must not consume the gesture');
  return {axis,before,after};
}
async function spotlightMetrics(page) {
  const metrics = await page.evaluate(() => {
    const dialog = document.querySelector('.fc-tutorial');
    const target = dialog.querySelector('[data-tutorial-target]');
    const spotlight = dialog.querySelector('.fc-tutorial-spotlight');
    const panel = dialog.querySelector('.fc-tutorial-panel');
    const t = target.getBoundingClientRect(), h = spotlight.getBoundingClientRect(), p = panel.getBoundingClientRect();
    const box = r => ({x:r.x,y:r.y,width:r.width,height:r.height});
    const ports = target.tabIndex === 0 ? [target] : [];
    return {
      target:box(t), spotlight:box(h), panel:box(p),
      aligned:Math.abs(h.x-t.x+4)<1 && Math.abs(h.y-t.y+4)<1
        && Math.abs(h.width-t.width-8)<1 && Math.abs(h.height-t.height-8)<1,
      separate:p.top>=h.bottom || p.bottom<=h.top,
      inView:[t,p,h].every(r=>r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight),
      mask:getComputedStyle(spotlight).boxShadow,
      transparent:getComputedStyle(dialog).backgroundColor,
      nativeModal:dialog.matches(':modal'),
      touchTargets:ports.every(port=>{
        const r=port.getBoundingClientRect();
        return port.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
      }),
      overflow:document.documentElement.scrollWidth>innerWidth,
    };
  });
  assert.ok(metrics.aligned && metrics.separate && metrics.inView,JSON.stringify(metrics));
  assert.ok(metrics.nativeModal && metrics.touchTargets,'modal isolates game while demo receives input');
  assert.ok(metrics.mask.includes('9999px') && metrics.transparent==='rgba(0, 0, 0, 0)','Gem-style cutout mask');
  assert.equal(metrics.overflow,false);
  return metrics;
}
(async()=>{
  let browser;const evidence=[];
  try {
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    for(const width of [360,430,1280]) for(const status of ['waiting','playing']) {
      const db=path.join(tmp,`${width}-${status}.db`);
      let fixture=backend({action:'seed',game_type:'four_color_tenhu',count:4,invited:true,fixture:status==='waiting'?'waiting':'opening'},db);
      const original=JSON.stringify(fixture);
      const page=await browser.newPage({viewport:{width,height:800},hasTouch:true,isMobile:width<500});
      const errors=[], posts=[];page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',async route=>{
        const req=route.request(),u=new URL(req.url()),p=u.pathname;
        if(u.hostname!=='four-color.test')return route.abort();
        if(p==='/'||p.startsWith('/static/')) {
          const rel=p==='/'?'index.html':p.slice(8),file=path.join(root,'app/static',rel);
          return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
        }
        if(req.method()==='POST' && p.startsWith('/api/rooms'))posts.push(p);
        let data={ok:true,rooms:[],timeline:[],notifications:[]};
        if(p==='/api/whoami')data={...data,bound:true,human_player_id:'p0',human_name:'玩家0',identity_label:'玩家0',machines:[],wallet:{balance:0},games:[{game_type:'four_color_tenhu',display_name:'四色牌·十胡',category:'card',allowed_player_counts:[2,3,4],recommended_players:4,supports_stakes:false}]};
        else if(p===`/api/rooms/${fixture.room.room_id}`)data=fixture;
        return route.fulfill({json:data});
      });
      const button=text=>page.getByRole('button',{name:text,exact:true});
      const shot=label=>page.screenshot({path:path.join(out,`${status}-${width}-${label}.png`)});
      await page.goto(`http://four-color.test/?room=${fixture.room.room_id}`);
      await page.locator('.fc-tutorial[open]').waitFor();
      const stateBefore=await page.evaluate(()=>JSON.stringify(room));
      const uiBefore=await page.evaluate(()=>JSON.stringify(registeredGameUIState));
      const boardBefore=await page.locator('#board').innerHTML();
      assert.equal(await page.locator('#fc-tutorial-copy').innerText(),'上下横滑、左右竖滑，就能查看被叠住的牌。');
      await shot('welcome');await button('查看教程').click();
      const scrolls=[], highlights=[];
      for (let step = 0; step < 4; step++) {
        if (step) await button('下一步').click();
        const board = page.locator('.fc-tutorial-board');
        assert.equal(await board.locator('.fc-seat').count(), 4);
        assert.equal(await board.locator('.room-participant-avatar').count(), 4);
        assert.equal(await board.locator('.fc-stock').count(), 1);
        assert.ok(await board.locator('.fc-hand').count());
        assert.equal(await board.getAttribute('data-source'), status === 'waiting' ? 'example' : 'snapshot');
        assert.equal(await board.locator('button:not(:disabled)').count(), 0);
        const port = board.locator('[data-tutorial-target]');
        const axis = step < 2 ? 'x' : 'y';
        const overflow = await port.evaluate((n, axis) => axis === 'x'
          ? n.scrollWidth - n.clientWidth : n.scrollHeight - n.clientHeight, axis);
        // Sparse real snapshots must stay sparse. Never inject fake cards into them.
        if (overflow > 20) scrolls.push(await swipe(page, port, axis));
        else scrolls.push({axis, step, overflow, skipped:'no overflow in this projection'});
        if (status === 'waiting' && [0, 2, 3].includes(step)) assert.ok(overflow > 20, 'example provides horizontal and vertical practice');
        highlights.push(await spotlightMetrics(page));
        await shot(`full-board-step-${step + 1}`);
      }
      assert.equal(await page.evaluate(()=>JSON.stringify(room)),stateBefore,'practice cannot mutate room state');
      assert.equal(await page.evaluate(()=>JSON.stringify(registeredGameUIState)),uiBefore,'practice cannot mutate UI drafts');
      assert.equal(await page.locator('#board').innerHTML(),boardBefore,'practice cannot modify real board DOM');
      assert.equal(JSON.stringify(fixture),original);assert.deepEqual(posts,[]);
      await page.setViewportSize({width:width===360?430:360,height:800});
      await page.waitForTimeout(100);await spotlightMetrics(page);
      await page.setViewportSize({width,height:800});
      await page.waitForTimeout(100);await spotlightMetrics(page);
      for(let i=0;i<7;i++) {
        await page.keyboard.press('Tab');
        assert.ok(await page.evaluate(()=>!!document.activeElement.closest('.fc-tutorial')));
      }
      await button('上一步').click();assert.equal(await page.locator('.fc-tutorial').getAttribute('data-step'),'3');
      await button('关闭本次教程').click();assert.equal(await page.locator('.fc-tutorial-board').count(),0);
      assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
      await page.reload();await page.locator('.fc-tutorial[open]').waitFor();
      await page.keyboard.press('Escape');assert.equal(await page.locator('.fc-tutorial').count(),0);
      assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
      await page.reload();await page.locator('.fc-tutorial[open]').waitFor();
      await button('不再提示').click();assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),'dismissed');
      await page.reload();await page.locator(status==='waiting'?'#inviteRoomPanel:not(.hidden)':'.fc-table').waitFor();
      assert.equal(await page.locator('.fc-tutorial').count(),0);
      await page.evaluate(k=>localStorage.removeItem(k),key);await page.reload();await button('查看教程').click();
      for (let i = 0; i < 3; i++) await button('下一步').click();await button('完成').click();assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),'completed');
      await page.reload();await page.locator(status==='waiting'?'#inviteRoomPanel:not(.hidden)':'.fc-table').waitFor();
      assert.equal(await page.locator('.fc-tutorial').count(),0);
      if(status==='waiting') {
        await page.evaluate(k=>localStorage.removeItem(k),key);await page.reload();await button('查看教程').click();
        fixture=backend({action:'start',room_id:fixture.room.room_id},db);
        const remaining=await page.evaluate(f=>{renderGame(f.room,'',f.timeline);return document.querySelectorAll('.fc-tutorial, .fc-tutorial-board').length;},fixture);
        assert.equal(remaining,0,'waiting -> playing removes the whole tutorial synchronously');
        assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
      }
      assert.deepEqual(errors,[]);
      evidence.push({status,width,scrolls,highlights,errors});fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(evidence,null,2));
      console.log(`PASS ${status}/${width}: full board and actual region highlights; state isolated; memory and cleanup`);
      await page.close();
    }
  } finally {await browser?.close();fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
