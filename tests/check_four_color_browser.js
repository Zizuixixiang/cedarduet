/* Real page + real engine in disposable SQLite. All network intercepted.
   FOUR_COLOR_RING_ONLY adds explicitly visual-only pressure projections;
   those projections are never submitted as engine moves or persisted to a DB. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../../..');
const out = process.env.FOUR_COLOR_ARTIFACTS || '/tmp/cedartoy-four-color-ui';
fs.mkdirSync(out, {recursive:true});
fs.rmSync(path.join(out,'measurements.json'),{force:true});
const tmp = fs.mkdtempSync(path.join(out, 'db-'));
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const env = {...process.env, PYTHONPATH:path.join(root,'vendor/duel'), PYTHONDONTWRITEBYTECODE:'1'};
let sequence = 0;
function backend(input) {
  if (input.action === 'seed') env.DUEL_DB_PATH = path.join(tmp, `${++sequence}.db`);
  const r = spawnSync(path.join(root,'vendor/duel/.venv/bin/python'), [path.join(__dirname,'four_color_ui_fixture.py')],
    {env, cwd:root, input:JSON.stringify(input), encoding:'utf8', timeout:45000});
  assert.equal(r.status, 0, r.stderr || String(r.error || r.signal || 'fixture failed')); return JSON.parse(r.stdout);
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
      const screenshots = [];
      const cornerBaselines = new Map(), geometryEvidence = [];
      let fiveOpeningIdentities;
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
        // Save the real page even when a subsequent geometry assertion fails.
        await (target || page).screenshot({path:path.join(out,`${mode}-${name}-${width}.png`),...(target?{}:{fullPage:true})});
        screenshots.push(path.join(out,`${mode}-${name}-${width}.png`));
        fs.writeFileSync(path.join(out,'screenshots.json'),JSON.stringify(screenshots,null,2));
        if (await page.locator('.fc-table').isVisible()) {
          const state = fixture.room.board_state;
          assert.equal(await page.locator('#roomParticipants').isVisible(),false,'embedded seats replace the generic roster');
          assert.equal(await page.locator('.fc-seat').count(),state.participant_order.length);
          for (const p of fixture.room.participants) {
            const badge = page.locator(`.fc-seat[data-player-id="${p.player_id}"]`);
            assert.equal(await badge.count(),1,'one table seat per player');
            const shownInHand = new Set([...(state.broken?.[p.player_id] || []), ...(state.listening?.[p.player_id] || [])]).size;
            const info=badge.locator('.fc-seat-counts');
            const hidden=Math.max(0,state.hand_counts[p.player_id]-shownInHand);
            const full=[`暗牌 ${hidden} 张`], compact=[`暗${hidden}`];
            const pairs=state.closed_pair_counts?.[p.player_id];
            if(Number.isInteger(pairs)) {full.push(`扣置 ${pairs} 对`);compact.push(`扣${pairs}`);}
            const points = state.result?.point_deltas?.[p.player_id];
            assert.equal(await badge.evaluate(n=>n.classList.contains('fc-seat-current')),fixture.room.status==='playing' && p.player_id===fixture.room.current_player_id,'only the acting identity is marked');
            if(Number.isInteger(points)) {
              const score=`${points>0?'+':''}${points}`;
              full.push(`${score} 分`); compact.push(score);
            }
            {
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
            }
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
          const pile=page.locator('.fc-table-center, .fc-history');
          assert.equal(await page.locator('.fc-discards').count(),1);
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
          for(const history of await page.locator('.fc-history').all()) {
            const owner=await history.getAttribute('data-player-id');
            const ids=await history.locator('[data-card-id]').evaluateAll(ns=>ns.map(n=>n.dataset.cardId));
            assert.ok(ids.every(id=>state.discard_sources?.[id]===owner),'only known player discards enter that inner edge');
            const seat=page.locator(`.fc-seat[data-player-id="${owner}"]`);
            assert.equal(await history.getAttribute('data-position'),await seat.getAttribute('data-position'));
          }
          const placements=await page.locator('.fc-seat').evaluateAll(nodes=>nodes.map(seat=>{
            const rect=n=>{const r=n.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
            return {position:seat.dataset.position,seat:rect(seat),identity:rect(seat.querySelector('.fc-seat-identity')),
              avatar:rect(seat.querySelector('.room-participant-avatar')),style:{border:getComputedStyle(seat).borderWidth,background:getComputedStyle(seat).backgroundColor},
              faces:[...seat.querySelectorAll('.fc-meld:first-child .fc-card')].map(rect)};
          }));
          assert.equal(new Set(placements.map(p=>p.position)).size,state.participant_order.length,'each player has an independent station');
          assert.equal(await page.locator('.fc-far-edge').count(),0);
          for(const p of placements) {
            const {identity:i,seat,avatar}=p;
            assert.ok(avatar.width>=20 && avatar.height>=20,'avatar retains its intended size');
            assert.equal(p.style.border,'0px','no whole-seat border');
            assert.equal(p.style.background,'rgba(0, 0, 0, 0)','no whole-seat background');
            p.faces.forEach((face,index)=>{
              const side=/^(east|west)/.test(p.position);
              assert.ok(side?face.width>face.height:face.height>face.width,'faces follow their table edge');
              if(index) {
                const previous=p.faces[index-1];
                const advance=side?Math.abs(face.x-previous.x):face.y-previous.y;
                assert.ok(advance>=15 && advance<(side?face.width:face.height),'cards overlap along their long axis inside each bundle');
              }
            });
          }
          const corners=await page.locator('.fc-seat').evaluateAll(ns=>Object.fromEntries(ns.map(n=>{
            const t=n.closest('.fc-table').getBoundingClientRect(), a=n.querySelector('.room-participant-avatar').getBoundingClientRect();
            return [n.dataset.position,{x:a.x+a.width/2-t.x,y:a.y+a.height/2-t.y}];
          })));
          const pair=(a,b,axis)=>assert.ok(Math.abs(corners[a][axis]-corners[b][axis])<=1,`${name}: avatar ${a}/${b} ${axis} corners align`);
          const count=state.participant_order.length;
          if(count===4) { pair('north','east','y'); pair('west','south','y'); pair('north','west','x'); pair('east','south','x'); }
          if(count===5) {
            pair('north-west','north-east','y'); pair('west','east','y');
            assert.ok(corners.west.y>corners['north-west'].y+100 && corners.south.y>corners.west.y+100,'five seats occupy upper, side and bottom levels');
            assert.ok(corners['north-west'].x>corners.west.x && corners['north-east'].x<corners.east.x,'upper shoulders are inset from side seats');
          }
          if(cornerBaselines.has(count)) assert.deepEqual(corners,cornerBaselines.get(count),'avatar centres remain fixed across names, actors and public cards');
          else cornerBaselines.set(count,corners);
          const rails=await page.locator('.fc-discard-history').evaluateAll(ns=>ns.map(n=>({axis:n.dataset.axis,key:n.dataset.historyKey,diagonal:getComputedStyle(n).transform!=='none',
            cards:[...n.querySelectorAll('.fc-card')].map(c=>{const r=c.getBoundingClientRect(),h=c.querySelector('strong').getBoundingClientRect(),slot=c.closest('.fc-history-card')||c; return {x:r.x,y:r.y,w:r.width,h:r.height,localX:slot.offsetLeft,localY:slot.offsetTop,localWidth:slot.offsetWidth,head:{x:h.x,y:h.y,w:h.width,h:h.height}};})})));
          for(const rail of rails) for(let i=0;i<rail.cards.length;i++) {
            const c=rail.cards[i],horizontal=rail.axis==='x';
            if(rail.diagonal) {
              if(i) {
                const previous=rail.cards[i-1],advance=c.localX-previous.localX;
                assert.equal(c.localY,previous.localY,'diagonal history is one local row');
                assert.ok(advance>=14 && advance<c.localWidth,'diagonal cards retain readable overlapping heads');
              }
              continue;
            }
            assert.ok(horizontal?c.w>c.h:c.h>c.w,'long edge follows the strip');
            if(!i) continue;
            const prev=rail.cards[i-1],advance=horizontal?c.x-prev.x:c.y-prev.y;
            assert.ok(Math.abs(horizontal?c.y-prev.y:c.x-prev.x)<=1,'history is exactly one row or column');
            assert.ok(advance>0 && advance<(horizontal?c.w:c.h),'history overlaps along its only axis');
            assert.ok(horizontal?prev.head.x+prev.head.w<=c.x+1:prev.head.y+prev.head.h<=c.y+1,'next card never covers the identifying head');
          }
          geometryEvidence.push({scene:name,corners,rails});
          fs.writeFileSync(path.join(out,'geometry.json'),JSON.stringify(geometryEvidence,null,2));
          const collisions=await page.locator('.fc-table').evaluate(table=>{
            const rect=n=>{const r=n.getBoundingClientRect();return {l:r.left,t:r.top,r:r.right,b:r.bottom};};
            const overlaps=(a,b)=>Math.min(a.r,b.r)-Math.max(a.l,b.l)>.5 && Math.min(a.b,b.b)-Math.max(a.t,b.t)>.5;
            const polygonOverlaps=(a,b)=>{
              const corners=[{x:b.l,y:b.t},{x:b.r,y:b.t},{x:b.r,y:b.b},{x:b.l,y:b.b}];
              for(const shape of [a,corners]) for(let i=0;i<shape.length;i++) {
                const p=shape[i],q=shape[(i+1)%shape.length],length=Math.hypot(q.x-p.x,q.y-p.y);
                const axis={x:(p.y-q.y)/length,y:(q.x-p.x)/length};
                const u=a.map(p=>p.x*axis.x+p.y*axis.y),v=corners.map(p=>p.x*axis.x+p.y*axis.y);
                if(Math.min(Math.max(...u),Math.max(...v))-Math.max(Math.min(...u),Math.min(...v))<=.5)return false;
              }
              return true;
            };
            const fixed=[...table.querySelectorAll('.room-participant-avatar,.fc-seat-name,.fc-seat-count,.fc-dealer,.fc-turn-indicator,.fc-card-back,.fc-offer .fc-card,.fc-offer > .fc-meta')].map(n=>({box:rect(n),name:n.className}));
            const bad=[];
            fixed.forEach((a,i)=>fixed.slice(i+1).forEach(b=>{if(overlaps(a.box,b.box))bad.push([a.name,b.name]);}));
            for(const n of table.querySelectorAll('.fc-seat-public .fc-card,.fc-discard-history .fc-card')) {
              const port=n.closest('.fc-seat-public,.fc-discard-history'),a=rect(n),clip=rect(port);
              if(getComputedStyle(port).transform!=='none') {
                // Intersect in the rail's local coordinates before rotating.
                // Axis-aligned bounds include empty triangular corners and
                // even offscreen cards, which are not painted over an identity.
                const slot=n.closest('.fc-history-card'),m=new DOMMatrix(getComputedStyle(port).transform);
                const l=Math.max(0,slot.offsetLeft-port.scrollLeft),t=Math.max(0,slot.offsetTop-port.scrollTop);
                const r=Math.min(port.clientWidth,slot.offsetLeft-port.scrollLeft+slot.offsetWidth),b=Math.min(port.clientHeight,slot.offsetTop-port.scrollTop+slot.offsetHeight);
                if(r<=l||b<=t)continue;
                const polygon=[[l,t],[r,t],[r,b],[l,b]].map(([x,y])=>{
                  x-=port.clientWidth/2;y-=port.clientHeight/2;
                  return {x:(clip.l+clip.r)/2+m.a*x+m.c*y,y:(clip.t+clip.b)/2+m.b*x+m.d*y};
                });
                for(const fixedBox of fixed)if(polygonOverlaps(polygon,fixedBox.box))bad.push([n.dataset.cardId,fixedBox.name]);
                continue;
              }
              const visible={l:Math.max(a.l,clip.l),t:Math.max(a.t,clip.t),r:Math.min(a.r,clip.r),b:Math.min(a.b,clip.b)};
              if(visible.r<=visible.l||visible.b<=visible.t)continue;
              for(const b of fixed)if(overlaps(visible,b.box))bad.push([n.dataset.cardId,b.name]);
            }
            return bad;
          });
          assert.deepEqual(collisions,[],`${name}: identities, badges, stock and offer must not cover cards or each other`);
          const acting=page.locator('.fc-turn-indicator');
          assert.equal(await acting.count(),fixture.room.status==='playing'?1:0);
          if(await acting.count()) {
            assert.equal(await acting.innerText(),'▸ 行动中');
            assert.ok(await acting.evaluate(n=>{
              const s=getComputedStyle(n),r=n.getBoundingClientRect();
              return s.whiteSpace==='nowrap' && r.height<20 && s.backgroundColor!=='rgba(0, 0, 0, 0)';
            }),'turn badge is a compact solid horizontal label');
          }
          assert.equal(await page.locator('.fc-table-center').evaluate(n=>/暂无弃牌|弃牌区 ·/.test(n.textContent)),false);
          for(const selector of ['.fc-discards','.fc-offer','.fc-table-center']) {
            for(const style of await page.locator(selector).evaluateAll(nodes=>nodes.map(n=>({border:getComputedStyle(n).borderWidth,background:getComputedStyle(n).backgroundColor})))) {
              assert.equal(style.border,'0px','no panel or separator borders');
              assert.equal(style.background,'rgba(0, 0, 0, 0)');
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
          assert.ok(layout.center.width>=54,'side seats preserve a usable center');
          assert.ok(layout.turn.bottom<=layout.center.bottom+1,'turn never leaves center');
          for(const identity of await page.locator('.fc-seat-identity').all()) assert.ok(await identity.evaluate(n=>{const r=n.getBoundingClientRect(),t=n.closest('.fc-table').getBoundingClientRect();return r.left>=t.left&&r.right<=t.right&&r.top>=t.top&&r.bottom<=t.bottom;}),'identity stays inside table');
          layout.names.forEach(n=>assert.ok(!n.vertical && n.height<=n.lineHeight+1,'long names stay horizontal on one line'));
          if(['opening','public-groups','2-seats','3-seats','five-seats','five-public-groups','3-public-groups'].includes(name)) {
            await page.evaluate(()=>scrollTo(0,0));
            const rack=await page.locator('.fc-hand').boundingBox();
            const control=await page.locator('.fc-controls').boundingBox();
            firstViewport.push({scene:name,tableHeight:layout.table.height,handBottom:rack.y+rack.height,actionsBottom:control.y+control.height});
            if(state.participant_order.length!==5) {
              assert.ok(rack.y+rack.height<=page.viewportSize().height,`${name}: hand visible in first viewport`);
              assert.ok(control.y+control.height<=page.viewportSize().height,`${name}: actions visible in first viewport (${control.y+control.height} > ${page.viewportSize().height})`);
            } else {
              // Five independent stations use a taller table; keep full-size
              // hand/action targets reachable through ordinary page scrolling.
              for(const selector of ['.fc-hand','.fc-controls']) {
                await page.locator(selector).scrollIntoViewIfNeeded();
                const r=await page.locator(selector).boundingBox();
                assert.ok(r.y>=0 && r.y+r.height<=page.viewportSize().height,`${selector} scrolls fully into view`);
              }
            }
          }
        }
      }
      async function open(kind, invited = false, count = 4) {
        fixture = backend({action:'seed',game_type:gameType,fixture:kind,count,invited});
        await page.goto(`http://four-color.test/?room=${fixture.room.room_id}`);
        await page.locator('.fc-hand').waitFor(); await dismiss();
      }
      async function identityPositions() {
        return page.locator('.fc-seat-identity').evaluateAll(nodes=>nodes.map(n=>{
          const r=n.getBoundingClientRect(), table=n.closest('.fc-table').getBoundingClientRect();
          return [r.x-table.x,r.y-table.y,r.width,r.height];
        }));
      }
      async function checkRailHeads() {
        const hidden=await page.locator('.fc-discard-history .fc-card > strong:first-child').evaluateAll(async heads=>{
          const bad=[];
          for(const n of heads) {
            n.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
            await new Promise(requestAnimationFrame);
            const r=n.getBoundingClientRect();
            const diagonal=getComputedStyle(n.closest('.fc-discard-history')).transform!=='none';
            for(const [x,y] of diagonal?[[r.width/2,r.height/2]]:[[1,1],[r.width-1,r.height-1]]) {
              const hit=document.elementFromPoint(r.x+x,r.y+y);
              if(!n.contains(hit))bad.push({card:n.parentElement.dataset.cardId,rail:n.closest('.fc-discard-history').dataset.historyKey,hit:hit?.outerHTML.slice(0,160)});
            }
          }
          return bad;
        });
        assert.deepEqual(hidden,[],'every history head is readable and reachable on its strip');
      }
      async function checkTurns(prefix) {
        const state=fixture.room.board_state;
        const original=fixture.room.current_player_id;
        let stable;
        for(const pid of state.participant_order) {
          fixture.room.current_player_id=pid;
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await page.evaluate(()=>scrollTo(0,0));
          const position=await page.locator(`.fc-seat[data-player-id="${pid}"]`).getAttribute('data-position');
          await screenshot(`${prefix}-turn-${position}`);
          const geometry=await identityPositions();
          if(stable) assert.deepEqual(geometry,stable,'changing actor never moves identities');
          else stable=geometry;
          const badgeHits=await page.locator('.fc-turn-indicator').evaluate(n=>{
            const r=n.getBoundingClientRect();
            // Probe inside the existing 3px rounded corners, not transparent pixels.
            return [[2,2],[r.width-2,2],[2,r.height-2],[r.width-2,r.height-2]].map(([x,y])=>{const hit=document.elementFromPoint(r.x+x,r.y+y); return {visible:n.contains(hit),hit:hit?.outerHTML.slice(0,200),x:r.x+x,y:r.y+y};});
          });
          assert.ok(badgeHits.every(h=>h.visible),`entire action badge is visible and unobscured: ${JSON.stringify(badgeHits)}`);
        }
        fixture.room.current_player_id=original;
        await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
      }
      if(process.env.FOUR_COLOR_REVIEW_ONLY || process.env.FOUR_COLOR_RING_ONLY) {
        const ringOnly=Boolean(process.env.FOUR_COLOR_RING_ONLY);
        const count=mode==='qunhu'?5:4;
        async function refresh() {await page.reload();await page.locator('.fc-table').waitFor();await dismiss();}
        async function completeCards() {
          const failures=await page.locator('.fc-seat-public, .fc-discard-history').evaluateAll(async clips=>{
            const bad=[];
            for(const clip of clips) {
              for(const face of clip.querySelectorAll('.fc-card')) {
                const slot=face.closest('.fc-side-card, .fc-history-card')||face;
                let x=slot.offsetLeft,y=slot.offsetTop,p=slot.offsetParent;
                while(p && p!==clip) {x+=p.offsetLeft;y+=p.offsetTop;p=p.offsetParent;}
                if(!p) { // Neutral scrollports are static, without a rotated rail.
                  const r=slot.getBoundingClientRect(),c=clip.getBoundingClientRect();
                  x=r.left-c.left+clip.scrollLeft;y=r.top-c.top+clip.scrollTop;
                }
                const w=slot.offsetWidth,h=slot.offsetHeight;
                clip.scrollLeft=Math.max(0,x+w-clip.clientWidth+1);
                clip.scrollTop=Math.max(0,y+h-clip.clientHeight+1);
                await new Promise(requestAnimationFrame);
                if(x<clip.scrollLeft-.5 || x+w>clip.scrollLeft+clip.clientWidth+.5 || y<clip.scrollTop-.5 || y+h>clip.scrollTop+clip.clientHeight+.5)
                  bad.push({card:face.dataset.cardId,x,y,w,h,width:clip.clientWidth,height:clip.clientHeight,left:clip.scrollLeft,top:clip.scrollTop});
              }
            }
            return bad;
          });
          assert.deepEqual(failures,[],'every complete face can be reached, including rotated rails and public-group tails');
        }
        await open('midgame',false,count);
        fixture.room.participants.forEach((p,i)=>p.display_name=i===1?'Codex_杉星_0828':i===2?'南杉在牌桌旁的长中文昵称':`玩家${i}`);
        await refresh();
        if(!ringOnly) {await completeCards(); await screenshot(`review-${count}-midgame`);}
        const normal=structuredClone(fixture);
        if(!ringOnly) {
          await open('dense',false,count);
          assert.ok(fixture.replay.discards>25,'legal dense replay must significantly exceed 19 discards');
          if(count===5) assert.equal(new Set(Object.values(fixture.replay.player_discards)).size,5,'five players have different real discard counts');
          fs.writeFileSync(path.join(out,'legal-replay.json'),JSON.stringify(fixture.replay,null,2));
          await completeCards(); await screenshot(`review-${count}-legal-dense`);
          metrics.push({mode,width,replay:{...fixture.replay,moves:fixture.replay.moves.length}});
        }
        fixture=normal;
        const s=fixture.room.board_state;
        const privateIds=new Set([...fixture.room.private_state.hand.map(c=>c.id),...(fixture.room.private_state.closed_pairs||[]).flat()]);
        const available=Array.from({length:112},(_,i)=>`${'RYGW'[Math.floor(i/28)]}${Math.floor(i/4)%7}-${i%4+1}`).filter(id=>!privateIds.has(id));
        Object.assign(s,{melds:{},broken:{},listening:{},discards:[],discard_sources:{}});
        for(const pid of s.participant_order) s.melds[pid]=(ringOnly?[2,2,2]:[2,3,2,3,2]).map(n=>({cards:available.splice(0,n)}));
        s.broken[s.participant_order[1]]=available.splice(0,2);
        s.listening[s.participant_order[2]]=available.splice(0,2);
        for(const [i,pid] of s.participant_order.entries()) {
          const ids=available.splice(0,i+(ringOnly?8:2));s.discards.push(...ids);ids.forEach(id=>s.discard_sources[id]=pid);
        }
        s.discards.push(...available.splice(0,3)); // Legacy neutral history; no invented owner.
        s.offer={card:available.shift(),source:s.participant_order[1],from_deck:false};
        if(ringOnly) {
          assert.ok(s.offer.card,'pressure fixture has enough unique physical cards');
          fs.writeFileSync(path.join(out,'visual-pressure-fixture.json'),JSON.stringify({kind:'UI pressure only; not a legal action replay',fixture},null,2));
        }
        await refresh(); await completeCards(); await screenshot(`${ringOnly?'ring':'review'}-${count}-visual-stress`);
        if(ringOnly) {
          const geometry=await page.locator('.fc-table').evaluate(table=>{
            const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,cx:r.x+r.width/2,cy:r.y+r.height/2};};
            return {live:rect(table.querySelector('.fc-live-cards')),rails:[...table.querySelectorAll('.fc-history')].map(n=>({position:n.dataset.position,...rect(n),widthLocal:n.clientWidth,matrix:getComputedStyle(n).transform,cards:n.children.length}))};
          });
          if(count===5) {
            assert.equal(geometry.rails.length,5);
            const lengths=geometry.rails.map(r=>r.widthLocal);
            assert.ok(Math.max(...lengths)-Math.min(...lengths)<=1,'five equally long scrollports');
            const cx=geometry.rails.reduce((v,r)=>v+r.cx,0)/5,cy=geometry.rails.reduce((v,r)=>v+r.cy,0)/5;
            assert.ok(Math.abs(cx-geometry.live.cx)<1 && Math.abs(cy-geometry.live.cy)<1,'live cards at pentagon geometric centre');
            const radii=geometry.rails.map(r=>Math.hypot(r.cx-cx,r.cy-cy));
            assert.ok(Math.max(...radii)-Math.min(...radii)<1,'all five sides share one apothem');
            const angles=geometry.rails.map(r=>(Math.atan2(r.cy-cy,r.cx-cx)*180/Math.PI+360)%360).sort((a,b)=>a-b);
            angles.forEach((a,i)=>assert.ok(Math.abs((angles[(i+1)%5]-a+360)%360-72)<.1,'regular 72 degree intervals'));
            geometry.rails.forEach(r=>assert.ok(r.cards>=8 && r.cards<=12));
          } else {
            const west=geometry.rails.find(r=>r.position==='west'),south=geometry.rails.find(r=>r.position==='south'),north=geometry.rails.find(r=>r.position==='north');
            assert.ok(south.y-west.y-west.height>=12,'bottom-left rails have clear vertical separation');
            assert.ok(south.width>=north.width+15,'bottom rail extends further right');
          }
          fs.writeFileSync(path.join(out,'ring-geometry.json'),JSON.stringify(geometry,null,2));
          metrics.push({mode,width,kind:'visual-pressure',playerDiscards:geometry.rails.map(r=>({position:r.position,count:r.cards})),neutralDiscards:3});
          await checkRailHeads();
        }
        for(const selector of ['.fc-seat-public','.fc-discard-history']) {
          const clips=page.locator(selector);
          const index=await clips.evaluateAll(ns=>ns.findIndex(n=>n.scrollWidth>n.clientWidth || n.scrollHeight>n.clientHeight));
          if(width===360) assert.ok(index>=0,'phone stress fixture exercises local scrolling');
          if(index<0) continue;
          const clip=clips.nth(index),axis=await clip.evaluate(n=>{n.scrollLeft=0;n.scrollTop=0;return n.scrollWidth>n.clientWidth?'x':'y';});
          await clip.focus();await page.keyboard.press(axis==='x'?'ArrowRight':'ArrowDown');
          await page.waitForFunction(({selector,index,axis})=>{const n=document.querySelectorAll(selector)[index];return axis==='x'?n.scrollLeft>0:n.scrollTop>0;},{selector,index,axis},{timeout:5000});
        }
        const typography=await page.locator('.fc-card').evaluateAll(ns=>ns.map(n=>{
          const style=getComputedStyle(n,'::before');
          return {frame:parseFloat(style.borderTopWidth),inset:parseFloat(style.top),height:n.clientHeight,glyphs:[...n.querySelectorAll('strong')].map(g=>({top:g.offsetTop,bottom:g.offsetTop+g.offsetHeight,size:parseFloat(getComputedStyle(g).fontSize)}))};
        }));
        typography.forEach(t=>{assert.equal(t.frame,1);t.glyphs.forEach(g=>{assert.ok(g.top>=t.inset+t.frame && g.bottom<=t.height-t.inset-t.frame,`inner caps do not cross either glyph: ${JSON.stringify(t)}`);assert.ok(g.size>=13);});});
        if(count===4) {
          const alignment=await page.locator('.fc-history-north,.fc-history-south').evaluateAll(ns=>ns.map(n=>({left:n.getBoundingClientRect().left,first:n.firstElementChild.offsetLeft})));
          assert.equal(alignment[0].left,alignment[1].left);assert.equal(alignment[0].first,alignment[1].first,'same natural starting baseline');
        }
        const stable=await identityPositions();
        for(const pid of s.participant_order) {fixture.room.current_player_id=pid;await refresh();assert.deepEqual(await identityPositions(),stable);assert.equal(await page.locator('.fc-turn-indicator').count(),1);}
        s.discards=[];s.discard_sources={};s.offer=null;
        if(count===4) {
          s.broken={};s.listening={};
          Object.values(s.melds).forEach(groups=>groups.splice(4));
        }
        await refresh(); await screenshot(`${ringOnly?'ring':'review'}-${count}-groups-no-history`);
        if(count===4) for(const clip of await page.locator('.fc-position-north .fc-seat-public,.fc-position-south .fc-seat-public').all()) {
          assert.ok(await clip.evaluate(n=>n.scrollWidth===n.clientWidth),'four ordinary upper/lower bundles fit without clipping or scrolling');
        }
        assert.deepEqual(errors,[]);await page.close();continue;
      }
      if(process.env.FOUR_COLOR_FOLLOWUP_ONLY) {
        const count=mode==='qunhu'?5:4;
        await open('midgame',false,count);
        fixture.room.participants[1].display_name='Codex_杉星_0828';
        const s=fixture.room.board_state;
        const deck=Array.from({length:112},(_,i)=>`${'RYGW'[Math.floor(i/28)]}${Math.floor(i/4)%7}-${i%4+1}`);
        const privateIds=new Set([...fixture.room.private_state.hand.map(c=>c.id),...(fixture.room.private_state.closed_pairs||[]).flat()]);
        const used=new Set([...privateIds,...Object.values(s.melds).flatMap(gs=>gs.flatMap(g=>g.cards))]);
        const available=deck.filter(id=>!used.has(id));
        const green=available.filter(id=>id.startsWith('G')).slice(0,3);
        assert.equal(green.length,3);
        Object.assign(s,{broken:{},listening:{},offer:{card:available.find(id=>!green.includes(id)),source:s.participant_order[1],from_deck:false}});
        async function refresh() { await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss(); }
        async function fullFaces(pile, sparse=false) {
          const evidence=await pile.evaluate(async (n,sparse)=>{
            const faces=[...n.querySelectorAll('.fc-card')];
            const measure=()=>{
              const r=n.getBoundingClientRect();
              const rotated=getComputedStyle(n).transform!=='none';
              const bounds=rotated?{left:0,top:0,right:n.clientWidth,bottom:n.clientHeight}:{left:r.left+n.clientLeft,top:r.top+n.clientTop,right:r.left+n.clientLeft+n.clientWidth,bottom:r.top+n.clientTop+n.clientHeight};
              return {bounds,rotated,scrollWidth:n.scrollWidth,clientWidth:n.clientWidth,faces:faces.map(c=>{
                if(rotated) {
                  const slot=c.closest('.fc-history-card')||c,left=slot.offsetLeft-n.scrollLeft,top=slot.offsetTop-n.scrollTop;
                  return {id:c.dataset.cardId,left,right:left+slot.offsetWidth,top,bottom:top+slot.offsetHeight};
                }
                const f=c.getBoundingClientRect();return {id:c.dataset.cardId,left:f.left,right:f.right,top:f.top,bottom:f.bottom};
              })};
            };
            if(sparse) return [measure()];
            const ends=[];
            for(const end of [0,1]) {
              n.scrollLeft=end*n.scrollWidth; n.scrollTop=end*n.scrollHeight;
              await new Promise(requestAnimationFrame);
              const m=measure(); m.faces=[m.faces.at(end?-1:0)]; ends.push(m);
            }
            return ends;
          },sparse);
          for(const m of evidence) for(const f of m.faces) {
            assert.ok(f.left>=m.bounds.left-.5 && f.right<=m.bounds.right+.5 && f.top>=m.bounds.top-.5 && f.bottom<=m.bounds.bottom+.5,
              `complete card border inside scrollport: ${JSON.stringify({width,...m,face:f})}`);
          }
          return evidence;
        }
        for(const source of ['unknown','deck']) for(const n of [1,2,3]) {
          s.discards=green.slice(0,n); s.discard_sources=source==='deck'?Object.fromEntries(s.discards.map(id=>[id,null])):{};
          await refresh();
          const evidence=await fullFaces(page.locator('.fc-neutral-pile .fc-discard-history'),true);
          metrics.push({mode,width,source,cards:n,evidence});
          if((source==='unknown'&&n===2)||(source==='deck'&&n===3)) await screenshot(`followup-${count}-${source}-${n}`);
        }
        // Many neutral cards and real source-owned rails share the unchanged table.
        s.discards=available.filter(id=>id!==s.offer.card).slice(0,25);
        s.discard_sources=Object.fromEntries(s.discards.slice(0,count).map((id,i)=>[id,s.participant_order[i]]));
        await refresh();
        for(const pile of await page.locator('.fc-discard-history').all()) await fullFaces(pile);
        await screenshot(`followup-${count}-scroll-end`);
        await checkRailHeads();
        if(width===360 && mode==='tenhu') {
          Object.assign(s,{melds:{},discards:deck.slice(0,100),offer:{...s.offer,card:deck[111]},
            discard_sources:Object.fromEntries(deck.slice(0,40).map((id,i)=>[id,s.participant_order[Math.floor(i/10)]]))});
          fixture.room.private_state={hand:[],legal_actions:[],action_labels:{}};
          await refresh();
          for(const pile of await page.locator('.fc-discard-history').all()) await fullFaces(pile);
          await screenshot('followup-100-scroll-end');
          await checkRailHeads();
        }
        await page.goto('http://four-color.test/'); await page.locator('#lobbyView:not(.hidden)').waitFor();
        await page.selectOption('#gameCategory','card');
        for(const [select,counts] of [['gameType','targetPlayerCount'],['inviteGame','inviteCount']]) {
          if(select==='inviteGame') {await page.locator('#inviteCreateEntry').click();await page.selectOption('#inviteCategory','card');}
          assert.equal(await page.locator(`#${select} option[value="four_color_tenhu"]`).textContent(),'四色牌 / 2–5人');
          for(const [rule,expected] of [['tenhu',['2','3','4']],['jiuzhi',['4']],['qunhu',['3','4','5']]]) {
            await page.locator(`#${select}RulePicker input[value="${rule}"]`).check();
            assert.deepEqual(await page.locator(`#${counts} option`).evaluateAll(ns=>ns.map(n=>n.value)),expected,`${select}/${rule} legal counts only`);
          }
          if(width===360&&mode==='tenhu') await screenshot(`followup-${select}`,page.locator(select==='gameType'?'.create-panel':'#inviteDialog'));
        }
        assert.deepEqual(errors,[]);
        await page.close(); continue;
      }
      if(process.env.FOUR_COLOR_VISUAL_ONLY) {
        for(const count of (mode==='qunhu'?[4,5]:[4])) {
          if(process.env.FOUR_COLOR_COUNT && count!==Number(process.env.FOUR_COLOR_COUNT)) continue;
          await open('midgame',false,count);
          fixture.room.participants.forEach((p,i)=>{p.display_name=i===1?'Codex_杉星_0828':i===count-1?'南杉正在牌桌旁的长昵称':`玩家${i}`;});
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-normal-midgame`);
          const normal=structuredClone(fixture);
          // Presentation stress fixtures only; real midgame above comes from engine moves.
          const deck=Array.from({length:112},(_,i)=>`${'RYGW'[Math.floor(i/28)]}${Math.floor(i/4)%7}-${i%4+1}`);
          const privateIds=new Set([...fixture.room.private_state.hand.map(t=>t.id),...(fixture.room.private_state.closed_pairs||[]).flat()]);
          const available=deck.filter(id=>!privateIds.has(id));
          const s=fixture.room.board_state;
          s.broken && Object.keys(s.broken).forEach(pid=>s.broken[pid]=[]);
          s.listening && Object.keys(s.listening).forEach(pid=>s.listening[pid]=null);
          s.discards=[]; s.discard_sources={};
          for(const pid of s.participant_order) {
            s.melds[pid]=[2,3,4].map(n=>({cards:available.splice(0,n)}));
            const discarded=available.splice(0,2);
            s.discards.push(...discarded);
            discarded.forEach(id=>s.discard_sources[id]=pid);
          }
          s.offer={card:available.shift(),source:s.participant_order[1],from_deck:false};
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-ring-groups-offer`);
          await checkTurns(`${count}-ring`);
          for(const glyph of await page.locator('.fc-seat-public .fc-card > strong:first-child').all()) {
            await glyph.scrollIntoViewIfNeeded();
            assert.ok(await glyph.evaluate(n=>{
              const r=n.getBoundingClientRect();
              return [[1,1],[r.width-1,1],[1,r.height-1],[r.width-1,r.height-1]].every(([x,y])=>n.parentElement.contains(document.elementFromPoint(r.x+x,r.y+y)));
            }),'each card exposes a readable glyph within its bundle');
          }
          s.discards=[]; s.discard_sources={}; s.offer=null;
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-groups-no-history`);
          fixture=structuredClone(normal);
          const sparse=fixture.room.board_state;
          const meldIds=new Set(Object.values(sparse.melds).flatMap(gs=>gs.flatMap(g=>g.cards)));
          const green=deck.filter(id=>id.startsWith('G')&&!privateIds.has(id)&&!meldIds.has(id)).slice(0,2);
          Object.assign(sparse,{broken:{},listening:{},discards:green,discard_sources:{},offer:{card:deck.find(id=>id.startsWith('Y')&&!privateIds.has(id)&&!meldIds.has(id)),source:sparse.participant_order[1],from_deck:false}});
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-sparse-user-like`);
          assert.equal(await page.locator('.fc-history').count(),0,'unknown greens never acquire a player source');
          await checkRailHeads();
          sparse.discard_sources=Object.fromEntries(green.map(id=>[id,null]));
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-neutral-deck-two`); await checkRailHeads();
          sparse.discards=green.slice(0,1);
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-neutral-one`); await checkRailHeads();
          fixture=structuredClone(normal);
          fixture.room.board_state.discard_sources={};
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-legacy-history`);
          assert.equal(await page.locator('.fc-history').count(),0,'old unknown history is never assigned to players');
          fixture=structuredClone(normal);
          Object.assign(fixture.room.board_state,{melds:Object.fromEntries(s.participant_order.map(pid=>[pid,[]])),broken:{},listening:{},discards:deck.slice(0,20),offer:{card:deck[111],source:s.participant_order[1],from_deck:false},
            discard_sources:Object.fromEntries(deck.slice(0,100).map((id,i)=>[id,s.participant_order[i%count]]))});
          fixture.room.private_state={hand:[],legal_actions:[],action_labels:{}};
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-dense-ring-20`); await checkRailHeads();
          fixture.room.board_state.discards=deck.slice(0,100);
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(`${count}-history-100`);
          for(const pile of await page.locator('.fc-history').all()) {
            assert.ok(await pile.evaluate(n=>n.scrollHeight>n.clientHeight || n.scrollWidth>n.clientWidth),'long history scrolls locally');
            await pile.evaluate(n=>{n.scrollLeft=0;n.scrollTop=0;});
            await pile.focus();
            const axis=await pile.getAttribute('data-axis'),key=await pile.getAttribute('data-history-key');
            await page.keyboard.press(axis==='x'?'ArrowRight':'ArrowDown');
            await page.waitForFunction(({key,axis})=>{const n=[...document.querySelectorAll('.fc-discard-history')].find(n=>n.dataset.historyKey===key);return axis==='x'?n.scrollLeft>0:n.scrollTop>0;},{key,axis},{timeout:5000});
            for(const index of [0,-1]) {
              const face=pile.locator('.fc-card > strong:first-child').nth(index); await face.scrollIntoViewIfNeeded();
              assert.ok(await face.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),'first and last history cards are reachable');
            }
          }
          await checkRailHeads();
          await open('opening',false,count); await screenshot(`${count}-empty-opening`);
        }
        assert.deepEqual(errors,[]);
        metrics.push({mode,width,visualMatrix:true,consoleErrors:errors});
        await page.close(); continue;
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
      const fourOpeningIdentities=await identityPositions();
      await checkTurns('four-opening');
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
      await open('midgame');
      assert.ok(fixture.room.revision>=8);
      await screenshot('normal-midgame');
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
      for(const history of await pile.locator('.fc-discard-history').all()) {
        assert.ok((await history.boundingBox()).height<=150);
        assert.ok(await history.locator('.fc-card').count(),'no empty history placeholder');
      }
      await screenshot('discards',pile);
      if(process.env.FOUR_COLOR_ACTIONS_ONLY) {
        assert.deepEqual(errors,[]);
        metrics.push({mode,width,actions:true,consoleErrors:errors});
        await page.close(); continue;
      }
      if (mode === 'tenhu') {
        for (const count of [2,3]) {
          await open('opening',false,count);
          await screenshot(`${count}-seats`);
          assert.equal(await page.locator('.fc-seat').count(),count);
        }
      }
      if (mode === 'qunhu') {
        await open('opening',false,5); await screenshot('five-seats');
        fiveOpeningIdentities=await identityPositions();
        await checkTurns('five-opening');
        await open('midgame',false,5); await screenshot('five-normal-midgame');
      }
      // Dense public groups and long names on the actual page, without changing rules or a live room.
      await open('opening');
      fixture.room.participants[1].display_name = 'Codex_杉星_0828';
      fixture.room.participants[3].display_name = '名字较长的四色牌玩家';
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
      assert.deepEqual(await identityPositions(),fourOpeningIdentities,'public cards never move four-player identities');
      await checkTurns('four-public');
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
        ['history-empty',[],null], ['offer-only',[],{card:'W6-4',from_deck:false}], ['deck-only',[],{card:'W6-4',from_deck:true}], ['history-only',highDiscards,null],
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
        fixture.room.participants.forEach((p,i)=>{p.display_name=i===1?'Codex_杉星_0828':`长昵称玩家${i}正在牌桌旁`;});
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
        assert.deepEqual(await identityPositions(),fiveOpeningIdentities,'public cards never move five-player identities');
        await checkTurns('five-public');
        assert.ok(await page.locator('.fc-discard-history').evaluate(n=>n.scrollHeight>n.clientHeight));
        const stations=await page.locator('.fc-seat').evaluateAll(nodes=>Object.fromEntries(nodes.map(n=>[n.dataset.position,n.getBoundingClientRect().y])));
        assert.ok(stations['north-west']<stations.west && stations['north-east']<stations.east);
        assert.equal(Object.keys(stations).length,5);
        for (const seat of await page.locator('.fc-seat').all()) {
          for (const glyph of await seat.locator('.fc-meld .fc-card > strong:first-child').all()) {
            await glyph.scrollIntoViewIfNeeded();
            assert.equal(await glyph.evaluate(n=>{
              const r=n.getBoundingClientRect();
              return [[1,1],[r.width-1,1],[1,r.height-1],[r.width-1,r.height-1]].every(([x,y])=>n.parentElement.contains(document.elementFromPoint(r.x+x,r.y+y)));
            }),true,'every five-player public glyph remains reachable and unobscured');
          }
        }
        for(const [scene,discards,offer] of [
          ['five-history-empty',[],null],
          ['five-offer-only',[],{card:'W6-4',from_deck:false}],
          ['five-deck-only',[],{card:'W6-4',from_deck:true}],
          ['five-history', ['W0-4','W1-4'],null],
          ['five-history-100',highDiscards,null],
        ]) {
          Object.assign(fixture.room.board_state,{discards,offer});
          await page.reload(); await page.locator('.fc-table').waitFor(); await dismiss();
          await screenshot(scene);
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
