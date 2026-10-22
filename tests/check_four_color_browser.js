/* Real page + real engine in disposable SQLite. All network intercepted. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../../..');
const out = process.env.FOUR_COLOR_ARTIFACTS || '/tmp/cedartoy-four-color-ui';
fs.mkdirSync(out, {recursive:true});
const tmp = fs.mkdtempSync(path.join(out, 'db-'));
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const env = {...process.env, PYTHONPATH:path.join(root,'vendor/duel'), PYTHONDONTWRITEBYTECODE:'1'};
let sequence = 0;
function backend(input) {
  if (input.action === 'seed') env.DUEL_DB_PATH = path.join(tmp, `${++sequence}.db`);
  const r = spawnSync(path.join(root,'vendor/duel/.venv/bin/python'), [path.join(__dirname,'four_color_ui_fixture.py')],
    {env, cwd:root, input:JSON.stringify(input), encoding:'utf8', timeout:15000});
  assert.equal(r.status, 0, r.stderr); return JSON.parse(r.stdout);
}
(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const metrics = [];
  try {
    for (const mode of ["tenhu","jiuzhi","qunhu"]) for (const width of [360,430,1280]) {
      if (process.env.FOUR_COLOR_CASE && process.env.FOUR_COLOR_CASE !== `${mode}:${width}`) continue;
      const gameType = `four_color_${mode}`;
      console.log(JSON.stringify({mode,width,status:"running"}));
      const page = await browser.newPage({viewport:{width,height:1000},hasTouch:width<500});
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      let fixture, posts = [], failNext = false;
      await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url()), p = url.pathname;
        if (url.hostname !== 'four-color.test') return route.abort();
        if (p === '/' || p.startsWith('/static/')) {
          const rel = p === '/' ? 'index.html' : p.slice(8), file = path.join(root,'vendor/duel/app/static',rel);
          if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
          return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
        }
        let data = {ok:true,notifications:[],rooms:[],timeline:[]};
        if (p === '/api/whoami') data = {...data,bound:true,human_player_id:'p0',human_name:'玩家0',identity_label:'玩家0',machines:[{id:'p1',name:'玩家1'}],wallet:{balance:0},
          games:['tenhu','jiuzhi','qunhu'].map(mode => ({game_type:`four_color_${mode}`,display_name:`四色牌·${mode}`,category:'card',allowed_player_counts:mode==='tenhu'?[2,3,4]:mode==='jiuzhi'?[4]:[3,4,5],recommended_players:4,
            supports_npcs:true,uses_local_npc_strategy:true,supports_stakes:false}))};
        else if (p.match(/^\/api\/rooms\/[A-Z0-9]+$/)) data = fixture;
        else if (p.endsWith('/move')) {
          const body = req.postDataJSON(); posts.push(body);
          if (failNext) { failNext = false; return route.fulfill({status:503,json:{detail:'测试断线，请重试'}}); }
          data = backend({action:'move',room_id:fixture.room.room_id,move:body.move,revision:body.revision});
          if (data.ok) fixture = data;
        } else if (p === '/api/invites' && req.method() === 'POST') {
          const body=req.postDataJSON(); assert.equal(body.game_type,gameType); assert.equal(body.stake,0);
          fixture=backend({action:'seed',game_type:gameType,count:body.target_player_count,invited:true}); data=fixture;
        } else if (p === '/api/rooms' && req.method() === 'POST') {
          const body = req.postDataJSON(); assert.equal(body.game_type, gameType); assert.equal(body.stake, 0);
          fixture = backend({action:'seed',game_type:gameType,count:body.target_player_count || 4}); data = fixture;
        }
        return route.fulfill({status:data.ok===false?(data.status||400):200,json:data});
      });
      async function dismiss() {
        const d = page.locator('#dismissWaitModeModalButton');
        if (await d.isVisible()) await d.click();
      }
      async function closeResult() {
        await page.locator('#resultModal').waitFor({state:'visible'});
        await page.locator('#finishGameButton').click();
        await page.locator('#resultModal').waitFor({state:'hidden'});
      }
      async function screenshot(name, target) {
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${width}/${name} overflow`);
        await (target || page).screenshot({path:path.join(out,`${mode}-${name}-${width}.png`),...(target?{}:{fullPage:true})});
      }
      async function open(kind, invited = false) {
        fixture = backend({action:'seed',game_type:gameType,fixture:kind,count:4,invited});
        await page.goto(`http://four-color.test/?room=${fixture.room.room_id}`);
        await page.locator('.fc-hand').waitFor(); await dismiss();
      }
      await page.goto('http://four-color.test/');
      await page.locator('#lobbyView:not(.hidden)').waitFor();
      await page.selectOption('#gameCategory','card');
      await page.locator('#gameTypeRulePicker').waitFor();
      const rects = await page.locator('#gameTypeRulePicker .fc-rule-card').evaluateAll(nodes => nodes.map(n => {
        const r = n.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,title:getComputedStyle(n.querySelector('strong')).fontSize};
      }));
      assert.equal(rects.length,3);
      rects.forEach((r,i) => {assert.equal(r.x,rects[0].x);assert.equal(r.width,rects[0].width);if(i)assert.ok(r.y>=rects[i-1].y+rects[i-1].height);});
      assert.equal(await page.locator('#gameTypeRulePicker input:disabled').count(),0);
      await page.locator(`#gameTypeRulePicker input[value="${mode}"]`).check();
      await screenshot('rules', page.locator('.create-panel'));
      const overlaps = await page.locator('.create-form').evaluate(form => {
        const a = form.querySelector('#createButton').getBoundingClientRect();
        const b = form.querySelector('#multiplayerOptions').getBoundingClientRect();
        return Math.min(a.right,b.right) > Math.max(a.left,b.left) && Math.min(a.bottom,b.bottom) > Math.max(a.top,b.top);
      });
      assert.equal(overlaps,false,'create button must not overlap NPC controls');
      await page.locator(`#gameTypeRulePicker input[value="${mode}"]`).focus(); await page.keyboard.press('Space');
      await page.locator('#inviteCreateEntry').click(); await page.selectOption('#inviteCategory','card');
      await page.locator('#inviteGameRulePicker').waitFor();
      assert.equal(await page.locator('#inviteGameRulePicker .fc-rule-card').count(),3);
      await page.locator(`#inviteGameRulePicker input[value="${mode}"]`).check();
      await screenshot('invite',page.locator('#inviteDialog'));
      await page.selectOption('#inviteCount','4');
      await page.locator('#inviteSubmit').click();
      await page.locator('.fc-hand').waitFor(); await dismiss();
      assert.equal(fixture.room.game_type,gameType); assert.equal(fixture.room.room_kind,'invite');
      await page.goto('http://four-color.test/');
      await page.locator('#lobbyView:not(.hidden)').waitFor();
      await page.selectOption('#gameCategory','card');
      await page.locator(`#gameTypeRulePicker input[value="${mode}"]`).check();
      await page.locator('#createButton').click();
      await page.locator('.fc-hand').waitFor(); await dismiss();
      assert.equal(fixture.room.game_type,gameType);
      await open('opening');
      const handCount=fixture.room.private_state.hand.length;
      assert.equal(await page.locator('.fc-hand .fc-card').count(),handCount);
      await screenshot('opening');
      const geometry = await page.locator('.fc-hand').evaluate(hand => {
        const names={R:'帥仕相俥傌炮兵',Y:'帥仕相俥傌炮兵',G:'將士象車馬包卒',W:'將士象車馬包卒'};
        const faces=[...hand.querySelectorAll('.fc-card')].map(face => {
          const button=face.parentElement, id=button.dataset.cardId, r=face.getBoundingClientRect(), b=button.getBoundingClientRect();
          return {id,width:r.width,height:r.height,targetWidth:b.width,targetHeight:b.height,
            glyphs:[...face.querySelectorAll('strong')].map(n=>n.textContent),expected:names[id[0]][Number(id[1])],disabled:button.disabled};
        });
        return {width:hand.clientWidth,scrollWidth:hand.scrollWidth,faces};
      });
      for(const face of geometry.faces) {
        assert.ok(face.height/face.width>=2.5 && face.height/face.width<=2.7,JSON.stringify(face));
        assert.ok(face.targetWidth>=44 && face.targetHeight>=44,JSON.stringify(face));
        assert.deepEqual(face.glyphs,[face.expected,face.expected]);
      }
      if(width<500 && handCount>20) assert.ok(geometry.scrollWidth>geometry.width,'long rack scrolls locally');
      if(width<500 && geometry.scrollWidth>geometry.width) {
        const rack=await page.locator('.fc-hand').boundingBox(), cdp=await page.context().newCDPSession(page);
        const start=rack.x+rack.width-20, y=rack.y+45;
        await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:start,y}]});
        for(let i=1;i<=8;i++) {
          await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:start-i*20,y}]});
          await page.waitForTimeout(20);
        }
        await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
        await page.waitForFunction(()=>document.querySelector('.fc-hand').scrollLeft>0);
        await page.waitForTimeout(250); await cdp.detach();
      }
      const unavailable=page.locator('.fc-hand button[data-unavailable="true"]');
      if(await unavailable.count()) {
        assert.equal(await unavailable.first().isDisabled(),true);
        const was=posts.length;
        await unavailable.first().evaluate(n=>n.click());
        assert.equal(posts.length,was); assert.equal(await page.locator('.fc-hand [aria-pressed="true"]').count(),0);
      }
      // Reach the far end by scrolling, then retain that position across rerenders.
      const tile = page.locator('.fc-hand button:not(:disabled)').last();
      await tile.scrollIntoViewIfNeeded();
      const selectedId=await tile.getAttribute('data-card-id');
      await page.waitForTimeout(50);
      const scrollBefore=await page.locator('.fc-hand').evaluate(n=>n.scrollLeft);
      if (width < 500) await tile.tap(); else await tile.click();
      assert.equal(await page.locator('.fc-hand [aria-pressed="true"]').count(),1);
      assert.ok(Math.abs(await page.locator('.fc-hand').evaluate(n=>n.scrollLeft)-scrollBefore)<=1,'selection preserves rack scroll');
      await page.keyboard.press('Space');
      assert.equal(await page.locator('.fc-hand [aria-pressed="true"]').count(),0);
      await page.keyboard.press('Space');
      assert.equal(await page.locator('.fc-hand [aria-pressed="true"]').getAttribute('data-card-id'),selectedId);
      const before = fixture.room.revision, submit = page.locator('[data-fc-action="discard"]');
      const buttonBox = await submit.boundingBox(); assert.ok(buttonBox.height >= (width<500?44:40));
      const selectedTarget=page.locator('.fc-hand [aria-pressed="true"]');
      assert.equal(await selectedTarget.evaluate(n=>{
        const r=n.getBoundingClientRect();
        return [[2,2],[r.width-2,2],[2,r.height-2],[r.width-2,r.height-2]].every(([x,y])=>n.contains(document.elementFromPoint(r.left+x,r.top+y)));
      }),true,'selected target must clear the floating rules tab');
      await screenshot('selected');
      failNext = true; await submit.click();
      await page.waitForFunction(() => !document.querySelector('[data-fc-action="discard"]').disabled);
      assert.equal(fixture.room.revision,before);
      await submit.click(); await page.waitForFunction(n => document.querySelectorAll('.fc-hand .fc-card').length === n, handCount-1);
      assert.equal(fixture.room.revision,before+1); assert.equal(posts.at(-1).revision,before);
      const count = posts.length;
      await page.reload(); await page.locator('.fc-hand').waitFor(); await dismiss();
      assert.equal(await page.locator('.fc-hand .fc-card').count(),handCount-1); assert.equal(posts.length,count);
      await screenshot('reconnected');
      await page.locator('#rulesButton').click();
      await page.locator('#rulesText').waitFor({state:'visible'});
      assert.match(await page.locator('#rulesText').innerText(),/双弈平台约定/);
      await screenshot('rule-text',page.locator('#rulesText'));
      await page.locator('#closeRulesButton').click();
      if(mode==='qunhu') {
        await open('listen'); await page.locator('.fc-hand [data-card-id="R4-4"]').click();
        await page.locator('[data-fc-action="listen"]').click();
        await page.waitForFunction(() => document.querySelector('.fc-rack')?.parentElement.textContent.includes('已听：'));
        assert.ok(fixture.room.board_state.listening.p0); await screenshot('listening');
      }
      await open('hu',true);
      await screenshot('hu-offer');
      for(const ratio of await page.locator('.fc-offer .fc-card, .fc-groups .fc-card').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return r.height/r.width;}))) assert.ok(ratio>=2.5&&ratio<=2.7);
      await page.locator('[data-fc-action^="hu"]').first().click();
      await page.locator('.fc-result').waitFor();
      assert.equal(fixture.room.board_state.result.reason,'hu');
      assert.equal(Object.values(fixture.room.board_state.result.point_deltas).reduce((a,b)=>a+b,0),0);
      await closeResult();
      if (!fixture.room.private_state.hand.length) {
        assert.equal((await page.locator('.fc-hand').boundingBox()).height,0,'empty hand must not reserve a card row');
      }
      await screenshot('settlement');
      await open('empty'); await page.locator('[data-fc-action="draw"]').click();
      await page.locator('.fc-result').waitFor(); assert.equal(fixture.room.board_state.result.reason,'wall_exhausted');
      assert.equal(await page.locator('.fc-controls button').count(),0);
      await closeResult();
      const pile=page.locator('.fc-discards').first();
      await pile.locator('summary').click();
      assert.ok((await pile.locator('.fc-cards').boundingBox()).height<=220);
      await screenshot('discards',pile);
      assert.deepEqual(errors,[]);
      metrics.push({mode,width,ruleCards:rects,hand:geometry,submitHeight:buttonBox.height,consoleErrors:errors});
      fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(metrics,null,2));
      console.log(JSON.stringify({mode,width,status:"passed"}));
      await page.close();
    }
    assert.ok(metrics.length,'FOUR_COLOR_CASE must match a mode:width case');
    fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(metrics,null,2));
    console.log(JSON.stringify({passed:true,viewports:metrics.map(m=>m.width),screenshots:out}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
