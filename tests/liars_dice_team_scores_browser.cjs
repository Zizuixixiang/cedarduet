/* Full page + real ASGI API, temporary DB. DICE_DOM_ONLY=1 checks behavior only. */
const assert = require('node:assert/strict'), fs = require('node:fs');
const {backend, driver, until, output, domOnly} = require('./liars_dice_lobby_browser.cjs');
(async () => {
  let browser;
  try {
    if (!domOnly) browser = await require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium.launch({headless:true,args:['--no-sandbox']});
    for (const width of domOnly ? [null] : [360,430,1280]) {
      const api = backend(); let ui;
      try {
        const call = async (url, body, actor='human-1') => {
          const reply = await api.request(url, 'POST', body, actor);
          assert.equal(reply.status, 200, reply.body);
          return JSON.parse(reply.body);
        };
        ui = await driver(browser, api, width);
        await until(ui, 'identity');
        let room = (await call('/api/rooms', {game_type:'liars_dice', mode:'human_first',
          ai_players:['ai-1','ai-2','ai-3'], target_player_count:4,
          game_options:{mode:'party',team_mode:'teams'}})).room;
        const rid = room.room_id;
        assert.equal(room.board_state.team_rules_version, 2);
        assert.equal(room.board_state.max_bid_quantity, 28);
        await ui.run(`openRoom(${JSON.stringify(rid)})`);
        await until(ui, `room?.room_id === '${rid}' && document.querySelector('#privateStateContent .my-dice')`);
        assert.equal(await ui.run(`document.querySelectorAll('#privateStateContent .liars-die').length`), 5);
        assert.equal(await ui.run(`$('privateStateContent').textContent.includes('我的骰子')`), true);
        assert.equal(await ui.run(`$('privateStateContent').textContent.includes('共用骰子')`), false);
        // Internal permissions drive controls, never raw private-state rows.
        for (const canRush of [false, true]) {
          await ui.run(`room.private_state.can_rush = ${canRush}; renderPrivateState(room)`);
          assert.equal(await ui.run(`$('privateStateContent').textContent.includes('can_rush')`), false);
          assert.equal(await ui.run(`/true|false/.test($('privateStateContent').textContent)`), false);
          assert.equal(await ui.run(`room.private_state.can_rush`), canRush);
          assert.equal(await ui.run(`document.querySelectorAll('#privateStateContent .liars-die').length`), 5);
        }
        // Keep generic private-field rendering for other games intact.
        await ui.run(`renderPrivateState({game_type:'other', board_state:{}, private_state:{hand:'保留手牌', can_rush:false}})`);
        assert.equal(await ui.run(`$('privateStateContent').textContent.includes('保留手牌')`), true);
        assert.equal(await ui.run(`$('privateStateContent').textContent.includes('can_rush')`), true);
        await ui.run(`room.private_state.can_rush = false; renderPrivateState(room)`);
        assert.equal(await ui.run(`$('board').textContent.includes('共用骰盅')`), false);
        assert.equal(await ui.run(`document.querySelectorAll('.liars-team-score-card').length`), 2);
        assert.equal(await ui.run(`document.querySelector('select[aria-label="叫点规则"]').value`), 'false');
        await ui.shot('team-personal-dice', '#privateStatePanel');
        await call(`/api/rooms/${rid}/move`, {revision:room.revision, move:{action:'bid',quantity:6,face:2}});
        room = JSON.parse((await api.request(`/api/rooms/${rid}`)).body).room;
        const teammates = Object.values(room.board_state.teams).find(ids => ids.includes('human-1'));
        const teammate = teammates.find(pid => pid !== 'human-1');
        const available = await call('/mcp/play', {action:'state',room_id:rid,full_state:true}, teammate);
        assert.deepEqual(available.actions, [{action:'rush_open'}]);
        assert.equal(available.snapshot.private_state.can_rush, true);
        assert.equal(available.snapshot.private_state.cup_owner_player_id, teammate);
        assert.equal(available.snapshot.private_state.dice.length, 5);
        assert.equal('dice_by_player' in available.snapshot.board_state, false);
        await ui.run('refreshRoom()');
        await until(ui, `document.querySelector('.liars-round-heading').textContent.includes('同队可抢')`);
        assert.equal(await ui.run(`document.querySelector('.liars-controls > button:nth-of-type(2)').disabled`), true);
        await call('/mcp/play', {action:'move',room_id:rid, revision:room.revision, move:{action:'rush_open'}},teammate);
        await ui.run(`openRoom(${JSON.stringify(rid)})`);
        await until(ui, `room?.board_state.flow.phase === 'awaiting_round_acknowledgement'`);
        assert.equal(await ui.run(`Object.keys(room.board_state.last_round_result.revealed_dice_by_player).length`), 4);
        assert.equal(await ui.run(`room.board_state.last_round_result.penalties.length`), 1);
        assert.equal(await ui.run(`Object.values(room.board_state.team_scores).reduce((a,b)=>a+b,0)`), 0);
        assert.equal(await ui.run(`$('board').textContent.includes('共用骰盅')`), false);
        await ui.shot('team-four-cups-revealed', '#board');
        // Legacy projection still labels the persisted shared cups accurately.
        await ui.run(`room.board_state.team_rules_version = 1; renderPrivateState(room); renderLiarsDice($('board'), room.board_state)`);
        assert.equal(await ui.run(`$('privateStateContent').textContent.includes('我队共用骰子')`), true);
        assert.equal(await ui.run(`$('board').textContent.includes('共用骰盅')`), true);
        await ui.run(`delete room.board_state.team_rules_version; renderPrivateState(room)`);
        assert.equal(await ui.run(`$('privateStateContent').textContent.includes('我队共用骰子')`), true);
        assert.deepEqual(ui.errors, []);
        console.log(JSON.stringify({mode:domOnly?'DOM':width, privateDice:5, revealedCups:4, teammateRush:true, charges:1, legacyLabels:true}));
      } finally { if (ui) await ui.close(); await api.close(); }
    }
  } finally {
    if (browser) await browser.close();
    if (!process.env.DICE_SCREENSHOTS) fs.rmSync(output,{recursive:true,force:true});
  }
})().catch(error => {console.error(error); process.exitCode=1;});
