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
  const distance = Math.min(100, (axis === 'x' ? r.width : r.height) - 12);
  const x=r.x+(axis==='x'?r.width-6:r.width/2), y=r.y+(axis==='y'?r.height-6:r.height/2);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(let i=1;i<=10;i++) {
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-(axis==='x'?i*distance/10:0),y:y-(axis==='y'?i*distance/10:0)}]});
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
  assert.ok(metrics.mask.includes('9999px') && metrics.transparent!=='rgba(0, 0, 0, 0)','Gem-style cutout over an opaque tutorial surface');
  assert.equal(metrics.overflow,false);
  return metrics;
}
(async()=>{
  let browser;const evidence=[];
  try {
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    const widths = (process.env.FOUR_COLOR_WIDTHS || '360,430,1280').split(',').map(Number);
    for(const width of widths) for(const status of ['waiting','playing']) {
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
      await page.evaluate(() => { window.realBoardClicks = 0; document.querySelector('#board').addEventListener('click', () => window.realBoardClicks++); });
      assert.equal(await page.locator('#fc-tutorial-copy').innerText(),'滑动公开牌和弃牌区，就能查看被叠住的牌。');
      await shot('welcome');await button('查看教程').click();
      const scrolls=[], highlights=[];
      for (let step = 0; step < 3; step++) {
        if (step) await button('下一步').click();
        const board = page.locator('.fc-tutorial-board');
        assert.equal(await board.locator('.fc-seat').count(), 4);
        assert.equal(await board.locator('.room-participant-avatar').count(), 4);
        assert.equal(await board.locator('.fc-stock').count(), 1);
        assert.equal(await board.locator('.fc-hand').count(), 1);
        assert.equal(await board.getAttribute('data-source'), 'example');
        assert.equal(await board.locator('button:not(:disabled)').count(), 0);
        const ports = [board.locator('[data-tutorial-target]')];
        const density = await board.evaluate(b => [...b.querySelectorAll('.fc-seat-public')].map(n => ({
          target:n.hasAttribute('data-tutorial-target'), cards:n.querySelectorAll('.fc-card').length,
          groups:n.querySelectorAll('.fc-meld').length
        })));
        density.filter(n => !n.target).forEach(n => assert.equal(n.cards,9,'other seats remain ordinary'));
        highlights.push({density});
        for (const port of ports) {
          const dimensions = await port.evaluate(n => ({x:n.scrollWidth-n.clientWidth, y:n.scrollHeight-n.clientHeight,
            width:n.clientWidth, height:n.clientHeight, axis:n.dataset.axis, touch:getComputedStyle(n).touchAction,
            type:n.className, position:n.closest('.fc-seat')?.dataset.position}));
          // Only the current representative needs overflow; the opposite seat stays ordinary.
          for (const axis of step === 2 ? [dimensions.axis] : ['x','y']) {
            assert.ok(dimensions[axis] > 20 && dimensions[axis] < 58, JSON.stringify({width, step, dimensions, axis}));
            scrolls.push({...dimensions, step, ...await swipe(page, port, axis)});
          }
        }
        if (step === 2) assert.ok((await board.locator('[data-tutorial-target]').getAttribute('class')).includes('fc-discard-history'));
        highlights.push(await spotlightMetrics(page));
        // No click can reach the board below the native modal or select a practice card.
        await board.locator('.fc-hand button').first().dispatchEvent('click');
        const hit = await page.evaluate(() => {
          const d = document.querySelector('dialog.fc-tutorial');
          return [[2,2],[innerWidth-2,innerHeight-2],[innerWidth/2,innerHeight-2]]
            .every(([x,y]) => { const n = document.elementFromPoint(x,y); return n === d || d.contains(n); });
        });
        assert.ok(hit, 'entire viewport remains inside tutorial, including bottom corners');
        await page.touchscreen.tap(2, 2); await page.touchscreen.tap(width-2, 798);
        assert.equal(await page.evaluate(() => window.realBoardClicks), 0);
        await shot(`full-board-step-${step + 1}`);
        if (width < 500) {
          await page.setViewportSize({width:width===360?430:360,height:800});
          await page.waitForTimeout(100);await spotlightMetrics(page);
          const port=board.locator('[data-tutorial-target]');
          const dimensions=await port.evaluate(n=>({x:n.scrollWidth-n.clientWidth,y:n.scrollHeight-n.clientHeight,axis:n.dataset.axis}));
          for(const axis of step===2?[dimensions.axis]:['x','y']) {
            assert.ok(dimensions[axis]>20 && dimensions[axis]<58,'resize keeps only a short overflow');
            scrolls.push({resized:true,step,...dimensions,...await swipe(page,port,axis)});
          }
          await page.setViewportSize({width,height:800});await page.waitForTimeout(100);
        }
      }
      assert.equal(await page.evaluate(()=>JSON.stringify(room)),stateBefore,'practice cannot mutate room state');
      assert.equal(await page.evaluate(()=>JSON.stringify(registeredGameUIState)),uiBefore,'practice cannot mutate UI drafts');
      assert.equal(await page.locator('#board').innerHTML(),boardBefore,'practice cannot modify real board DOM');
      assert.equal(JSON.stringify(fixture),original);assert.deepEqual(posts,[]);
      await page.setViewportSize({width:width===360?430:360,height:800});
      await page.waitForTimeout(100);await spotlightMetrics(page);
      await page.setViewportSize({width,height:800});
      await page.waitForTimeout(100);await spotlightMetrics(page);
      await page.setViewportSize({width, height:640}); await page.waitForTimeout(100); await spotlightMetrics(page);
      await shot('short-640');
      await page.setViewportSize({width, height:800}); await page.waitForTimeout(100);
      for(let i=0;i<7;i++) {
        await page.keyboard.press('Tab');
        assert.ok(await page.evaluate(()=>!!document.activeElement.closest('.fc-tutorial')));
      }
      await button('上一步').click();assert.equal(await page.locator('.fc-tutorial').getAttribute('data-step'),'2');
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
      for (let i = 0; i < 2; i++) await button('下一步').click();await button('完成').click();assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),'completed');
      await page.reload();await page.locator(status==='waiting'?'#inviteRoomPanel:not(.hidden)':'.fc-table').waitFor();
      assert.equal(await page.locator('.fc-tutorial').count(),0);
      if(status==='waiting') {
        await page.evaluate(k=>localStorage.removeItem(k),key);await page.reload();await button('查看教程').click();
        fixture=backend({action:'start',room_id:fixture.room.room_id},db);
        const remaining=await page.evaluate(f=>{renderGame(f.room,'',f.timeline);return document.querySelectorAll('.fc-tutorial, .fc-tutorial-board').length;},fixture);
        assert.equal(remaining,0,'waiting -> playing removes the whole tutorial synchronously');
        assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null);
      }
      if (status === 'playing') {
        for (const seats of [4, 5]) {
          const geometryFixture = seats === 4 ? fixture : backend({action:'seed',game_type:'four_color_qunhu',count:5,invited:true,fixture:'opening'},path.join(tmp,`${width}-five.db`));
          await page.evaluate(f => {
            const sparse = structuredClone(f);
            for (const pid of sparse.room.board_state.participant_order) sparse.room.board_state.melds[pid] =
              Array.from({length:6}, (_, i) => ({cards:[1,2,3].map(copy => `R${i}-${copy}`)}));
            sparse.room.revision++;
            renderGame(sparse.room, '', sparse.timeline);
          }, geometryFixture);
          const sparse = await page.locator('#board').evaluate(board => {
            const box = n => { const r=n.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
            const ports = [...board.querySelectorAll('.fc-seat-public')];
            const positions = () => [...board.querySelectorAll('.fc-seat-identity')].map(box);
            const measure = () => ports.map(n => ({port:box(n), groups:box(n.querySelector('.fc-seat-groups')),
              card:box(n.querySelector('.fc-card')), gap:getComputedStyle(n.querySelector('.fc-seat-groups')).gap,
              side:!!n.closest('.fc-seat-side')}));
            const avatars = positions(), expanded = measure();
            const before = document.createElement('style');
            before.textContent = `
              .fc-position-north .fc-seat-public,.fc-position-south .fc-seat-public {left:6px;right:6px}
              .fc-seat-side .fc-seat-public {top:180px;bottom:142px}
              .fc-table[data-seats="5"] :is(.fc-position-north-west,.fc-position-north-east) .fc-seat-public {width:calc(50% - 16px)}
              .fc-table[data-seats="5"] .fc-position-north-west .fc-seat-public {left:10px}
              .fc-table[data-seats="5"] .fc-position-north-east .fc-seat-public {right:10px}
              .fc-table[data-seats="5"] .fc-seat-side .fc-seat-public {top:248px;bottom:132px}
              .fc-table[data-seats="5"] .fc-position-south .fc-seat-public {left:10px;right:10px}`;
            document.head.append(before);
            const old = measure(), oldAvatars = positions(); before.remove();
            const overlap = (a,b) => Math.min(a.x+a.width,b.x+b.width)>Math.max(a.x,b.x)+.5
              && Math.min(a.y+a.height,b.y+b.height)>Math.max(a.y,b.y)+.5;
            const collisions = [];
            expanded.forEach((a,i) => {
              avatars.forEach((b,j) => {if(overlap(a.port,b))collisions.push(`port${i}/avatar${j}`)});
              expanded.slice(i+1).forEach((b,j) => {if(overlap(a.port,b.port))collisions.push(`port${i}/port${i+j+1}`)});
            });
            return {expanded, old, avatars, oldAvatars, collisions};
          });
          assert.deepEqual(sparse.avatars,sparse.oldAvatars,'avatars never move');
          assert.deepEqual(sparse.collisions,[],'four/five-player ports avoid one another and avatars');
          sparse.expanded.forEach((n,i) => {
            const old=sparse.old[i], long=n.side?'height':'width', short=n.side?'width':'height';
            assert.equal(n.groups.width,old.groups.width,'sparse groups keep original compact width');
            assert.equal(n.groups.height,old.groups.height);
            assert.equal(n.card.width,old.card.width);assert.equal(n.card.height,old.card.height);
            assert.equal(n.gap,old.gap);
            assert.equal(n.port[short],old.port[short]);
            assert.ok(n.port[long]-old.port[long]>=4 && n.port[long]-old.port[long]<=6,'only 4–6px extension');
          });
          highlights.push({seats,sparse});await shot(`normal-six-columns-${seats}-seats`);
        }
      }
      assert.deepEqual(errors,[]);
      evidence.push({status,width,scrolls,highlights,errors});fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(evidence,null,2));
      console.log(`PASS ${status}/${width}: full board and actual region highlights; state isolated; memory and cleanup`);
      await page.close();
    }
  } finally {await browser?.close();fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
