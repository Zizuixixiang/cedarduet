/* Focused paid manual-team lifecycle. Uses the existing disposable ASGI bridge.
   DICE_DOM_ONLY=1 JSDOM_MODULE=/path/to/jsdom node tests/liars_dice_pending_browser.cjs */
const assert = require('node:assert/strict');
const {backend, driver, until, domOnly} = require('./liars_dice_lobby_browser.cjs');
(async () => {
  let browser;
  try {
    if (!domOnly) browser = await require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium.launch({headless:true, args:['--no-sandbox']});
    for (const width of domOnly ? [null] : [360, 430, 1280]) {
      const api = backend(); let ui;
      try {
        const created = await api.request('/api/rooms', 'POST', {
          game_type:'liars_dice', ai_players:['ai-1', 'ai-2'], stake:3, target_player_count:4,
          game_options:{mode:'party', team_mode:'teams', random_teams:false},
        });
        assert.equal(created.status, 200, created.body);
        const rid = JSON.parse(created.body).room.room_id;
        ui = await driver(browser, api, width);
        await until(ui, `identity && !!document.querySelector('#pendingList button')`);
        // Ordinary pending invites and other games keep their original view classification.
        assert.equal(await ui.run(`[
          {status:'pending',room_kind:'invite',game_type:'liars_dice'},
          {status:'pending',room_kind:'invite',game_type:'uno'},
          {status:'playing',game_type:'liars_dice',team_selection:{}},
        ].some(isInviteWaiting)`), false);
        // Reopen from the actual pending invitation card, then repeat via the room route.
        await ui.run(`Array.from($('pendingList').querySelectorAll('button')).find(b=>b.textContent.includes('查看房间')).click()`);
        await until(ui, `room?.room_id === '${rid}' && !!document.querySelector('.liars-team-seat')`);
        await ui.run(`openRoom('${rid}', {historyMode:'replace'})`);
        const assertPending = async () => {
          assert.equal(await ui.run(`room.status`), 'pending');
          assert.equal(await ui.run(`$('gameView').classList.contains('invite-waiting-view')`), true);
          assert.equal(await ui.run(`$('board').children.length`), 0);
          assert.equal(await ui.run(`document.querySelectorAll('.liars-team-seat:disabled').length`), 4);
          assert.equal(await ui.run(`document.querySelector('[data-action="开始游戏"]').disabled`), true);
          assert.ok(await ui.run(`$('inviteRoomPanel').textContent.includes('等待所有真实参赛者确认筹码')`));
          assert.ok(await ui.run(`$('inviteRoomPanel').textContent.includes('待确认')`));
          assert.equal(await ui.run(`JSON.stringify(room.private_state)`), '{}');
        };
        await assertPending();
        const postCount = api.posts.length;
        await ui.run(`document.querySelector('.liars-team-seat').click(); document.querySelector('[data-action="开始游戏"]').click(); chooseDiceTeamSeat('red',1)`);
        assert.equal(api.posts.length, postCount, 'disabled controls must not send requests');
        assert.equal((await api.request('/mcp/play','POST',{action:'accept',room_id:rid},'ai-1')).status, 200);
        await ui.run('refreshRoom({quiet:true})');
        await assertPending();
        assert.ok(await ui.run(`$('inviteRoomPanel').textContent.includes('已确认')`));
        await ui.shot('pending-teams', '#inviteRoomPanel');
        assert.equal((await api.request('/mcp/play','POST',{action:'accept',room_id:rid},'ai-2')).status, 200);
        await ui.run('refreshRoom({quiet:true})');
        assert.equal(await ui.run('room.status'), 'waiting');
        assert.equal(await ui.run('room.team_selection.can_start'), false);
        assert.equal(await ui.run(`document.querySelector('[data-action="开始游戏"]').disabled`), true);
        await ui.click('[aria-label="加入红队第1位"]');
        await until(ui, `room.team_selection.seats.red[0] === 'human-1'`);
        await ui.shot('waiting-teams', '#inviteRoomPanel');
        // Real timer refresh, including can_start changes without a revision change.
        await until(ui, `room.team_selection.can_start`, 24000);
        assert.equal(await ui.run(`document.querySelector('[data-action="开始游戏"]').disabled`), false);
        await ui.click('[data-action="开始游戏"]');
        await until(ui, `room.status === 'playing'`);
        assert.equal(await ui.run(`$('gameView').classList.contains('invite-waiting-view')`), false);
        assert.equal(await ui.run(`$('inviteRoomPanel').classList.contains('hidden')`), true);
        assert.ok(await ui.run(`$('board').children.length > 0`));
        assert.equal(await ui.run(`'dice_by_player' in room.board_state`), false);
        assert.equal(await ui.run('room.private_state.dice.length'), 5);
        assert.deepEqual(ui.errors, []);
        console.log(`PASS ${width || 'DOM'}: pending card/reentry → partial confirmation → waiting/seats → 20s/start → playing; disabled controls send no requests`);
      } finally {if (ui) await ui.close(); await api.close();}
    }
  } finally {if (browser) await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
