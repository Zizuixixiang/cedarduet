/* Local ASGI + temporary DB. DICE_DOM_ONLY=1 needs only existing jsdom.
   Optional installed Playwright checks the same states at 360/430/1280px. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {backend, driver, until, output, domOnly} = require('./liars_dice_lobby_browser.cjs');
(async () => {
  let browser;
  try {
    if (!domOnly) browser = await require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium.launch({headless:true, args:['--no-sandbox']});
    for (const width of domOnly ? [null] : [360, 430, 1280]) {
      const api = backend(); let ui;
      const call = async (url, body, actor='human-1', method='POST') => {
        const reply = await api.request(url, method, body, actor);
        assert.equal(reply.status, 200, reply.body);
        return JSON.parse(reply.body);
      };
      try {
        ui = await driver(browser, api, width);
        await until(ui, 'identity && !$("lobbyView").classList.contains("hidden")');
        for (const team of [false, true]) {
          const count = team ? 4 : 2;
          let room = (await call('/api/invites', {game_type:'liars_dice', target_player_count:count,
            game_options:{mode:'party', rush_open:false, allow_surrender:true, surrender_no_reroll:true,
              ...(team ? {team_mode:'teams'} : {})}})).room;
          for (let i=2; i<=count; i++) await call('/api/invites/join', {invite_code:room.invite_code}, `human-${i}`);
          room = (await call(`/api/rooms/${room.room_id}/start`, {})).room;
          const rid = room.room_id;
          await ui.run(`openRoom(${JSON.stringify(rid)})`);
          await until(ui, `room?.room_id === '${rid}' && room.status === 'playing'`);
          const scoresOnly = async () => {
            assert.equal(await ui.run(`document.querySelectorAll('.liars-party-scores details, .liars-party-scores .liars-result-line').length`), 0);
            assert.equal(await ui.run(`$('board').textContent.includes('回合扣分记录')`), false);
            assert.equal(await ui.run(`document.querySelectorAll('.liars-party-scores').length`), 1);
            assert.equal(await ui.run(`document.querySelectorAll('${team ? '.liars-team-score-card' : '.liars-score-row'}').length`), team ? 2 : count);
          };
          await scoresOnly(); // Zero rounds.
          assert.equal(await ui.run(`document.querySelectorAll('.awaiting-acknowledgement').length`), 0);
          for (let round=1; round<=2; round++) {
            room = (await call(`/api/rooms/${rid}/move`, {revision:room.revision,
              move:{action:'bid', quantity:10, face:6, exact:true}}, room.current_player_id)).room;
            room = (await call(`/api/rooms/${rid}/move`, {revision:room.revision,
              move:{action:round === 1 ? 'challenge' : 'surrender'}}, room.current_player_id)).room;
            assert.equal(room.current_player_id, 'human-1');
            await ui.run('refreshRoom({quiet:true})');
            await until(ui, `room?.revision === ${room.revision}`);
            await scoresOnly();
            assert.equal(await ui.run(`$('board').firstElementChild.classList.contains('awaiting-acknowledgement')`), true);
            assert.equal(await ui.run(`document.querySelector('.liars-result-title').textContent`), `第 ${round} 轮结算`);
            const text = await ui.run(`document.querySelector('.awaiting-acknowledgement').textContent`);
            assert.ok(text.includes('叫 10 个 6 点（斋）'));
            assert.ok(text.includes(round === 1 ? '实际' : '投降认罚'));
            assert.ok(text.includes('累计'));
            assert.equal(await ui.run(`document.querySelector('.liars-round-acknowledgement button').disabled`), false);
            assert.equal(await ui.run(`document.querySelectorAll('.liars-penalty-summary').length`), 0);
            assert.equal(await ui.run(`document.querySelector('.liars-penalty-details').open`), false);
            if (!domOnly) {
              assert.equal(await ui.run('document.documentElement.scrollWidth <= innerWidth'), true);
              assert.ok(await ui.run(`document.querySelector('.liars-round-acknowledgement button').getBoundingClientRect().height >= 44`));
              await ui.shot(`ack-${team ? 'teams' : 'solo'}-${round}`, '.awaiting-acknowledgement');
            }
            // Other participants get a waiting message and no confirmation control.
            const other = await call(`/api/rooms/${rid}`, undefined, 'human-2', 'GET');
            await ui.run(`renderGame(${JSON.stringify(other.room)}, null, ${JSON.stringify(other.timeline)})`);
            assert.equal(await ui.run(`document.querySelectorAll('.liars-round-acknowledgement button').length`), 0);
            assert.ok((await ui.run(`document.querySelector('.liars-round-acknowledgement').textContent`)).includes('等待'));
            assert.equal(await ui.run('acknowledgeLiarsRound()'), false);
            const denied = await api.request(`/api/rooms/${rid}/move`, 'POST',
              {revision:room.revision, move:{action:'acknowledge_round'}}, 'human-2');
            assert.equal(denied.status, 409);
            await ui.run('refreshRoom({quiet:true})');
            await until(ui, `room.viewer.player_id === 'human-1'`);
            // Refresh/reentry keeps the authoritative wait; a click alone advances.
            assert.equal(await ui.run('room.board_state.flow.round_number'), round);
            const starter = room.board_state.pending_next_round.starter_player_id;
            const beforeScores = room.board_state.scores;
            await ui.click('.liars-round-acknowledgement button');
            await until(ui, `room.board_state.flow.round_number === ${round + 1}`);
            room = JSON.parse(await ui.run('JSON.stringify(room)'));
            assert.equal(room.current_player_id, starter);
            assert.deepEqual(room.board_state.scores, beforeScores);
          }
          await scoresOnly(); // Two rounds, no second history inside the scores.
          await ui.click('#historyDrawerTab');
          assert.equal(await ui.run(`$('historyDrawer').getAttribute('aria-hidden')`), 'false');
          const history = await ui.run(`$('timeline').textContent`);
          assert.ok(history.includes('开') && history.includes('投降'), history);
          await ui.click('#closeHistoryButton');
          assert.deepEqual(ui.errors, []);
        }
        console.log(`PASS ${domOnly ? 'DOM + temporary ASGI (no visual validation)' : width+'px'}: 0/2 rounds, result first, designated human click, peer denied, scores only, sidebar history`);
      } finally {if (ui) await ui.close(); await api.close();}
    }
  } finally {
    if (browser) await browser.close();
    if (!process.env.DICE_SCREENSHOTS) fs.rmSync(output, {recursive:true, force:true});
  }
})().catch(error => {console.error(error); process.exitCode=1;});
