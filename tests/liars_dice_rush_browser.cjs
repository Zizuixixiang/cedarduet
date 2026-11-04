/* Real page + ASGI + temporary SQLite; includes a real ten-second deadline.
   Run with DICE_DOM_ONLY=1 when no installed Chromium is available. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {backend, driver, until, domOnly, output} = require('./liars_dice_lobby_browser.cjs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  let browser;
  try {
    if (!domOnly) browser = await require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium.launch({headless:true,args:['--no-sandbox']});
    for (const width of domOnly ? [null] : [360,430,1280]) {
      const api = backend(); let ui;
      try {
        const call = async (url, body, actor='human-1', method='POST') => {
          const response = await api.request(url,method,body,actor);
          assert.equal(response.status,200,response.body);
          return JSON.parse(response.body);
        };
        ui = await driver(browser,api,width);
        await until(ui, `identity && !$('lobbyView').classList.contains('hidden')`);
        await ui.choose('#gameCategory','dice');
        await ui.choose('#gameType','liars_dice');
        await ui.choose('#gameTypeRulePicker select[aria-label="吹牛骰子玩法"]','party');
        const picker='#gameTypeRulePicker';
        assert.equal(await ui.run(`document.querySelector('${picker} details').open`),false);
        await ui.click(`${picker} summary`);
        const checkbox=`${picker} input[aria-label="抢开 · 默认开启 · 每次叫点后10秒"]`;
        assert.equal(await ui.run(`document.querySelector('${checkbox}').checked`),true);
        await ui.click(checkbox);
        assert.equal(await ui.run(`selectedGameOptions($('gameType')).rush_open`),false);
        await ui.click(checkbox);
        assert.equal(await ui.run(`selectedGameOptions($('gameType')).rush_open`),true);
        await ui.shot('rush-options',picker);
        let room=(await call('/api/rooms',{game_type:'liars_dice',mode:'human_first',ai_players:['ai-1','ai-2','ai-3'],
          target_player_count:4,game_options:{mode:'party'}})).room;
        assert.equal(room.game_options.rush_open,true);
        const rid=room.room_id, seats=room.participants.map(p=>p.player_id);
        await ui.run(`openRoom('${rid}')`);
        await until(ui,`room?.room_id === '${rid}' && !!document.querySelector('.liars-controls')`);
        const assertPrivateDice = async canRush => {
          assert.equal(await ui.run('room.private_state.can_rush'), canRush);
          assert.doesNotMatch(await ui.run("$('privateStateContent').textContent"), /can_rush|can_reroll|team_id|cup_owner_player_id/);
          assert.match(await ui.run("$('privateStateContent').textContent"), /我的骰子/);
          assert.deepEqual(JSON.parse(await ui.run(`JSON.stringify([...document.querySelectorAll('#privateStateContent .liars-die')].map(d => Number(d.dataset.face)))`)),
            JSON.parse(await ui.run('JSON.stringify(room.private_state.dice)')));
        };
        await assertPrivateDice(false);
        assert.equal(await ui.run(`document.querySelector('select[aria-label="叫点规则"]').value`),'false');
        // Existing normal-bid default is preserved, and the first bid travels through the real UI.
        await ui.click('.liars-controls > button:first-of-type');
        await until(ui, `room?.board_state?.rush_until && !!document.querySelector('.liars-round-heading').textContent.includes('剩余')`);
        const first=JSON.parse(await ui.run('JSON.stringify(room)'));
        assert.equal(Object.hasOwn(first.board_state, 'server_time'), false);
        assert.ok(Number.isFinite(first.rushServerTime));
        assert.ok(first.board_state.rush_until - first.rushServerTime <= 10);
        await assertPrivateDice(false);
        assert.equal(first.board_state.current_bid.exact,false);
        assert.equal(await ui.run(`document.querySelector('.liars-controls > button:nth-of-type(2)').textContent`),'抢开');
        assert.equal(await ui.run(`document.querySelector('.liars-controls > button:nth-of-type(2)').disabled`),true,'cannot rush own bid');
        const deadline=first.board_state.rush_until;
        await pause(1200);
        await ui.reload();
        await until(ui, `identity`);
        if (domOnly) await ui.run(`openRoom('${rid}')`);
        await until(ui, `room?.room_id === '${rid}' && room.board_state?.rush_until === ${deadline}`);
        await until(ui, `document.querySelector('.liars-round-heading').textContent.includes('剩余')`);
        assert.equal(await ui.run(`document.querySelector('.liars-round-heading').textContent.includes('剩余 10 秒')`),false,'reload cannot reset timer');
        // Page timer triggers the expiry refresh without fixture polling or changing server time.
        await until(ui,`room?.room_id === '${rid}' && !room.board_state.rush_until`,12000);
        const expired=JSON.parse(await ui.run('JSON.stringify(room)'));
        assert.equal(expired.revision,first.revision+1);
        assert.deepEqual(expired.participants.map(p=>p.player_id),seats);
        const next=expired.current_player_id;
        assert.notEqual(next,'human-1');
        const response=await call('/mcp/play',{action:'move',room_id:rid,revision:expired.revision,
          move:{action:'bid',quantity:7,face:6}},next);
        assert.ok(response.r>expired.revision);
        await ui.run('refreshRoom()');
        await until(ui,`room?.board_state?.current_bid?.bidder_player_id === '${next}'`);
        assert.equal(await ui.run(`canHumanMove()`),false,'human is not the next seat');
        assert.equal(await ui.run(`document.querySelector('.liars-controls > button:nth-of-type(2)').disabled`),false);
        await assertPrivateDice(true);
        await ui.shot('rush-active','.liars-current-round');
        if (!domOnly) {
          const m=JSON.parse(await ui.run(`JSON.stringify([...document.querySelectorAll('.liars-controls > button')].map(e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,height:r.height};}))`));
          assert.ok(m.every(r=>r.left>=0&&r.right<=width&&r.height>=44));
          assert.equal(await ui.run('document.documentElement.scrollWidth<=innerWidth'),true);
        }
        await ui.click('.liars-controls > button:nth-of-type(2)');
        await until(ui,`room?.board_state?.flow?.phase === 'awaiting_round_acknowledgement'`);
        const settled=JSON.parse(await ui.run('JSON.stringify(room)'));
        assert.equal(settled.board_state.last_round_result.challenger_player_id,'human-1');
        assert.equal(settled.board_state.round_results.length,1);
        assert.equal(Object.values(settled.board_state.scores).reduce((a,b)=>a+b,0),-1);
        const again=await api.request(`/api/rooms/${rid}/move`,'POST',{revision:settled.revision-1,move:{action:'rush_open'}});
        assert.equal(again.status,409);
        assert.ok(api.posts.some(p=>p.body?.move?.action==='rush_open'));
        assert.deepEqual(ui.errors,[]);
        console.log(`PASS ${domOnly?'jsdom (not visual)':width}: defaults, real 10s, reconnect, normal next bid, out-of-turn UI rush, stale duplicate`);
      } finally {if(ui) await ui.close();await api.close();}
    }
  } finally {
    if(browser) await browser.close();
    if(domOnly && !process.env.DICE_SCREENSHOTS) fs.rmdirSync(output);
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
