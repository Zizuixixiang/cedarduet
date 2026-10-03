// Focused renderer checks using real engine states, no production rooms/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const output = process.env.DUEL_UI_SCREENSHOTS || '/tmp/duel-monopoly-trade-jail';
const generated = spawnSync('.venv/bin/python', ['-c', `
import json, random
from copy import deepcopy
from app.games.monopoly import Monopoly
ps=[dict(player_id=f'human:{i+1}', display_name=f'玩家{i+1}', role='human', participant_kind='human', seat_index=i) for i in range(4)]
g=Monopoly(random.Random(1)); states={}
s=g.initialize(ps); s['phase']='manage'
def play(action, **kw):
 global s
 actor=next(p for p in ps if p['player_id']==s['turn_player_id'])
 s=g.apply_action(s,dict(action=action,action_seq=s['action_seq'],**kw),actor).state
def save(key, viewer):
 states[key]=dict(viewer=viewer,board_state=g.public_state(s,ps),private_state=g.private_state(s,ps[int(viewer[-1])-1],ps))
offer=dict(give_cash=100,take_cash=0,give_tiles=[],take_tiles=[])
play('propose_trade',to='human:4',**offer)
play('propose_trade',to='human:3',**offer)
play('end_turn'); save('third_party','human:2')
play('propose_trade',to='human:1',**offer)
s['phase']='manage'; play('end_turn'); save('recipient','human:3')
play('respond_trade',accept=True); save('accepted','human:3')
s=g.initialize(ps); s['players'][0].update(position=10,jail_cards=['chance'])
s['_decks']['chance'].remove(0); save('visiting','human:1')
s['players'][0]['jailed']=True; save('jailed','human:1')
play('use_jail_card'); save('released','human:1')
print(json.dumps(dict(participants=ps,states=states)))
`], {cwd:root,encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
const fixtures = JSON.parse(generated.stdout);

async function checkDOM() {
  const {JSDOM} = await import('jsdom');
  const dom = new JSDOM('<div id="board"></div><div id="controls"></div>', {runScripts:'outside-only',url:'http://duel.test'});
  const {window} = dom; const doc=window.document; let renderer;
  window.DuelGameUI={register:(_,value)=>{renderer=value;}};
  window.eval(fs.readFileSync(path.join(root,'app/static/games/monopoly.js'),'utf8'));
  const sent=[], uiState={};
  const tick=()=>new Promise(resolve=>window.setTimeout(resolve,0));
  const render=key=>{
    const f=fixtures.states[key];
    const c={state:f.board_state,participants:fixtures.participants,viewer:{player_id:f.viewer},
      room:{room_id:'dom-check',current_player_id:f.board_state.turn_player_id},
      board:doc.getElementById('board'),controls:doc.getElementById('controls'),uiState,
      canMove:true,isTerminal:false,privateState:f.private_state,legalActions:f.private_state.legal_actions,
      helpers:{canMove:()=>true,renderParticipantAvatar:()=>{},submitMove:async move=>{sent.push(move);return true;}}};
    c.board.replaceChildren();c.controls.replaceChildren();renderer.renderBoard(c);renderer.renderControls(c);
  };
  render('third_party');
  assert.equal(doc.querySelector('details').open,false);
  assert.equal(doc.querySelector('summary').textContent,'待处理交易 2 笔');
  doc.querySelector('summary').click();await tick();
  assert.equal(doc.querySelector('details').open,true);
  render('third_party');assert.equal(doc.querySelector('details').open,true);
  doc.querySelector('summary').click();await tick();
  render('third_party');assert.equal(doc.querySelector('details').open,false);
  assert.equal(doc.querySelectorAll('.monopoly-pending-trade').length,2);
  assert.equal(doc.querySelector('[data-action="roll"]').disabled,false);
  assert.ok([...doc.querySelectorAll('button')].some(b=>b.textContent==='提出交易'&&!b.disabled));
  assert.equal(doc.querySelectorAll('[data-action="respond_trade"]').length,0);
  render('recipient');
  assert.equal(doc.querySelector('details').open,true);
  assert.match(doc.querySelector('summary').textContent,/待处理交易 3 笔.*等你回应/);
  assert.match(doc.querySelector('.monopoly-pending-trade.is-due').textContent,/玩家1 ⇄ 玩家3/);
  assert.equal(doc.querySelectorAll('.monopoly-pending-trade').length,3);
  assert.equal(doc.querySelectorAll('[data-action="respond_trade"]').length,2);
  assert.match(doc.querySelector('.monopoly-trade-task').textContent,/玩家1向你提出交易/);
  const accept=[...doc.querySelectorAll('[data-action="respond_trade"]')].find(b=>b.textContent==='接受交易');
  assert.match(accept.closest('.monopoly-pending-trade').textContent,/玩家1 ⇄ 玩家3/);
  accept.click();await new Promise(resolve=>setImmediate(resolve));assert.equal(sent.at(-1).accept,true);
  const reject=[...doc.querySelectorAll('[data-action="respond_trade"]')].find(b=>b.textContent==='拒绝交易');
  reject.click();await tick();assert.equal(sent.at(-1).accept,false);
  render('accepted');
  assert.equal(doc.querySelector('details').open,false);
  assert.equal(doc.querySelectorAll('.monopoly-pending-trade').length,2);
  assert.ok(doc.querySelector('[data-action="roll"]'));
  const room=key=>({room_id:'dom-check',board_state:fixtures.states[key].board_state,participants:fixtures.participants});
  assert.equal(renderer.monopolyTransitionBeats(room('recipient'),room('accepted'))[0].kind,'trade');
  render('visiting');
  assert.match(doc.querySelector('.monopoly-location').textContent,/只是探访，未入狱/);
  assert.match(doc.querySelector('.monopoly-context').textContent,/只是探访监狱，未入狱/);
  assert.match(doc.querySelector('.monopoly-action-panel').textContent,/只有真正入狱后/);
  assert.equal(doc.querySelectorAll('[data-action="pay_bail"],[data-action="use_jail_card"]').length,0);
  render('jailed');
  for(const action of ['pay_bail','use_jail_card']) {
    const b=doc.querySelector(`[data-action="${action}"]`);assert.equal(b.disabled,false);
    b.click();await new Promise(resolve=>setImmediate(resolve));assert.equal(sent.at(-1).action,action);
    assert.equal(sent.at(-1).action_seq,fixtures.states.jailed.board_state.action_seq);
  }
  render('released');
  assert.equal(doc.querySelectorAll('[data-action="use_jail_card"]').length,0);
  assert.ok(doc.querySelector('[data-action="roll"]'));
  assert.equal(doc.querySelector('.monopoly-pending-trades'),null);
  // Old single-offer projections still render; other viewers cannot respond.
  fixtures.states.legacy=structuredClone(fixtures.states.recipient);
  delete fixtures.states.legacy.board_state.trades;
  render('legacy');
  assert.equal(doc.querySelector('details').open,true);
  assert.match(doc.querySelector('summary').textContent,/待处理交易 1 笔/);
  assert.equal(doc.querySelectorAll('[data-action="respond_trade"]').length,2);
  fixtures.states.observer=structuredClone(fixtures.states.recipient);
  fixtures.states.observer.viewer='human:2';
  fixtures.states.observer.private_state.legal_actions=[];
  render('observer');
  assert.equal(doc.querySelector('details').open,false);
  assert.equal(doc.querySelectorAll('[data-action="respond_trade"]').length,0);
  // An explicit empty v2 list takes precedence over a stale legacy alias.
  fixtures.states.legacy.board_state.trades=[];
  render('legacy');assert.equal(doc.querySelector('.monopoly-pending-trades'),null);
  dom.window.close();
  console.log(JSON.stringify({ok:true,mode:'DOM',states:Object.keys(fixtures.states),visualVerification:false}));
}

(async () => {
  if(process.env.DUEL_MONOPOLY_DOM_ONLY)return checkDOM();
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const checks=[];
  try {
    for (const width of [360,430,1280]) {
      const page = await browser.newPage({viewport:{width,height:1000},hasTouch:width<600});
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>{window.setInterval=()=>0;});
      await page.route('**/*',route=>{
        const url=new URL(route.request().url());
        if(url.host!=='duel.test')return route.abort();
        if(url.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true}});
        const rel=url.pathname==='/'?'index.html':url.pathname.replace(/^\/static\//,'');
        const file=path.join(root,'app/static',rel);
        if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
        let body=fs.readFileSync(file);
        if(rel==='app.js')body=body.toString().replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/,'')+'\nwindow.run=(source)=>eval(source);';
        return route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
      });
      await page.goto('http://duel.test/');
      await page.evaluate(async()=>{
        window.run("identity={human_player_id:'human:1',human_name:'玩家1',machines:[],games:[]};startRoomPolling=()=>{};window.sent=[];submitMove=async move=>{sent.push(move);return true;};");
        await window.DuelGameUI.load('monopoly');
      });
      let revision=100;
      const render=async key=>{
        const f=fixtures.states[key], id=f.board_state.turn_player_id;
        const r={room_id:'trade-jail-test',game_type:'monopoly',game_name:'大富翁',status:'playing',revision:++revision,
          participants:fixtures.participants,current_player_id:id,current_actor:fixtures.participants.find(p=>p.player_id===id),
          current_turn:'human',turn:'human',viewer:{player_id:f.viewer,role:'human',can_move:true,is_participant:true},
          board_state:f.board_state,private_state:f.private_state};
        await page.evaluate(r=>{
          window.fixture=r;
          window.run('identity.human_player_id=fixture.viewer.player_id;room=null;renderGame(fixture,"",[]);hideWaitModeModal();closeResultModal();');
        },r);
        await page.waitForFunction(()=>document.querySelector('.monopoly-ring')&&getComputedStyle(document.querySelector('.monopoly-ring')).display==='grid');
      };
      const geometry=async key=>{
        const metrics=await page.evaluate(()=>({
          overflow:document.documentElement.scrollWidth>innerWidth,
          font:getComputedStyle(document.querySelector('.monopoly-context')||document.querySelector('.monopoly-help')).fontSize,
          buttons:[...document.querySelectorAll('.monopoly-controls button, .monopoly-controls summary')].map(e=>e.getBoundingClientRect().height).filter(h=>h>0),
        }));
        assert.equal(metrics.overflow,false);assert.ok(metrics.buttons.every(h=>h>=44));
        await page.locator('.monopoly-controls').scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(output,`${key}-${width}.png`),fullPage:true});
        checks.push({width,key,...metrics});
      };
      await render('third_party');
      assert.equal(await page.locator('details.monopoly-pending-trades').evaluate(e=>e.open),false);
      assert.equal(await page.locator('.monopoly-pending-trades summary').textContent(),'待处理交易 2 笔');
      assert.equal(await page.locator('.monopoly-pending-trade').first().isVisible(),false);
      await page.locator('.monopoly-pending-trades summary').focus();await page.keyboard.press('Enter');
      assert.equal(await page.locator('.monopoly-pending-trade').first().isVisible(),true);
      await render('third_party');
      assert.equal(await page.locator('details.monopoly-pending-trades').evaluate(e=>e.open),true);
      await page.locator('.monopoly-pending-trades summary').click();
      assert.equal(await page.locator('.monopoly-pending-trade').count(),2);
      assert.equal(await page.locator('[data-action="roll"]').isEnabled(),true);
      assert.equal(await page.locator('[data-action="respond_trade"]').count(),0);
      await page.getByRole('button',{name:'提出交易',exact:true}).click();
      assert.deepEqual(await page.locator('.monopoly-dialog select option').evaluateAll(es=>es.map(e=>e.value)),['human:1']);
      await page.keyboard.press('Escape');await geometry('third_party');
      await render('recipient');
      assert.equal(await page.locator('details.monopoly-pending-trades').evaluate(e=>e.open),true);
      assert.match(await page.locator('.monopoly-pending-trade').first().textContent(),/玩家1 ⇄ 玩家3/);
      assert.equal(await page.locator('.monopoly-pending-trade').count(),3);
      assert.equal(await page.locator('[data-action="respond_trade"]').count(),2);
      assert.match(await page.locator('.monopoly-trade-task').textContent(),/玩家1向你提出交易/);
      assert.match(await page.locator('.monopoly-pending-trade').filter({has:page.locator('[data-action="respond_trade"]')}).textContent(),/玩家1 ⇄ 玩家3/);
      assert.equal(await page.locator('[data-action="roll"]').count(),0);
      await page.getByRole('button',{name:'接受交易',exact:true}).focus();await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(()=>sent.at(-1).accept),true);await geometry('recipient');
      await page.getByRole('button',{name:'拒绝交易',exact:true}).click();
      assert.equal(await page.evaluate(()=>sent.at(-1).accept),false);
      await render('accepted');
      assert.equal(await page.locator('details.monopoly-pending-trades').evaluate(e=>e.open),false);
      assert.equal(await page.locator('.monopoly-pending-trade').count(),2);
      assert.equal(await page.locator('[data-action="roll"]').isEnabled(),true);
      const beats=await page.evaluate(({before,after})=>window.DuelGameUI.get('monopoly').monopolyTransitionBeats(
        {game_type:'monopoly',board_state:before,participants:fixture.participants},
        {game_type:'monopoly',board_state:after,participants:fixture.participants}),
        {before:fixtures.states.recipient.board_state,after:fixtures.states.accepted.board_state});
      assert.equal(beats[0].kind,'trade');
      await render('visiting');
      assert.match(await page.locator('.monopoly-location').textContent(),/只是探访，未入狱/);
      assert.match(await page.locator('.monopoly-context').textContent(),/只是探访监狱，未入狱/);
      assert.match(await page.locator('.monopoly-action-panel').textContent(),/只有真正入狱后/);
      assert.equal(await page.locator('[data-action="pay_bail"],[data-action="use_jail_card"]').count(),0);
      assert.equal(await page.locator('[data-action="roll"]').isEnabled(),true);await geometry('visiting');
      await render('jailed');
      for(const action of ['pay_bail','use_jail_card']) {
        assert.equal(await page.locator(`[data-action="${action}"]`).isEnabled(),true);
        await page.locator(`[data-action="${action}"]`).click();
        assert.equal(await page.evaluate(()=>sent.at(-1).action),action);
        assert.equal(await page.evaluate(()=>sent.at(-1).action_seq),fixtures.states.jailed.board_state.action_seq);
      }
      await geometry('jailed');await render('released');
      assert.equal(await page.locator('[data-action="use_jail_card"]').count(),0);
      assert.equal(await page.locator('[data-action="roll"]').isEnabled(),true);
      assert.match(await page.locator('.monopoly-context').textContent(),/未入狱/);
      assert.deepEqual(errors,[]);await page.close();
    }
    console.log(JSON.stringify({ok:true,checks,screenshots:output}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
