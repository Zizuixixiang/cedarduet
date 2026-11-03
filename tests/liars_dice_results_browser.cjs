/* Same real ASGI/temporary DB and full page as the lobby test. No installation.
   Default: installed Chromium at 360/430/1280. DICE_DOM_ONLY=1 is NOT visual proof. */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {backend, driver, until, output, domOnly} = require('./liars_dice_lobby_browser.cjs');
(async () => {
  let browser;
  const evidence = [];
  try {
    if (!domOnly) browser = await require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium.launch({headless:true, args:['--no-sandbox']});
    for (const width of domOnly ? [null] : [360, 430, 1280]) {
      const api = backend(); let ui;
      try {
        const call = async (url, body, actor = 'human-1', method = 'POST') => {
          const response = await api.request(url, method, body, actor);
          assert.equal(response.status, 200, response.body);
          return JSON.parse(response.body);
        };
        ui = await driver(browser, api, width);
        await until(ui, `identity && !$('lobbyView').classList.contains('hidden')`);
        for (const scenario of ['zero', 'bid-only', 'solo', 'teams', 'teams-npc', 'classic']) {
          const team = scenario.startsWith('teams');
          const count = team ? 6 : scenario === 'solo' ? 3 : 2;
          const options = scenario === 'classic' ? {mode:'classic'} : {mode:'party', allow_surrender:true,
            surrender_no_reroll:true, ...(team ? {team_mode:'teams'} : {})};
          let room = (await call('/api/invites', {game_type:'liars_dice', target_player_count:count, game_options:options})).room;
          await call('/api/invites/join', {invite_code:room.invite_code}, 'human-2');
          if (scenario !== 'teams-npc') for (let i = 3; i <= count; i++) {
            await call('/api/invites/join', {invite_code:room.invite_code}, `human-${i}`);
          }
          room = (await call(`/api/rooms/${room.room_id}/start`, {fill_with_npcs:count > 2})).room;
          const rid = room.room_id;
          if (!['zero', 'classic', 'teams-npc'].includes(scenario)) {
            // Invite seat order is random; each move uses the real current identity.
            const actor = room.participants.find(p => p.player_id === room.current_player_id);
            if (actor.participant_kind !== 'system_npc') {
              room = (await call(`/api/rooms/${rid}/move`, {revision:room.revision, move:{action:'bid', quantity:count === 3 ? 5 : 3, face:2}}, actor.player_id)).room;
              const next = room.participants.find(p => p.player_id === room.current_player_id);
              if (['solo', 'teams'].includes(scenario) && next.participant_kind !== 'system_npc') {
                room = (await call(`/api/rooms/${rid}/move`, {revision:room.revision, move:{action:'surrender'}}, next.player_id)).room;
              }
            }
          }
          await ui.run(`openRoom(${JSON.stringify(rid)})`);
          await until(ui, `room?.room_id === '${rid}' && room.status === 'playing'`);
          await ui.run('window.confirm = () => true');
          await ui.click('#resignButton');
          await until(ui, `room?.status === 'finished' && !$('resultModal').classList.contains('hidden')`);
          const saved = JSON.parse(await ui.run('JSON.stringify(room.result)'));
          if (scenario === 'classic') {
            assert.equal(await ui.run(`$('partyResultRanking').classList.contains('hidden')`), true);
            assert.equal(await ui.run(`$('resultModal').classList.contains('liars-result-active')`), false);
            assert.equal(await ui.run(`$('partyResultRanking').childElementCount`), 0);
          } else {
            assert.equal(saved.mode, 'party');
            assert.equal(await ui.run(`document.querySelectorAll('#partyResultRanking .liars-final-row').length`), team ? 2 : count);
            assert.equal(await ui.run(`document.querySelectorAll('#partyResultRanking .liars-final-member').length`), count);
            assert.equal(await ui.run(`$('resultBannerText').textContent.includes('和棋')`), false);
            assert.equal(await ui.run(`$('resultModalText').textContent`), saved.result_text);
            assert.equal(await ui.run(`document.querySelectorAll('#partyResultRanking .is-leader').length`), saved.rankings.filter(r => r.rank === 1).length);
            if (['zero', 'bid-only'].includes(scenario)) assert.equal(saved.outcome, 'no_contest');
            if (['solo', 'teams'].includes(scenario)) assert.equal(saved.completed_rounds, 1);
            const content = await ui.run(`$('partyResultRanking').textContent`);
            if (!domOnly) {
              assert.equal(await ui.run(`$('partyResultRanking').scrollWidth <= $('partyResultRanking').clientWidth`), true);
              assert.equal(await ui.run(`document.documentElement.scrollWidth <= innerWidth`), true);
            }
            await ui.shot(`results-${scenario}`, '#resultModal');
            await ui.click('#finishGameButton');
            await ui.run('refreshRoom({quiet:true})');
            assert.equal(await ui.run(`$('resultModal').classList.contains('hidden')`), true, 'poll must not reopen a dismissed modal');
            await ui.click('#backButton');
            await until(ui, `!room && !!document.querySelector('#roomList .room-open[aria-label$="${rid}"]')`);
            assert.equal(await ui.run(`document.querySelector('#roomList .room-open[aria-label$="${rid}"]').textContent.includes('和棋')`), false);
            await ui.reload();
            await until(ui, `!!document.querySelector('#roomList .room-open[aria-label$="${rid}"]')`);
            await ui.click(`#roomList .room-open[aria-label$="${rid}"]`);
            await until(ui, `room?.room_id === '${rid}' && !$('resultModal').classList.contains('hidden')`);
            assert.equal(await ui.run(`$('partyResultRanking').textContent`), content);
            assert.deepEqual(JSON.parse(await ui.run('JSON.stringify(room.result)')), saved);
            evidence.push({width, scenario, completed_rounds:saved.completed_rounds, outcome:saved.outcome, reloadSameRanking:true});
          }
          await ui.click('#finishGameButton');
          await ui.click('#backButton');
          await until(ui, `!room && !$('lobbyView').classList.contains('hidden')`);
        }
        // Renderer boundaries: champion/ties, long names, avatar fallback, safe text.
        await ui.run(`(() => {
          const fixture = {status:'finished', participants:[
            {player_id:'a', display_name:'<img onerror=alert(1)>很长的参赛昵称'.repeat(3), role:'human', avatar:{type:'emoji',value:'🌲'}},
            {player_id:'b', display_name:'另一位', role:'ai'}], result:{outcome:'ranked', rankings:[
              {rank:1, score:-.5, player_ids:['a']}, {rank:1, score:-.5, player_ids:['b']} ]}};
          $('partyResultRanking').replaceChildren();
          window.DuelDiceResults.render($('partyResultRanking'), fixture, renderParticipantAvatar);
        })()`);
        assert.equal(await ui.run(`document.querySelectorAll('#partyResultRanking .is-leader').length`), 2);
        assert.equal(await ui.run(`document.querySelector('#partyResultRanking .liars-final-place').textContent`), '并列第一');
        assert.equal(await ui.run(`document.querySelectorAll('#partyResultRanking img').length`), 0);
        assert.deepEqual(ui.errors, []);
        console.log(`PASS ${domOnly ? 'DOM + real ASGI (no visual validation)' : width + 'px Chromium'}: end modal, authoritative ranking, lobby/reload/reentry, classic unchanged, ties, safe names`);
      } finally {if (ui) await ui.close(); await api.close();}
    }
    fs.writeFileSync(path.join(output, domOnly ? 'results-dom-evidence.json' : 'results-browser-evidence.json'), JSON.stringify(evidence, null, 2));
  } finally {
    if (browser) await browser.close();
    if (!process.env.DICE_SCREENSHOTS) fs.rmSync(output, {recursive:true, force:true});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
