/* Original board + optional rules, checked with disposable server projections.
   PLAYWRIGHT_MODULE may point to an existing Playwright installation.
   DICE_SCREENSHOTS is optional; when absent, screenshots are cleaned automatically. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const generated = spawnSync(path.join(root,'.venv/bin/python'), ['-c', `
import json,tempfile,random
from pathlib import Path
from app import database,framework,invites,liars_dice_setup
from tests.test_liars_dice_teams import expire,seat
from unittest.mock import patch
from app.npc_personas import NpcPersona
from app.games import GAMES,game_catalog
from app.games.liars_dice import LiarsDice
from tests.test_multiplayer_games import participants
with tempfile.TemporaryDirectory(prefix='duel-dice-browser-db-') as d:
 database.DB_PATH=Path(d)/'test.db'; database.init_db()
 GAMES['liars_dice']=LiarsDice(random.Random(51))
 result={'catalog':game_catalog()}
 def project(r): return framework.project_room_for_viewer(r,'human-1')
 def act(r,move):
  pid=r['current_player_id'];role='human' if pid=='human-1' else 'ai'
  return framework.play_move(r['room_id'],role,pid,move,expected_revision=r['revision'])
 for key,options,count in [('classic',{},2),('party',{'mode':'party'},5),('pairs',{'mode':'party','team_mode':'pairs','literal_dice':True},4)]:
  r=framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',ordered_participants=participants(count),game_options=options)
  states={'opening':project(r)}
  r=act(r,{'action':'bid','quantity':(10 if key=='pairs' else 5*count),'face':6});states['bid']=project(r)
  r=act(r,{'action':'challenge'});states['reveal']=project(r)
  r=(framework.acknowledge_liars_dice_round(r['room_id'],'human-1',r['revision']) if key=='classic' else act(r,{'action':'acknowledge_round'}));states['next']=project(r)
  if key!='classic':
   r=framework.play_move(r['room_id'],'human','human-1',{'action':'end_session'},expected_revision=r['revision'])
   states['finished']=project(r)
  result[key]=states
 r=framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',participant_names={'human-1':'南山','ai-1':'松果小机'},game_options={'mode':'party','allow_surrender':True,'surrender_no_reroll':True,'cup_penalty':True,'extreme_penalty':True,'companion_penalty':True})
 state=r['board_state'];state['dice_by_player']={'human-1':[1,2,3,4,5],'ai-1':[3]*5}
 with database.write_transaction() as conn: conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?',(json.dumps(state),r['room_id']))
 r=framework.get_room(r['room_id']); advanced={'opening':project(r)}
 with patch.object(GAMES['liars_dice'],'_roll',return_value=[2]*5): r=act(r,{'action':'reroll'})
 advanced['rerolled']=project(r)
 r=act(r,{'action':'bid','quantity':10,'face':6});advanced['bid']=project(r)
 with patch.object(GAMES['liars_dice'],'_roll',return_value=[1]): r=act(r,{'action':'challenge'})
 advanced['reveal']=project(r)
 r=act(r,{'action':'acknowledge_round'})
 r=act(r,{'action':'bid','quantity':3,'face':2})
 r=act(r,{'action':'surrender'});advanced['surrender']=project(r)
 result['advanced']=advanced
 r=framework.create_room('liars_dice','human_first','human','human-1',opponent_id='ai-1',game_options={'mode':'party','team_mode':'teams','random_teams':False},target_player_count=6)
 r=liars_dice_setup.remember_profiles(r,{'human-1':{'name':'人类一号','avatar':{'type':'emoji','value':'🌲'}},'ai-1':{'name':'小机一号','avatar':{'type':'emoji','value':'🦊'}}})
 manual={'empty':project(r)}
 r=seat(r,'human-1','red',1);manual['red']=project(r)
 r=seat(r,'human-1','blue',3);manual['blue']=project(r)
 r=seat(r,'ai-1','red',2);manual['ai']=project(r)
 r=expire(r);manual['ready']=project(r)
 with patch('app.invites.npc_provider_capabilities',return_value={'available':True}),patch('app.npc_personas.select_personas',return_value=[NpcPersona(f'test-{i}',f'NPC{i}','test') for i in range(4)]):
  r=liars_dice_setup.start_regular(r['room_id'],'human','human-1',True)
 manual['started']=project(r);result['manual']=manual
 print(json.dumps(result,ensure_ascii=False))
`], {cwd:root, encoding:'utf8'});
assert.equal(generated.status, 0, generated.stderr);
const fixtures=JSON.parse(generated.stdout);
const ownedOutput=!process.env.DICE_SCREENSHOTS;
const out=process.env.DICE_SCREENSHOTS || fs.mkdtempSync(path.join(os.tmpdir(),'duel-dice-screens-'));
fs.mkdirSync(out,{recursive:true});
(async()=>{
 let browser;
 try {
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const measurements=[];
  for(const width of (process.env.DICE_WIDTHS || '360,430,1280').split(',').map(Number)) {
   const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<600,isMobile:width<600});
   const errors=[], posts=[]; let served=null, selected='party';
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const req=route.request(), url=new URL(req.url()), p=url.pathname;
    if(url.hostname!=='dice.test')return route.abort();
    if(p==='/' || p.startsWith('/static/')) {
     const file=path.join(root,'app/static',p==='/'?'index.html':p.slice(8));
     return route.fulfill({status:fs.existsSync(file)?200:404,body:fs.existsSync(file)?fs.readFileSync(file):'',contentType:p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html'});
    }
    let data={ok:true,notifications:[],rooms:[],timeline:[]};
    if(p==='/api/whoami')data={...data,bound:true,human_player_id:'human-1',human_name:'玩家',identity_label:'玩家',machines:[1,2,3,4].map(n=>({id:`ai-${n}`,name:`小机${n}`})),wallet:{balance:0},games:fixtures.catalog,npc_provider:{available:true}};
    if(req.method()==='POST' && (p==='/api/rooms'||p==='/api/invites'||p.endsWith('/move')||p.endsWith('/start'))) {
     const body=req.postDataJSON();posts.push({p,body});
     if(p==='/api/rooms' && body.game_options?.random_teams===false) {selected='manual';served=fixtures.manual.empty;}
     else if(p==='/api/rooms') {selected=body.game_options?.team_mode==='teams'?'pairs':body.game_options?.mode==='party'?'party':'classic';served=fixtures[selected].opening;}
     else if(p.endsWith('/move') && body.move.action==='choose_team_seat')served=body.move.team==='red'?fixtures.manual.red:fixtures.manual.blue;
     else if(p.endsWith('/move'))served=body.move.action==='reroll'?fixtures.advanced.rerolled:body.move.action==='end_session'?fixtures[selected].finished:fixtures[selected].bid;
     else if(p.endsWith('/start'))served=fixtures.manual.started;
     else return route.fulfill({status:400,json:{message:'测试已捕获邀请配置'}});
    }
    if(served && (p.startsWith('/api/rooms') || p==='/api/invites'))data={...data,room:served};
    return route.fulfill({json:data});
   });
   const assertVisibleControl=async(locator)=>{
    await locator.scrollIntoViewIfNeeded();
    assert.ok(await locator.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}), 'control center must not be covered');
   };
   const screenshot=async(name)=>{
    await page.evaluate(()=>{getSelection()?.removeAllRanges();document.activeElement?.blur();});
    await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:false});
   };
   await page.goto('http://dice.test/');
   await page.locator('#gameCategory').selectOption('dice');
   await page.locator('#gameType').selectOption('liars_dice');
   const mode=page.locator('#gameTypeRulePicker select[aria-label="吹牛骰子玩法"]');
   assert.equal(await mode.inputValue(),'classic');
   await mode.selectOption('party');
   assert.equal(await page.locator('#gameTypeRulePicker .liars-advanced').evaluate(n=>n.open),false);
   await screenshot(`picker-default`);
   const advancedSummary=page.locator('#gameTypeRulePicker .liars-advanced summary');
   await advancedSummary.focus();await advancedSummary.press('Space');
   assert.ok(await page.locator('#gameTypeRulePicker .liars-advanced').evaluate(n=>n.open));
   await advancedSummary.press('Space');
   await page.locator('#gameTypeRulePicker select[aria-label="组队玩法"]').selectOption('random');
   assert.deepEqual(await page.locator('#targetPlayerCount option').evaluateAll(ns=>ns.map(n=>n.value)),['4','6']);
   assert.equal(await page.locator('#stake').inputValue(),'0');
   assert.ok(await page.locator('#stake').isDisabled());
   assert.equal(await page.locator('#gameTypeRulePicker select[aria-label="组队玩法"]').inputValue(),'random');
   assert.equal(await page.locator('#gameTypeRulePicker .liars-open-teams').count(),0);
   await page.locator('#gameTypeRulePicker .liars-advanced summary')[width<600?'tap':'click']();
   await page.locator('#gameTypeRulePicker select[aria-label="有骰计骰"]').selectOption('true');
   assert.ok((await page.locator('#gameTypeRulePicker .liars-advanced').innerText()).includes('1仍百搭'));
   await page.locator('#gameTypeRulePicker input[aria-label^="允许本轮投降"]').check();
   await page.locator('#gameTypeRulePicker input[aria-label="投降不揭骰、不重摇"]').check();
   await page.locator('#gameTypeRulePicker select[aria-label="5人普通起叫"]').selectOption('7');
   // Use actual existing picker state, then trigger its normal rendering path.
   await page.evaluate(()=>{selectedMachineIds.clear();['ai-1','ai-2','ai-3'].forEach(id=>selectedMachineIds.add(id));renderMachineMultiPicker();renderCreateSeatPreview();updateCreateButtonState();});
   await page.locator('#gameTypeRulePicker .liars-advanced').scrollIntoViewIfNeeded();
   const ruleSizes=await page.locator('#gameTypeRulePicker .liars-rule-group .liars-random-toggle').evaluateAll(ns=>ns.map(n=>n.getBoundingClientRect().height));
   assert.equal(ruleSizes.length,6);
   assert.ok(ruleSizes.every(h=>h>=44));
   await screenshot(`picker`);
   await page.locator('#createButton')[width<600?'tap':'click']();
   await page.locator('.liars-current-round').waitFor();
   await page.evaluate(()=>stopPolling());
   assert.deepEqual(posts[0].body.game_options,{mode:'party',literal_dice:true,team_mode:'teams',random_teams:true,allow_surrender:true,surrender_no_reroll:true,opening_five:7});
   assert.equal(posts[0].body.game_type,'liars_dice');
   if(await page.locator('#dismissWaitModeModalButton').isVisible())await page.locator('#dismissWaitModeModalButton')[width<600?'tap':'click']();
   for(const key of ['classic','party','pairs']) {
    selected=key;
    for(const phase of ['opening','reveal','next',...(key==='classic'?[]:['finished'])]) {
     served=fixtures[key][phase];
     await page.evaluate(r=>{stopPolling();renderGame(r,'',[]);closeResultModal();},served);
     if(await page.locator('#dismissWaitModeModalButton').isVisible())await page.locator('#dismissWaitModeModalButton')[width<600?'tap':'click']();
     await page.locator('.board.liars_dice').waitFor();
     if(phase==='opening') {
      assert.equal(await page.locator('.liars-current-round').count(),1,'existing board remains');
      assert.ok(await page.locator('.liars-controls button').count()>=2);
      if(key==='classic')assert.equal(await page.locator('.liars-controls select').count(),2);
      else {
       assert.equal(await page.locator('.liars-controls select').count(),3);
       await page.locator('select[aria-label="本轮叫点数量"]').selectOption(key==='party'?'7':'2');
       await page.locator('select[aria-label="本轮叫点点数"]').selectOption('2');
       await page.locator('select[aria-label="叫点规则"]').selectOption('false');
       assert.ok(await page.locator('.liars-controls button').first().isDisabled());
       await page.locator('select[aria-label="叫点规则"]').selectOption('true');
       assert.ok(await page.locator('.liars-controls button').first().isEnabled());
       assert.ok((await page.locator('.liars-party-scores').innerText()).includes('0分'));
      }
     }
     if(phase==='reveal') {
      const text=await page.locator('.liars-result-loss').innerText();
      assert.ok(text.includes(key==='classic'?'-1颗骰子':'-1分'),text);
      await page.locator('.liars-reveal-details').last().evaluate(n=>n.open=true);
      assert.equal(await page.locator('.liars-revealed-player').count(),key==='pairs'?2:key==='party'?5:2);
      assert.equal(await page.locator('.liars-round-acknowledgement button').isDisabled(),key!=='classic' && served.current_player_id!=='human-1');
     }
     if(key==='pairs')assert.ok((await page.locator('#privateStateContent').innerText()).includes('我的骰子'));
     if(phase==='finished')assert.equal(await page.evaluate(r=>resultTextFor(r,[]),served),served.result.result_text);
     if(key!=='classic' && phase!=='opening') {
      assert.ok((await page.locator('.liars-party-scores').innerText()).includes('-1分'));
      assert.equal(await page.locator('.liars-party-scores details').count(),0);
     }
     const bad=await page.evaluate(()=>[...document.querySelectorAll('.board.liars_dice,.liars-controls select,.liars-controls button,.liars-party-scores')].filter(n=>{const r=n.getBoundingClientRect();return r.width && (r.left<-.5||r.right>innerWidth+.5);}).map(n=>n.className));

     assert.deepEqual(bad,[],`${key}/${phase}/${width}: overflow`);
     await screenshot(`${key}-${phase}`);
    }
   }
   selected='advanced'; served=fixtures.advanced.opening;
   await page.evaluate(r=>{stopPolling();renderGame(r,'',[]);closeResultModal();},served);
   if(await page.locator('#dismissWaitModeModalButton').isVisible())await page.locator('#dismissWaitModeModalButton')[width<600?'tap':'click']();
   await page.getByRole('button',{name:'单骰重摇 · 本轮限一次'})[width<600?'tap':'click']();
   await page.waitForFunction(()=>!room.private_state.can_reroll);
   assert.deepEqual(posts.at(-1).body.move,{action:'reroll'});
   assert.equal(await page.getByRole('button',{name:'单骰重摇 · 本轮限一次'}).count(),0);
   for(const phase of ['reveal','surrender']) {
    served=fixtures.advanced[phase];
    await page.evaluate(r=>{stopPolling();renderGame(r,'',[]);closeResultModal();},served);
    if(phase==='reveal') {
     assert.ok((await page.locator('.liars-result-loss').innerText()).includes('-4'));
     assert.equal(await page.locator('.liars-penalty-details summary').innerText(),'计分原因');
     assert.ok(await page.locator('.liars-penalty').first().isHidden());
     await page.locator('.liars-penalty-details summary')[width<600?'tap':'click']();
     assert.ok((await page.locator('.liars-penalty-details').innerText()).includes('最多扣4分'));
     await screenshot(`advanced-details`);
     await page.locator('.liars-penalty-details summary')[width<600?'tap':'click']();
    } else {
     assert.ok((await page.locator('.liars-result-loss').innerText()).includes('-0.5'));
     assert.ok((await page.locator('.liars-round-acknowledgement').innerText()).includes('保留骰面'));
     assert.equal(await page.locator('.liars-revealed-player').count(),0);
    }
    assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('.liars-penalty,.liars-result-loss')].filter(n=>n.getBoundingClientRect().right>innerWidth+.5).map(n=>n.className)),[]);
    await assertVisibleControl(page.locator('.liars-round-acknowledgement button'));
    await screenshot(`advanced-${phase}`);
   }
   // The existing room action ends party sessions even outside the human turn.
   selected='pairs'; served=fixtures.pairs.bid;
   await page.evaluate(r=>{stopPolling();renderGame(r,'',[]);closeResultModal();},served);
   assert.equal(await page.evaluate(()=>canHumanMove()),false);
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#resignButton')[width<600?'tap':'click']();
   await page.waitForFunction(()=>room.status==='finished');
   assert.deepEqual(posts.at(-1).body.move,{action:'end_session'});
   assert.equal(posts.at(-1).body.revision,fixtures.pairs.bid.revision);
   assert.ok(await page.locator('#resignButton').isDisabled());
   await page.evaluate(()=>closeResultModal());
   // Manual setup stays in the existing room panel and only moves the human viewer.
   await page.evaluate(()=>showView('lobbyView'));
   await page.locator('#gameTypeRulePicker select[aria-label="组队玩法"]').selectOption('manual');
   assert.equal(await page.locator('#gameTypeRulePicker .liars-open-teams').count(),0);
   await page.locator('#targetPlayerCount').selectOption('6');
   await page.locator('#createButton')[width<600?'tap':'click']();
   await page.locator('.liars-team-seat').first().waitFor();
   await page.evaluate(()=>stopPolling());
   assert.equal(await page.locator('.liars-team-row').count(),2);
   assert.equal(await page.locator('.liars-team-seat').count(),6);
   assert.equal(await page.locator('button[aria-label^="加入"]').count(),6);
   assert.ok(await page.locator('[data-action="开始游戏"]').isDisabled());
   await assertVisibleControl(page.locator('[aria-label="加入红队第1位"]'));
   await page.locator('[aria-label="加入红队第1位"]')[width<600?'tap':'click']();
   await page.waitForFunction(()=>room.team_selection.seats.red[0]==='human-1');
   await page.locator('[aria-label="加入蓝队第3位"]')[width<600?'tap':'click']();
   await page.waitForFunction(()=>room.team_selection.seats.blue[2]==='human-1');
   assert.equal(await page.locator('[aria-label="加入红队第1位"]').count(),1);
   assert.deepEqual(posts.at(-1).body.move,{action:'choose_team_seat',team:'blue',seat:3});
   assert.equal(Object.hasOwn(posts.at(-1).body.move,'player_id'),false);
   served=fixtures.manual.ai;
   await page.evaluate(r=>{stopPolling();renderGame(r,'',[]);},served);
   assert.equal(await page.locator('.liars-team-seat:not(button)').count(),2);
   assert.equal(await page.locator('.liars-team-seat:not(button) button').count(),0);
   assert.ok((await page.locator('.liars-team-seats').innerText()).includes('ID ai-1'));
   assert.ok((await page.locator('.liars-team-seats').innerText()).includes('🦊'));
   assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('.liars-team-seat')].filter(n=>n.getBoundingClientRect().right>innerWidth+.5).map(n=>n.className)),[]);
   const manualSize=await page.locator('#inviteRoomPanel').boundingBox();
   const red=await page.locator('.liars-team-red').boundingBox(),blue=await page.locator('.liars-team-blue').boundingBox();
   if(width===1280) {assert.equal(manualSize.width,880);assert.equal(red.y,blue.y);}
   else assert.ok(blue.y>red.y);
   measurements.push({viewport:width,manualCardWidth:manualSize.width,teamColumns:width===1280?2:1,smallestRuleTarget:Math.min(...ruleSizes)});
   await screenshot(`manual`);
   // Long names must wrap within the same seats; no alternate roster UI.
   const longNames=structuredClone(served);longNames.revision+=100;
   longNames.participants[0].display_name='山间晚风与松林一起听骰声';
   await page.evaluate(r=>renderGame(r,'',[]),longNames);
   assert.ok(await page.locator('.liars-team-name').evaluateAll(ns=>ns.every(n=>n.scrollWidth<=n.clientWidth+1)));
   await screenshot(`manual-long-name`);
   served=fixtures.manual.ready;
   await page.evaluate(r=>{stopPolling();renderGame(r,'',[]);},served);
   assert.ok(await page.locator('.liars-team-seats').isVisible());
   await page.locator('[data-action="开始游戏"]')[width<600?'tap':'click']();
   await page.waitForFunction(()=>room.status==='playing');
   assert.equal(posts.at(-1).body.fill_with_npcs,true);
   assert.equal(await page.locator('.liars-team-selection').count(),0);
   assert.equal(await page.locator('.liars-current-round').count(),1);
   // Invite picker uses the same rules, including constrained team table sizes.
   await page.evaluate(async()=>{writeRoomRoute(null,{mode:'replace'});await backToLobby({fromHistory:true});await showInviteDialog('create');});
   await page.locator('#inviteCategory').selectOption('dice');
   await page.locator('#inviteGame').selectOption('liars_dice');
   await page.locator('#inviteGameRulePicker select[aria-label="吹牛骰子玩法"]').selectOption('party');
   await page.locator('#inviteGameRulePicker select[aria-label="组队玩法"]').selectOption('random');
   assert.deepEqual(await page.locator('#inviteCount option').evaluateAll(ns=>ns.map(n=>n.value)),['4','6']);
   assert.ok(await page.locator('#inviteStake').isDisabled());
   assert.equal(await page.locator('#inviteGameRulePicker .liars-advanced').evaluate(n=>n.open),false);
   await screenshot(`invite-default`);
   await page.locator('#inviteGameRulePicker .liars-advanced summary')[width<600?'tap':'click']();
   await page.locator('#inviteGameRulePicker input[aria-label^="天黑黑"]').check();
   await page.locator('#inviteGameRulePicker .liars-advanced').scrollIntoViewIfNeeded();
   await screenshot(`invite-options`);
   await page.locator('#inviteSubmit')[width<600?'tap':'click']();
   await page.waitForFunction(()=>document.getElementById('inviteError').textContent.includes('测试'));
   assert.equal(posts.at(-1).body.game_options.team_mode,'teams');
   assert.equal(posts.at(-1).body.game_options.dark_rule,true);
   assert.deepEqual(errors,[]);
   console.log(`passed ${width}px: picker, invite, classic/party/teams rounds; manual self-seat/swap, avatars, NPC start; original board retained`);
   await page.close();
  }
  fs.writeFileSync(path.join(out,'ui-measurements.json'),JSON.stringify(measurements,null,2));
 } finally {
  if(browser)await browser.close();
  if(ownedOutput)fs.rmSync(out,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
