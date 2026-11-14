// Local DOM only: no network, player state, or persistent database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const assets = path.resolve(__dirname, '../app/static');
(async () => {
  const {JSDOM} = await import('jsdom');
  const dom = new JSDOM(fs.readFileSync(path.join(assets, 'index.html'), 'utf8'), {
    url:'https://duel.test/', runScripts:'outside-only', pretendToBeVisual:true,
  });
  const w=dom.window, d=w.document;
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  for (const file of ['game_ui_registry.js','games/mahjong.js']) w.eval(fs.readFileSync(path.join(assets,file),'utf8'));
  w.eval(fs.readFileSync(path.join(assets,'app.js'),'utf8').replace(/void \(async \(\) => \{\n  const invite[\s\S]*$/, '')+'\nwindow.run = source => eval(source);');
  const chosen={double_hu:true,gang_discard_double:true,liang_liability:true,consecutive_kong_double:true,horse_count:1,piao:5,joker:'red'};
  const defaults={double_hu:false,gang_discard_double:false,liang_liability:false,consecutive_kong_double:false,horse_count:0,piao:0,joker:'none'};
  const valuesIn = panel => Object.fromEntries([...panel.querySelectorAll('[data-kwx-option]')].map(input=>[
    input.dataset.kwxOption, input.type==='checkbox' ? input.checked : input.dataset.kwxOption==='joker' ? input.value : Number(input.value),
  ]));
  for(const id of ['gameType','inviteGame']) {
    const select=d.getElementById(id); select.replaceChildren(new w.Option('卡五星','ka_wu_xing'),new w.Option('国标','mahjong'));
    w.run(`renderGameRulePicker(document.getElementById('${id}'))`);
    const panel=d.getElementById(id+'RulePicker');
    assert.equal(panel.querySelectorAll('select').length,3);
    assert.equal(panel.querySelectorAll('input[type=checkbox]').length,4);
    assert.deepEqual([...panel.querySelectorAll('legend')].map(e=>e.textContent),['胡牌与杠分','额外玩法']);
    assert.deepEqual([...panel.querySelectorAll('fieldset')].map(e=>e.querySelectorAll('[data-kwx-option]').length),[4,3]);
    assert.equal(panel.querySelector('[data-kwx-option=rounds]'),null);
    assert.doesNotMatch(panel.textContent,/局数|4局|8局/);
    assert.equal(panel.querySelector('details').open,false);
    assert.equal(panel.querySelector('summary').textContent,'可选房规');
    assert.deepEqual(valuesIn(panel),defaults);
    assert.equal(select.dataset.kwxRules,undefined); // Viewing defaults must not write options.
    panel.querySelector('summary').click();
    assert.equal(panel.querySelector('details').open,true);
    for(const [key,val] of Object.entries(chosen)) {
      const input=panel.querySelector(`[data-kwx-option=${key}]`);
      const help=d.getElementById(input.getAttribute('aria-describedby'));
      assert.ok(help && panel.contains(help) && help.textContent);
      assert.ok(input.labels.length && input.getAttribute('aria-label'));
      assert.equal(input.disabled,false);
      if(input.type==='checkbox') {
        // Clicking the title or help activates the containing native label.
        const title=input.closest('label').querySelector('.kwx-option-title');
        title.click();assert.equal(input.checked,true);
        help.click();assert.equal(input.checked,false);
        assert.equal(JSON.parse(select.dataset.kwxRules)[key],false);
        title.click();
      } else {
        for(const option of input.options) {
          input.value=option.value;input.dispatchEvent(new w.Event('change'));
          assert.equal(JSON.parse(select.dataset.kwxRules)[key],key==='joker'?option.value:Number(option.value));
        }
        input.value=String(val);input.dispatchEvent(new w.Event('change'));
      }
    }
    panel.querySelector('summary').click();assert.equal(panel.querySelector('details').open,false);
    panel.querySelector('summary').click();assert.deepEqual(valuesIn(panel),chosen);
    assert.deepEqual(JSON.parse(w.run(`JSON.stringify(selectedGameOptions(document.getElementById('${id}')))`)),chosen);
    select.value='mahjong';w.run(`renderGameRulePicker(document.getElementById('${id}'))`);
    assert.equal(d.getElementById(id+'RulePicker'),null);
    assert.equal(w.run(`selectedGameOptions(document.getElementById('${id}'))`),undefined);
    select.value='ka_wu_xing';w.run(`renderGameRulePicker(document.getElementById('${id}'))`);
    const restored=d.getElementById(id+'RulePicker');
    assert.deepEqual(valuesIn(restored),chosen);
    assert.equal(restored.querySelector('details').open,false);
  }
  // Stale in-memory form data must not submit a retired multi-hand option.
  const createSelect=d.getElementById('gameType');
  createSelect.dataset.kwxRules=JSON.stringify({...chosen,rounds:8});
  w.run('renderGameRulePicker(document.getElementById("gameType"))');
  assert.deepEqual(JSON.parse(createSelect.dataset.kwxRules),chosen);
  // Separate forms retain independent settings, and repeated renders load CSS once.
  d.querySelector('#inviteGameRulePicker [data-kwx-option=double_hu]').click();
  assert.equal(JSON.parse(d.getElementById('inviteGame').dataset.kwxRules).double_hu,false);
  assert.equal(JSON.parse(d.getElementById('gameType').dataset.kwxRules).double_hu,true);
  assert.equal(d.querySelectorAll('#duel-game-style-mahjong').length,1);
  const cssUrl=new URL(d.getElementById('duel-game-style-mahjong').href);
  assert.equal(cssUrl.pathname,'/static/games/mahjong.css');
  assert.ok(cssUrl.searchParams.get('v'),'stylesheet has a cache key');
  assert.ok(fs.existsSync(path.join(assets,cssUrl.pathname.replace('/static/',''))));
  const appSource=fs.readFileSync(path.join(assets,'app.js'),'utf8');
  // JS and CSS change independently; verify each referenced asset and cache key.
  const rendererUrls=appSource.match(/\/static\/games\/mahjong\.js\?v=[^"\s]+/g) || [];
  assert.equal(rendererUrls.length,1,'one versioned renderer entry');
  const rendererUrl=new URL(rendererUrls[0],w.location.href);
  assert.ok(fs.existsSync(path.join(assets,rendererUrl.pathname.replace('/static/',''))));
  const appUrl=new URL(d.querySelector('script[src*="/static/app.js"]').src);
  assert.ok(appUrl.search && fs.existsSync(path.join(assets,'app.js')));
  const ids=[...d.querySelectorAll('[id]')].map(e=>e.id);
  assert.equal(new Set(ids).size,ids.length,'both forms use distinct accessible descriptions');
  const renderer=w.DuelGameUI.get('ka_wu_xing');
  const waitingRules=d.createElement('section');
  renderer.renderRoomRules(waitingRules,{game_options:chosen,rules_text:'本房固定规则：漂3，每家加付3基础分'});
  assert.match(waitingRules.textContent,/本房房规/);
  assert.match(waitingRules.textContent,/每家加付3基础分/);
  w.invitation={game_type:'ka_wu_xing',game_options:chosen,game_name:'襄阳卡五星',participant_count:1,target_player_count:3,stake:0};
  w.run(`showInviteStakePreview('ABCDEF123456', window.invitation)`);
  assert.match(d.getElementById('invitePreview').textContent,/红中万能/);
  assert.match(d.getElementById('invitePreview').textContent,/漂5/);

  assert.equal(w.DuelGameUI.get('mahjong').renderRulePicker,undefined);
  const participants=['p0','p1','p2'].map((player_id,seat_index)=>({player_id,seat_index,display_name:['南杉','小机甲','小机乙'][seat_index]}));
  const tile={id:'same-discard',code:'B5',label:'五筒'};
  const winners=['p1','p2'].map((winner_player_id,i)=>({winner_player_id,win_type:'discard',source_player_id:'p0',winning_tile:tile,multiplier:2+i*2,fans:[{name:'卡五星',multiplier:2}]}));
  const result={winner_results:winners,winner_player_ids:['p1','p2'],source_player_id:'p0',score_units:{p0:-6,p1:2,p2:4},score_breakdown:{hu:{p0:-6,p1:2,p2:4},piao:{p0:-10,p1:5,p2:5}},horse_tiles:[]};
  const state={variant:'ka_wu_xing',phase:'finished',participant_order:['p0','p1','p2'],turn_player_id:'p0',dealer_player_id:'p0',game_result:result,terminal_hands:{p0:[],p1:[tile],p2:[tile]},melds:{},discards:{},seat_winds:{p0:'东',p1:'南',p2:'西'},hand_counts:{},wall_count:12};
  const board=d.createElement('div'), controls=d.createElement('div');d.body.append(board,controls);
  let move;
  const context={state,participants,board,controls,room:{status:'finished',current_player_id:null},viewer:{player_id:'p0',seat:0},privateState:{hand:[]},uiState:{},canMove:false,legalActions:[],helpers:{setBoardLayout(){},rerender(){},submitMove:async payload=>{move=payload;return true;}}};
  renderer.renderBoard(context);renderer.renderControls(context);
  assert.match(board.textContent,/一炮双响/);assert.match(board.textContent,/终局复盘/);
  assert.equal(board.querySelectorAll('.mahjong-terminal-row.winner').length,2);
  assert.equal(board.querySelectorAll('.winning-tile').length,2);
  assert.equal(board.querySelectorAll('.mahjong-fan-row').length,2);
  assert.match(board.textContent,/漂 5/);assert.doesNotMatch(board.textContent,/累计|第 1 局|下一局/);
  assert.equal(board.querySelectorAll('button.mahjong-tile').length,0);
  assert.equal(controls.querySelectorAll('button').length,0);
  context.state={...state,phase:'discard',game_result:null,terminal_hands:{},revealed:{}};context.uiState={};context.room.status="playing";
  renderer.renderBoard(context);assert.equal(board.querySelector('.mahjong-terminal-review'),null);
  context.state={...state,phase:'finished',game_result:result,rounds:4,cumulative_scores:result.score_units,round_results:[result,result]};context.room.status='finished';
  renderer.renderBoard(context);renderer.renderControls(context);
  assert.match(board.textContent,/已完成 2 局/);assert.equal(controls.querySelectorAll('button').length,0);

  w.matchRoom={game_type:'ka_wu_xing',status:'finished',game_options:{rounds:4},participants,board_state:{},result:{...result,match_winner_player_ids:['p0']}};
  assert.match(w.run(`resultTextFor(window.matchRoom)`),/南杉 累计领先/);
  console.log('PASS (DOM only): normal/invite 4+3 groups, defaults, label toggles, all enum values, collapse/reopen and game-switch persistence, independent forms, accessible labels/descriptions, static asset references; MCR isolation and single-hand/result and read-only history UI regression. No browser layout verification.');
  dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
