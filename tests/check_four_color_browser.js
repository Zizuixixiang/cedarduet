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
  let browser;
  const metrics = [];
  try {
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    for (const mode of ["tenhu","jiuzhi","qunhu"]) for (const width of [360,430,1280]) {
      if (process.env.FOUR_COLOR_CASE && process.env.FOUR_COLOR_CASE !== `${mode}:${width}`) continue;
      const gameType = `four_color_${mode}`;
      console.log(JSON.stringify({mode,width,status:"running"}));
      const page = await browser.newPage({viewport:{width,height:width===360?800:900},hasTouch:width<500});
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      let fixture, posts = [], failNext = false;
      const firstViewport = [];
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
        if (await page.locator('.fc-table').isVisible()) {
          const state = fixture.room.board_state;
          assert.equal(await page.locator('#roomParticipants').isVisible(),false,'embedded seats replace the generic roster');
          assert.equal(await page.locator('.fc-seat').count(),state.participant_order.length);
          for (const p of fixture.room.participants) {
            const badge = page.locator(`.fc-seat[data-player-id="${p.player_id}"]`);
            assert.equal(await badge.count(),1,'one table seat per player');
            const shownInHand = new Set([...(state.broken?.[p.player_id] || []), ...(state.listening?.[p.player_id] || [])]).size;
            const side=['east','west'].includes(await badge.getAttribute('data-position'));
            const info=badge.locator('.fc-seat-counts');
            const hidden=Math.max(0,state.hand_counts[p.player_id]-shownInHand);
            const full=[`暗牌 ${hidden} 张`], compact=[`暗${hidden}`];
            const pairs=state.closed_pair_counts?.[p.player_id];
            if(Number.isInteger(pairs)) {full.push(`扣置 ${pairs} 对`);compact.push(`扣${pairs}`);}
            const points = state.result?.point_deltas?.[p.player_id];
            assert.equal(await badge.evaluate(n=>n.classList.contains('fc-seat-current')),fixture.room.status==='playing' && p.player_id===fixture.room.current_player_id,'only the acting seat receives a gold border');
            if(Number.isInteger(points)) {
              const score=`${points>0?'+':''}${points}`;
              full.push(`${score} 分`); compact.push(score);
            }
            if(side) {
              assert.equal(await info.getAttribute('title'),full.join(' · '));
              assert.equal(await info.getAttribute('aria-label'),full.join(' · '));
              assert.deepEqual(await info.locator('.fc-seat-count').allTextContents(),compact);
              for(const tag of await info.locator('.fc-seat-count').evaluateAll(nodes=>nodes.map(n=>{
                const r=n.getBoundingClientRect(),p=n.parentElement.getBoundingClientRect();
                return {height:r.height,line:parseFloat(getComputedStyle(n).lineHeight),left:r.left,right:r.right,parentLeft:p.left,parentRight:p.right};
              }))) {
                assert.ok(tag.height<=tag.line+1,'each side count is one readable short label');
                assert.ok(tag.left>=tag.parentLeft-1 && tag.right<=tag.parentRight+1,'short counts fit the identity rail');
              }
            } else assert.equal(await info.innerText(),full.join(' · '),'north/south counts unchanged');
            const visibleIds = await badge.locator('[data-card-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.cardId));
            const expectedIds = [...(state.melds[p.player_id] || []).flatMap(g=>g.cards), ...(state.broken?.[p.player_id] || []), ...(state.listening?.[p.player_id] || [])];
            assert.deepEqual(visibleIds.sort(),expectedIds.sort(),'each seat only displays its own public projection');
          }
          const publicIds = [...new Set([...state.discards, ...(state.offer ? [state.offer.card] : [])])];
          for (const pid of state.participant_order) {
            publicIds.push(...(state.melds[pid] || []).flatMap(g=>g.cards), ...(state.broken?.[pid] || []), ...(state.listening?.[pid] || []));
          }
          const rendered = await page.locator('.fc-table [data-card-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.cardId));
          assert.deepEqual(rendered.sort(),publicIds.sort(),'table only renders server-public cards');
          const pile=page.locator('.fc-discards');
          assert.equal(await pile.count(),1);
          assert.equal(await page.locator('.fc-table-center > .fc-discards').count(),1);
          assert.equal(await page.locator('details.fc-discards').count(),0);
          const pileIds=await pile.locator('[data-card-id]').evaluateAll(nodes=>nodes.map(n=>n.dataset.cardId));
          assert.equal(pileIds.length,new Set(pileIds).size,'offer and history never repeat a physical card');
          assert.equal(await pile.locator('.fc-card-current').count(),state.offer || state.discards.length ? 1 : 0);
          if(state.offer) assert.equal(await pile.locator('.fc-offer .fc-meta').innerText(),state.offer.from_deck?'公开翻牌':'当前弃牌');
          else {
            assert.equal(await pile.locator('.fc-offer').count(),0);
            if(state.discards.length) assert.equal(await pile.locator('.fc-card-current').getAttribute('data-card-id'),state.discards.at(-1));
          }
          const placements=await page.locator('.fc-seat').evaluateAll(nodes=>nodes.map(seat=>{
            const rect=n=>{if(!n)return null; const r=n.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
            const publicArea=seat.querySelector('.fc-seat-public');
            return {position:seat.dataset.position,seat:rect(seat),identity:rect(seat.querySelector('.fc-seat-identity')),
              avatar:rect(seat.querySelector('.room-participant-avatar')),publicArea:rect(publicArea),
              faces:[...seat.querySelectorAll('.fc-meld:first-child .fc-card')].map(n=>({...rect(n),transform:getComputedStyle(n).transform}))};
          }));
          for(const p of placements) {
            const {identity:i,publicArea:a,seat,avatar}=p;
            assert.ok(i.x>=seat.x && i.right<=seat.right+1 && i.y>=seat.y && i.bottom<=seat.bottom+1,'identity contained by its own seat');
            assert.ok(avatar.width>=20 && avatar.height>=20,'avatar retains its intended size');
            if(['north','south'].includes(p.position)) {
              assert.ok(Math.abs((i.x+i.right)-(seat.x+seat.right))<2,'north/south identity centered on its own edge');
              if(a) assert.ok(p.position==='north'? i.bottom<=a.y+1:i.y>=a.bottom-1,'identity on the outer rail, clear of faces');
              for(const face of p.faces) assert.ok(face.height>face.width,'top/bottom faces stay upright');
            } else {
              if(a) assert.ok(p.position==='west'?i.right<=a.x+1:i.x>=a.right-1,'side identity outside public column');
              p.faces.forEach((face,index)=>{
                assert.ok(face.width>face.height,'side face lies across the table edge');
                assert.ok(face.transform.includes(p.position==='west'?'0, 1, -1, 0':'0, -1, 1, 0'),'opposite side orientations');
                if(index) assert.ok(face.y>=p.faces[index-1].bottom-1 && Math.abs(face.x-p.faces[index-1].x)<1,'each meld is a vertical sequence');
              });
            }
          }
          const ys = await page.locator('.fc-hand button').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().y));
          assert.ok(new Set(ys).size<=1,'hand always occupies one row');
          const layout = await page.evaluate(() => {
            const rect = n => {const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
            return {
              table:rect(document.querySelector('.fc-table')),
              center:rect(document.querySelector('.fc-table-center')),
              stock:rect(document.querySelector('.fc-stock')),
              discards:rect(document.querySelector('.fc-discards')),
              turn:rect(document.querySelector('.fc-turn')),
              seats:[...document.querySelectorAll('.fc-seat')].map(n=>({position:n.dataset.position,...rect(n)})),
              names:[...document.querySelectorAll('.fc-seat-name')].map(n=>({height:rect(n).height,width:rect(n).width,vertical:getComputedStyle(n).writingMode==='vertical-rl',lineHeight:parseFloat(getComputedStyle(n).lineHeight)})),
              historyPosition:getComputedStyle(document.querySelector('#historyDrawerTab')).position,
            };
          });
          assert.equal(layout.historyPosition,'static','history tab cannot float over the table or hand');
          assert.ok(layout.center.width>=100,'side seats preserve a usable center');
          assert.equal(layout.center.height,204,'center height stays fixed');
          const empty=!state.discards.length && !state.offer?.card;
          assert.ok(empty?layout.discards.height<=55:layout.discards.height>=100,'only empty history collapses into a small box');
          if(empty) assert.ok(layout.discards.width<=110,'empty box stays compact on desktop too');
          assert.ok(layout.stock.bottom<=layout.discards.y && layout.discards.bottom<=layout.turn.y,'stock, discard history and turn have separate space');
          assert.ok(layout.turn.bottom<=layout.center.bottom+1,'turn never leaves center');
          const south=layout.seats.find(s=>s.position==='south');
          assert.ok(south.y>=layout.center.bottom-1,'viewer sits below the center');
          for(const s of layout.seats) {
            if(s.position==='north') assert.ok(s.bottom<=layout.center.y+1,'opposite seats stay above center');
            if(['west','east'].includes(s.position)) {
              if(width<=600) assert.equal(s.width,88,'phone side width unchanged');
              else assert.ok(s.width>88 && s.width<=132,'desktop sides widen within bounds');
            }
            if(s.position==='west') assert.ok(s.right<=layout.center.x+1,'left player stays left of center');
            if(s.position==='east') assert.ok(s.x>=layout.center.right-1,'right player stays right of center');
            assert.ok(s.x>=layout.table.x && s.right<=layout.table.right,'seat stays inside table');
          }
          layout.names.forEach(n=>assert.ok((n.vertical?n.width:n.height)<=n.lineHeight+1,'long names stay on one line'));
          if(['opening','public-groups','2-seats','3-seats','five-seats','five-public-groups','3-public-groups'].includes(name)) {
            await page.evaluate(()=>scrollTo(0,0));
            const rack=await page.locator('.fc-hand').boundingBox();
            const control=await page.locator('.fc-controls').boundingBox();
            firstViewport.push({scene:name,tableHeight:layout.table.height,handBottom:rack.y+rack.height,actionsBottom:control.y+control.height});
            assert.ok(rack.y+rack.height<=page.viewportSize().height,`${name}: hand visible in first viewport`);
            assert.ok(control.y+control.height<=page.viewportSize().height,`${name}: actions visible in first viewport (${control.y+control.height} > ${page.viewportSize().height})`);
          }
        }
        await (target || page).screenshot({path:path.join(out,`${mode}-${name}-${width}.png`),...(target?{}:{fullPage:true})});
      }
      async function open(kind, invited = false, count = 4) {
        fixture = backend({action:'seed',game_type:gameType,fixture:kind,count,invited});
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
      for (const [rule, upper] of [['tenhu',850],['jiuzhi',750],['qunhu',700]]) {
        await page.locator(`#gameTypeRulePicker input[value="${rule}"]`).check();
        assert.equal(await page.locator('#gameTokenEstimate').innerText(),`（约100–${upper} token/轮）`);
        assert.match(await page.locator('#gameTokenEstimate').getAttribute('title'),/cl100k_base/);
      }
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
        assert.ok(face.targetWidth>=24 && face.targetHeight>=44,JSON.stringify(face));
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
      }),true,'selected target must be unobscured');
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
      for(const ratio of await page.locator('.fc-offer .fc-card, .fc-groups .fc-card').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return Math.max(r.height,r.width)/Math.min(r.height,r.width);}))) assert.ok(ratio>=2.5&&ratio<=2.7);
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
      assert.equal(await page.locator('.fc-table-center > .fc-discards').count(),1);
      assert.equal(await page.locator('details.fc-discards').count(),0);
      assert.ok((await pile.locator('.fc-discard-history').boundingBox()).height<=150);
      await screenshot('discards',pile);
      if (mode === 'tenhu') {
        for (const count of [2,3]) {
          await open('opening',false,count);
          await screenshot(`${count}-seats`);
          assert.equal(await page.locator('.fc-seat').count(),count);
        }
      }
      if (mode === 'qunhu') { await open('opening',false,5); await screenshot('five-seats'); }
      // Dense public groups and long names on the actual page, without changing rules or a live room.
      await open('opening');
      fixture.room.participants[1].display_name = '名字较长的四色牌玩家';
      if(mode==='jiuzhi') {
        fixture.room.board_state.closed_pair_counts.p1=1;
        fixture.room.board_state.hand_counts.p1=20+(fixture.room.board_state.broken?.p1?.length||0);
      }
      fixture.room.board_state.participant_order.forEach((pid,i)=>{
        const c='RYGW'[i];
        fixture.room.board_state.melds[pid]=[
          {cards:[`${c}0-1`,`${c}1-1`,`${c}2-1`]},
          {cards:[`${c}3-1`,`${c}4-1`,`${c}5-1`]},
          {cards:[`${c}6-1`,`${c}6-2`,`${c}6-3`,`${c}6-4`]},
        ];
      });
      await page.reload(); await page.locator('.fc-meld').first().waitFor(); await dismiss();
      await screenshot('public-groups');
      const seats = await page.locator('.fc-seat').evaluateAll(nodes=>Object.fromEntries(nodes.map(n=>{
        const r=n.getBoundingClientRect(); return [n.dataset.playerId,{x:r.x,y:r.y}];
      })));
      assert.ok(seats.p3.x<seats.p1.x, 'opponents occupy left and right sides');
      assert.ok(seats.p2.y<seats.p3.y && seats.p2.y<seats.p1.y, 'opposite player sits above both side players');
      assert.ok(seats.p0.y>seats.p3.y && seats.p0.y>seats.p1.y, 'viewer sits below both side players');
      for (const glyph of await page.locator('.fc-meld .fc-card > strong:first-child').all()) {
        await glyph.scrollIntoViewIfNeeded();
        assert.equal(await glyph.evaluate(n=>{
          const r=n.getBoundingClientRect(), face=n.parentElement;
          return [[1,1],[r.width-1,1],[1,r.height-1],[r.width-1,r.height-1]].every(([x,y])=>face.contains(document.elementFromPoint(r.x+x,r.y+y)));
        }),true,'every public card keeps its top character unobscured');
      }
      // Pending discard, deck offer, settled history, empty, and overlapping projections.
      const highDiscards=Array.from({length:100},(_,i)=>`${'RYGW'[Math.floor(i/28)]}${Math.floor(i/4)%7}-${i%4+1}`);
      let historyGeometry;
      for(const [scene,discards,offer] of [
        ['history-empty',[],null], ['history-only',highDiscards,null],
        ['discard-offer',highDiscards,{card:'W6-4',from_deck:false}],
        ['deck-offer',highDiscards,{card:'W6-4',from_deck:true}],
        ['overlapping-offer',highDiscards,{card:highDiscards.at(-1),from_deck:false}],
      ]) {
        Object.assign(fixture.room.board_state,{discards,offer});
        await page.reload(); await page.locator('.fc-discards').waitFor(); await dismiss();
        await screenshot(scene);
        const geometry=await page.evaluate(()=>{
          const table=document.querySelector('.fc-table').getBoundingClientRect();
          return [table.height,...['.fc-stock','.fc-turn','.fc-hand'].map(selector=>document.querySelector(selector).getBoundingClientRect().top-table.top)];
        });
        if(historyGeometry) geometry.forEach((value,i)=>assert.ok(Math.abs(value-historyGeometry[i])<=1,'empty/full history leaves table, stock, turn and hand stationary'));
        else historyGeometry=geometry;
        if(discards.length) {
          const history=page.locator('.fc-discard-history');
          assert.ok(await history.evaluate(n=>n.scrollHeight>n.clientHeight),'large history scrolls within fixed center');
          await history.focus(); await page.keyboard.press('Home');
          await history.evaluate(n=>n.scrollTop=0);
          await history.locator('.fc-card').first().scrollIntoViewIfNeeded();
          assert.equal(await history.locator('.fc-card').first().evaluate(n=>{
            const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
          }),true,'oldest discard remains reachable');
          const saved=await page.locator('.fc-table').boundingBox();
          await history.locator('.fc-card').last().scrollIntoViewIfNeeded();
          assert.equal((await page.locator('.fc-table').boundingBox()).height,saved.height,'scrolling never expands table');
        }
      }
      if(mode!=='jiuzhi') {
        const dense=fixture.room.board_state.melds;
        await open('opening',false,3);
        fixture.room.board_state.melds=dense;
        fixture.room.board_state.discards=highDiscards;
        await page.reload(); await page.locator('.fc-meld').first().waitFor(); await dismiss();
        await screenshot('3-public-groups');
      }
      // History remains reachable using the original drawer, without a floating tab.
      await page.locator('#historyDrawerTab').click();
      await page.locator('#historyDrawer.show').waitFor();
      await page.locator('#closeHistoryButton').click();
      assert.equal(await page.locator('#historyDrawerTab').getAttribute('aria-expanded'),'false');
      if (mode==='qunhu') {
        await open('opening',false,5);
        fixture.room.participants.forEach((p,i)=>{p.display_name=`长昵称玩家${i}正在牌桌旁`;});
        fixture.room.board_state.participant_order.forEach((pid,i)=>{
          const c='RYGW'[i%4];
          fixture.room.board_state.melds[pid]=[
            {cards:[`${c}0-1`,`${c}1-1`,`${c}2-1`]},
            {cards:[`${c}3-1`,`${c}4-1`,`${c}5-1`]},
            {cards:[`${c}6-1`,`${c}6-2`,`${c}6-3`,`${c}6-4`]},
          ];
        });
        fixture.room.board_state.discards=highDiscards;
        fixture.room.board_state.offer={card:'W6-4',from_deck:true};
        await page.reload(); await page.locator('.fc-discards').waitFor(); await dismiss();
        await screenshot('five-public-groups');
        assert.ok(await page.locator('.fc-discard-history').evaluate(n=>n.scrollHeight>n.clientHeight));
        const north=await page.locator('.fc-far-edge > .fc-seat').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right};}));
        assert.ok(north[0].right<north[1].left,'five-player far seats remain distinct');
        assert.equal(await page.locator('.fc-far-edge > .fc-seat').count(),2);
        for (const seat of await page.locator('.fc-seat').all()) {
          const publicArea=seat.locator('.fc-seat-public');
          if (width<500 && ['east','west'].includes(await seat.getAttribute('data-position'))) {
            assert.ok(await publicArea.evaluate(n=>n.scrollHeight>n.clientHeight),'dense side groups scroll inside their own seat');
          }
          for (const glyph of await seat.locator('.fc-meld .fc-card > strong:first-child').all()) {
            await glyph.scrollIntoViewIfNeeded();
            assert.equal(await glyph.evaluate(n=>{
              const r=n.getBoundingClientRect();
              return [[1,1],[r.width-1,1],[1,r.height-1],[r.width-1,r.height-1]].every(([x,y])=>n.parentElement.contains(document.elementFromPoint(r.x+x,r.y+y)));
            }),true,'every five-player public glyph remains reachable and unobscured');
          }
        }
      }
      assert.deepEqual(errors,[]);
      metrics.push({mode,width,ruleCards:rects,hand:geometry,submitHeight:buttonBox.height,firstViewport,consoleErrors:errors});
      fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(metrics,null,2));
      console.log(JSON.stringify({mode,width,status:"passed"}));
      await page.close();
    }
    assert.ok(metrics.length,'FOUR_COLOR_CASE must match a mode:width case');
    fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(metrics,null,2));
    console.log(JSON.stringify({passed:true,viewports:metrics.map(m=>m.width),screenshots:out}));
  } finally { await browser?.close(); fs.rmSync(tmp,{recursive:true,force:true}); }
})().catch(error => {console.error(error);process.exitCode=1;});
