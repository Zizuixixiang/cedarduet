// Local assets and disposable DB only. --dom checks interaction/structure;
// default Playwright additionally checks 360/390/430/1280px geometry and screenshots.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'app/static');
const kwxOptions = {double_hu:true,gang_discard_double:true,liang_liability:true,consecutive_kong_double:true,horse_count:1,piao:3,joker:'red'};
const python = process.env.DUEL_TEST_PYTHON || path.join(root, '.venv/bin/python');
const fixtures = spawnSync(python, ['-c', String.raw`
import json, tempfile
from pathlib import Path
from unittest.mock import patch
from app import database, framework, invites
from app.games import GAMES, game_catalog
with tempfile.TemporaryDirectory(prefix='kwx-ui-') as tmp, patch.object(database,'DB_PATH',Path(tmp)/'test.db'):
 database.init_db(); rooms={}
 for game,count in [('mahjong',4),('ka_wu_xing',3)]:
  r=invites.create_invite(game,'human','p0',target_player_count=count,display_name='测试玩家')
  for i in range(1,count): invites.join_invite(r['invite_code'],'ai',f'p{i}',display_name=f'小机{i}')
  r=invites.start_invite(r['room_id'],'human','p0')
  rooms[game]=framework.project_room_for_viewer(r,'p0')
  if game == 'ka_wu_xing':
   for _ in range(240):
    if r['status']=='finished': break
    actor=next(p for p in r['participants'] if p['player_id']==r['current_player_id'])
    move=GAMES[game].choose_local_npc_action(r['board_state'],actor,r['participants'])
    r=framework.play_move(r['room_id'],actor['role'],actor['player_id'],move,expected_revision=r['revision'])
    if r['status']=='playing' and r['board_state']['revealed'] and game+'_revealed' not in rooms:
     rooms[game+'_revealed']=framework.project_room_for_viewer(r,'p0')
   rooms[game+'_terminal']=framework.project_room_for_viewer(r,'p0')
 print(json.dumps({'games':[g for g in game_catalog() if g['game_type'] in rooms], 'rooms':rooms}))
`], {cwd:root, encoding:'utf8'});
assert.equal(fixtures.status, 0, fixtures.stderr);
const fixture = JSON.parse(fixtures.stdout);
function script(name) {return fs.readFileSync(path.join(staticRoot,name),'utf8');}
const app = script('app.js').replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '') + '\nwindow.run = source => eval(source);';
const setup = `identity={human_player_id:'p0',human_name:'测试玩家',machines:[1,2,3].map(i=>({id:'p'+i,name:'小机'+i})),bound:true,games:window.fixture.games,npc_provider:{available:false},wallet:{balance:200}}; syncGameTypeOptions(identity.games,{preserveSelection:false}); syncMachinePicker(identity.machines); showView('lobbyView');`;

async function domCheck() {
 const {JSDOM}=require(process.env.JSDOM_MODULE || 'jsdom');
 const dom = new JSDOM(script('index.html'), {url:'http://kwx.test/', runScripts:'outside-only', pretendToBeVisual:true});
 try {
 const w=dom.window;
 w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 const requests=[];
 w.fetch=async(url,options={})=>{
  const body=options.body?JSON.parse(options.body):null;
  if(options.method==='POST') requests.push({url,body});
  return {ok:true,json:async()=>({ok:true,rooms:[],notifications:[],wallet:{balance:200},
   ...(body?.game_type?{room:fixture.rooms[body.game_type]}:{})})};
 };
 w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.eval(script('game_ui_registry.js')); w.eval(script('games/mahjong.js')); w.eval(app);
 w.fixture=fixture; w.run(setup);
 for(const hook of ['renderBoard','renderControls']) assert.equal(w.DuelGameUI.get('mahjong')[hook],w.DuelGameUI.get('ka_wu_xing')[hook]);
 assert.equal(typeof w.DuelGameUI.get('ka_wu_xing').renderRulePicker,'function');
 assert.equal(w.DuelGameUI.get('mahjong').renderRulePicker,undefined);
 const select=w.document.getElementById('gameType');
 w.document.getElementById('gameCategory').value='card';
 w.run('syncGameTypeOptions(identity.games,{preserveSelection:false})');
 const options=element=>[...element.options].map(o=>[o.value,o.textContent]);
 const expected=[['ka_wu_xing','襄阳卡五星 · 3人'],['mahjong','麻将 · 4人']];
 assert.deepEqual(options(select),expected); // Existing player-count sort: 3 before 4.
 w.run('$("inviteCategory").value="card";syncInviteGameOptions({preserveSelection:false})');
 const invite=w.document.getElementById('inviteGame');
 assert.deepEqual(options(invite),expected);
 select.dispatchEvent(new w.Event('change'));
 for(const [game,count] of [['mahjong',4],['ka_wu_xing',3],['mahjong',4]]) {
  w.run('selectedMachineIds.clear();["p1","p2","p3"].forEach(id=>selectedMachineIds.add(id));inviteMachineIds=new Set(["p1","p2"]);');
  select.value=game;select.dispatchEvent(new w.Event('change'));
  const tokenLabel=w.document.getElementById('gameTokenEstimate');
  assert.equal(tokenLabel.textContent,game==='ka_wu_xing'?'（常见约400–550 token/轮）':'（约600–900 token/轮）');
  if(game==='ka_wu_xing') {
   for(const term of ['终局另计','cl100k_base','state','move','根工具包装另计','不是计费上限']) assert.ok(tokenLabel.title.includes(term));
  } else assert.equal(tokenLabel.title,'');
  invite.value=game;invite.dispatchEvent(new w.Event('change'));
  assert.equal(w.document.querySelectorAll('.mahjong-rule-picker').length,0); // No redundant variant selector.
  assert.equal(w.document.querySelectorAll('#gameTypeRulePicker,#inviteGameRulePicker').length,game==='ka_wu_xing'?2:0);
  if(game==='ka_wu_xing') {
   for(const id of ['gameTypeRulePicker','inviteGameRulePicker']) {
    const picker=w.document.getElementById(id);
    assert.equal(picker.querySelectorAll('select').length,3);
    assert.equal(picker.querySelectorAll('input[type=checkbox]').length,4);
    assert.equal(picker.querySelector('details').open,false);
    picker.querySelector('details').open=true;
    for(const [key,value] of Object.entries(kwxOptions)) {
     const input=picker.querySelector(`[data-kwx-option=${key}]`);
     assert.ok(w.document.getElementById(input.getAttribute('aria-describedby')));
     if(input.type==='checkbox') input.checked=value;
     else input.value=String(value);
     input.dispatchEvent(new w.Event('change'));
    }
   }
  }
  assert.equal(w.run('selectedGameType($("gameType"))'),game);
  assert.equal(w.run('selectedGameRequirement().maxPlayers'),count);
  assert.equal(w.run('selectedGameRequirement().supportsNpcs'),true);
  assert.deepEqual([...w.document.getElementById('targetPlayerCount').options].map(o=>o.value),[String(count)]);
  assert.deepEqual([...w.document.getElementById('inviteCount').options].map(o=>o.value),[String(count)]);
  assert.equal(w.run('selectedParticipantIds().length'),count-1);
  assert.equal(w.run('inviteMachineIds.size'),count-2); // Reserve an external invitation seat.
  w.run('selectedMachineIds.clear();selectedMachineIds.add("p1");$("fillWithNpcs").checked=true;$("stake").value="0";$("inviteStake").value="0";renderCreateSeatPreview();');
  assert.equal(w.document.querySelectorAll('#seatPreview .seat-preview-item').length,count);
  assert.equal([...w.document.querySelectorAll('#seatPreview .seat-preview-kind')].filter(n=>n.textContent==='NPC').length,count-2);
  assert.equal(w.document.getElementById('createButton').disabled,false);
  await w.run('createRoom()');
  assert.deepEqual(requests.at(-1),{url:'/api/rooms',body:{ai_player:'p1',ai_players:['p1'],game_type:game,mode:w.document.getElementById('mode').value,stake:0,target_player_count:count,fill_with_npcs:true,...(game==='ka_wu_xing'?{game_options:kwxOptions}:{})}});
  w.run('stopPolling()');
  w.document.getElementById('inviteSubmit').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(requests.at(-1),{url:'/api/invites',body:{game_type:game,target_player_count:count,ai_players:count===3?['p1']:['p1','p2'],stake:0,timeout_takeover_seconds:0,...(game==='ka_wu_xing'?{game_options:kwxOptions}:{})}});
  assert.equal(w.document.getElementById('inviteError').textContent,'');
  if(game==='ka_wu_xing') {
   for(let reopen=0;reopen<2;reopen++) {
    await w.run('showInviteDialog("create")');
    const picker=w.document.getElementById('inviteGameRulePicker');
    assert.equal(picker.querySelector('details').open,false);
    for(const [key,value] of Object.entries(kwxOptions)) {
     const input=picker.querySelector(`[data-kwx-option=${key}]`);
     assert.equal(input.type==='checkbox'?input.checked:input.value,typeof value==='boolean'?value:String(value));
    }
    assert.deepEqual(JSON.parse(w.run('JSON.stringify(selectedGameOptions($("inviteGame")))')),kwxOptions);
    w.document.getElementById('inviteDismiss').click();
   }
  }
  w.run('stopPolling()');
 }
 for(const [game,count] of [['ka_wu_xing',3],['mahjong',4]]) {
  w.run(`room=null;renderGame(window.fixture.rooms[${JSON.stringify(game)}]);`);
  assert.equal(w.document.querySelectorAll('.mahjong-seat').length,count);
  assert.equal(w.document.querySelectorAll('.mahjong-discards').length,count);
  assert.equal(w.document.querySelectorAll('.mahjong-seat.position-top').length,count===3?0:1);
  assert.equal(w.document.querySelectorAll('.mahjong-own-hand-scroll button').length,fixture.rooms[game].private_state.hand.length);
 }
 for (const suffix of ['revealed','terminal']) {
  const key='ka_wu_xing_'+suffix;if(!fixture.rooms[key])continue;
  w.run(`room=null;renderGame(window.fixture.rooms[${JSON.stringify(key)}]);`);
  if(suffix==='revealed') assert.ok(w.document.querySelectorAll('.mahjong-revealed-hand .mahjong-tile').length>0);
  else assert.equal(w.document.querySelectorAll('.mahjong-terminal-row').length,3);
 }
 } finally {dom.window.close();}
 console.log('PASS: independent ordinary/invite options and POST payloads, invite dialog reopen, fixed 3/4 players, AI limits/NPC fill, shared renderer/private hand DOM (no layout engine)');
}

// Use the actual expanded native controls, including touch hit targets and
// helper typography; this branch requires a real browser, not jsdom.
async function checkRulePicker(page, id, game, width, output) {
 const picker=page.locator('#'+id);
 assert.equal(await picker.count(),game==='ka_wu_xing'?1:0);
 assert.equal(await page.locator('.mahjong-rule-picker').count(),0);
 if(game!=='ka_wu_xing') return;
 assert.equal(await picker.locator('select').count(),3);
 assert.equal(await picker.locator('input[type=checkbox]').count(),4);
 await picker.locator('summary').click();
 assert.equal(await picker.locator('details').evaluate(e=>e.open),true);
 for(const [key,value] of Object.entries(kwxOptions)) {
  const input=picker.locator(`[data-kwx-option=${key}]`);
  await input.scrollIntoViewIfNeeded();
  if(typeof value==='boolean') {
   await input.setChecked(value);
   await input.focus();
   await input.press('Space');
   assert.equal(await input.isChecked(),!value);
   await input.press('Space');
   assert.equal(await input.isChecked(),value);
  } else await input.selectOption(String(value));
  const geometry=await input.evaluate(e=>{
   const r=e.getBoundingClientRect(), target=e.type==='checkbox'?e.closest('label'):e;
   const touch=target.getBoundingClientRect();
   const help=document.getElementById(e.getAttribute('aria-describedby'));
   const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
   return {x:touch.x,right:touch.right,height:touch.height,hit:hit===e || e.contains(hit),
    controlSize:parseFloat(getComputedStyle(e.type==='checkbox'?target.querySelector('.kwx-option-title'):e).fontSize),helpSize:parseFloat(getComputedStyle(help).fontSize)};
  });
  assert.ok(geometry.x>=0 && geometry.right<=width,'rule field clipped');
  assert.ok(geometry.height>=44,'rule field touch target below 44px');
  assert.ok(geometry.hit,'rule field covered by another element');
  assert.ok(geometry.helpSize<=geometry.controlSize,'rule help must remain secondary');
 }
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'expanded rules overflow');
 const selectId=id==='gameTypeRulePicker'?'gameType':'inviteGame';
 assert.deepEqual(await page.evaluate(selectId=>window.run(`selectedGameOptions(document.getElementById('${selectId}'))`),selectId),kwxOptions);
 await picker.locator('summary').focus();
 await picker.locator('summary').press('Enter');
 assert.equal(await picker.locator('details').evaluate(e=>e.open),false);
 await picker.locator('summary').press('Enter');
 assert.equal(await picker.locator('details').evaluate(e=>e.open),true);
 assert.deepEqual(await page.evaluate(selectId=>window.run(`selectedGameOptions(document.getElementById('${selectId}'))`),selectId),kwxOptions);
 await picker.screenshot({path:path.join(output,`rules-${selectId}-${width}.png`)});
 // Restore focus/scroll to the top before checking surrounding form controls.
 await picker.locator('summary').scrollIntoViewIfNeeded();
 await picker.locator('summary').focus();
}

async function browserCheck() {
 const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
 const output=process.env.DUEL_UI_SCREENSHOTS || path.join(root,'.kwx-ui-screenshots');
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'], ...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 try {
  for(const width of [360,390,430,1280]) {
   const page=await browser.newPage({viewport:{width,height:900},hasTouch:width<600,isMobile:width<600});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{
    const u=new URL(route.request().url());if(u.hostname!=='kwx.test')return route.abort();
    if(u.pathname.startsWith('/api/'))return route.fulfill({json:{ok:true,rooms:[],notifications:[]}});
    const rel=u.pathname==='/'?'index.html':u.pathname.replace(/^\/static\//,'');
    const file=path.join(staticRoot,rel);if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
    const body=rel==='app.js'?app:fs.readFileSync(file);
    return route.fulfill({body,contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':rel.endsWith('.svg')?'image/svg+xml':'text/html'});
   });
   await page.goto('http://kwx.test/');
   await page.evaluate(async data=>{window.fixture=data;await window.run('loadCatalogGameRenderers(window.fixture.games)');},fixture);
   await page.evaluate(setup=>window.run(setup),setup);
   assert.equal(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches),width<600);
   await page.selectOption('#gameCategory','card');
   assert.deepEqual(await page.locator('#gameType option').evaluateAll(nodes=>nodes.map(n=>[n.value,n.textContent])),[['ka_wu_xing','襄阳卡五星 · 3人'],['mahjong','麻将 · 4人']]);
   for(const [game,count] of [['mahjong',4],['ka_wu_xing',3]]) {
    await page.selectOption('#gameType',game);
    const tokenLabel=page.locator('#gameTokenEstimate');
    assert.equal(await tokenLabel.textContent(),game==='ka_wu_xing'?'（常见约400–550 token/轮）':'（约600–900 token/轮）');
    const labelStyle=await tokenLabel.evaluate(el=>{
     const r=el.getBoundingClientRect(),s=getComputedStyle(el),parent=el.parentElement.getBoundingClientRect();
     return {font:s.fontSize,line:s.lineHeight,color:s.color,left:r.left,right:r.right,parentRight:parent.right};
    });
    assert.equal(labelStyle.font,'12px');
    assert.equal(labelStyle.color,'rgb(201, 95, 114)');
    assert.ok(labelStyle.left>=0 && labelStyle.right<=width,'token label overflow');
    if(game==='ka_wu_xing') assert.ok((await tokenLabel.getAttribute('title')).includes('终局另计'));
    assert.equal(await page.locator('#targetPlayerCount').inputValue(),String(count));
    await checkRulePicker(page,'gameTypeRulePicker',game,width,output);
    if(width<600) assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'lobby overflow');
    await page.screenshot({path:path.join(output,`picker-${game}-${width}.png`),fullPage:false});
    assert.equal(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches),width<600,'screenshot changed input emulation');
   }
   await page.evaluate(()=>window.run('showInviteDialog("create")'));
   await page.selectOption('#inviteCategory','card');
   assert.deepEqual(await page.locator('#inviteGame option').evaluateAll(nodes=>nodes.map(n=>[n.value,n.textContent])),[['ka_wu_xing','襄阳卡五星 · 3人'],['mahjong','麻将 · 4人']]);
   for(const [game,count] of [['mahjong',4],['ka_wu_xing',3]]) {
    await page.selectOption('#inviteGame',game);
    assert.equal(await page.locator('#inviteCount').inputValue(),String(count));
    await checkRulePicker(page,'inviteGameRulePicker',game,width,output);
    await page.screenshot({path:path.join(output,`invite-${game}-${width}.png`),fullPage:false});
   }
   await page.locator('#inviteDismiss').click();
   for(const [game,count] of [['ka_wu_xing',3],['mahjong',4]]) {
    await page.evaluate(game=>window.run(`room=null;renderGame(window.fixture.rooms[${JSON.stringify(game)}]);`),game);
    await page.locator('.mahjong-table').waitFor();
    assert.equal(await page.locator('.mahjong-seat').count(),count);
    assert.equal(await page.locator('.mahjong-seat.position-top').count(),count===3?0:1);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'page overflow');
    const geometry=await page.locator('.mahjong-table').evaluate(table=>{
     const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};};
     return {table:rect(table),seats:[...table.querySelectorAll('.mahjong-seat')].map(rect)};
    });
    for(const seat of geometry.seats)assert.ok(seat.x>=geometry.table.x && seat.right<=geometry.table.right,'seat clipping');
    await page.locator('.mahjong-table').screenshot({path:path.join(output,`${game}-${width}.png`)});
   }
   for (const suffix of ['revealed','terminal']) {
    const key='ka_wu_xing_'+suffix;if(!fixture.rooms[key])continue;
    await page.evaluate(key=>window.run(`room=null;renderGame(window.fixture.rooms[${JSON.stringify(key)}]);`),key);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'revealed/terminal overflow');
    await page.locator('.mahjong-board-layout').screenshot({path:path.join(output,`${key}-${width}.png`)});
   }
   assert.equal(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches),width<600);
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log(`PASS: browser widths 360/390/430/1280; screenshots ${output}`);
 } finally {await browser.close();}
}
(process.argv.includes('--dom')?domCheck():browserCheck()).catch(error=>{console.error(error);process.exitCode=1;});
