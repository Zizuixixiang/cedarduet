/* Real ASGI lifecycle, disposable DB. Default: Chromium at 360/430/1280.
   DICE_DOM_ONLY=1 runs the same actions in jsdom without visual assertions.
   PLAYWRIGHT_MODULE / JSDOM_MODULE can point to installed dependencies.
   DICE_SCREENSHOTS retains screenshots and measurements when provided. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawn} = require('node:child_process'), readline = require('node:readline');
const root = path.resolve(__dirname, '..'), domOnly = process.env.DICE_DOM_ONLY === '1';
const output = process.env.DICE_SCREENSHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'dice-lobby-evidence-'));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
fs.mkdirSync(output, {recursive: true});
function backend(playingRooms = 0) {
  const child = spawn(path.join(root, '.venv/bin/python'), [path.join(__dirname, 'liars_dice_asgi_bridge.py')],
    {env: {...process.env, DICE_TEST_PLAYING_ROOMS: String(playingRooms)}});
  child.stderr.pipe(process.stderr);
  const pending = new Map(), posts = []; let serial = 0;
  readline.createInterface({input: child.stdout}).on('line', line => {
    const reply = JSON.parse(line); pending.get(reply.id)?.(reply); pending.delete(reply.id);
  });
  return {posts, close: async () => {child.stdin.end(); await new Promise(resolve => child.once('exit', resolve));},
    request: (url, method = 'GET', body, actor = 'human-1') => new Promise(resolve => {
      const id = ++serial; pending.set(id, resolve);
      if (method === 'POST') posts.push({url, body, actor});
      child.stdin.write(JSON.stringify({id, path: url, method, body, actor}) + '\n');
    })};
}
async function driver(browser, backend, width) {
  if (!domOnly) {
    const page = await browser.newPage({viewport: {width, height: 900}, isMobile: width < 600, hasTouch: width < 600});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.hostname !== 'dice.test') return route.abort();
      if (url.pathname === '/' || url.pathname.startsWith('/static/')) {
        const file = path.join(root, 'app/static', url.pathname === '/' ? 'index.html' : url.pathname.slice(8));
        return route.fulfill({body: fs.readFileSync(file), contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
      }
      const reply = await backend.request(url.pathname + url.search, req.method(), req.postData() ? req.postDataJSON() : undefined);
      await route.fulfill({status: reply.status, body: reply.body, contentType: 'application/json'});
    });
    await page.goto('http://dice.test/');
    await page.evaluate(() => {stopPolling(); startRoomPolling = () => {};});
    return {errors, run: source => page.evaluate(source => eval(source), source),
      click: selector => page.locator(selector)[width < 600 ? 'tap' : 'click'](),
      choose: (selector, value) => page.locator(selector).selectOption(value),
      reload: async () => {await page.reload(); await page.evaluate(() => {stopPolling(); startRoomPolling = () => {};});}, close: () => page.close(),
      shot: async (name, selector) => {await page.locator(selector).scrollIntoViewIfNeeded(); await page.screenshot({path: path.join(output, `${name}-${width}.png`)});}};
  }
  const {JSDOM, VirtualConsole} = require(process.env.JSDOM_MODULE || 'jsdom');
  const vm = require('node:vm'), errors = []; let dom;
  const load = () => {
    const vc = new VirtualConsole(); vc.on('jsdomError', e => {if (!e.message.includes('CSS')) errors.push(e.message);});
    dom = new JSDOM(fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8'), {
      url: 'http://dice.test/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
      beforeParse(w) {
        w.matchMedia = () => ({matches: false, addEventListener() {}});
        w.fetch = async (url, options = {}) => {
          const reply = await backend.request(url, options.method, options.body ? JSON.parse(options.body) : undefined);
          return {ok: reply.status < 400, status: reply.status, headers: {get: () => null}, json: async () => JSON.parse(reply.body)};
        };
        w.HTMLElement.prototype.scrollIntoView = () => {};
        w.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
        w.HTMLDialogElement.prototype.close = function() {this.open = false;};
      },
    });
    const w = dom.window, append = w.document.head.appendChild.bind(w.document.head);
    const execute = script => vm.runInContext(fs.readFileSync(path.join(root, 'app', new URL(script.src).pathname), 'utf8'), dom.getInternalVMContext());
    w.document.head.appendChild = node => {append(node); if (node.tagName === 'SCRIPT') {execute(node); node.dispatchEvent(new w.Event('load'));} return node;};
    for (const script of w.document.querySelectorAll('script[src]')) execute(script);
    // jsdom has no transport cancellation; polling is tested separately.
    w.eval('startRoomPolling = () => {};');
  };
  load();
  return {errors, run: async source => dom.window.eval(source),
    click: async selector => dom.window.document.querySelector(selector).click(),
    choose: async (selector, value) => {const node = dom.window.document.querySelector(selector); node.value = value; node.dispatchEvent(new dom.window.Event('change'));},
    reload: async () => {dom.window.close(); load();}, close: async () => dom.window.close(), shot: async () => {}};
}
async function until(ui, source, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (!await ui.run(source)) {if (Date.now() > deadline) throw new Error(`Timed out: ${source}`); await sleep(50);}
}
module.exports = {backend, driver, until, output, domOnly};
if (require.main === module) (async () => {
  let browser;
  const evidence = [];
  try {
    if (!domOnly) browser = await require(process.env.PLAYWRIGHT_MODULE || 'playwright').chromium.launch({headless: true, args: ['--no-sandbox']});
    for (const width of domOnly ? [null] : [360, 430, 1280]) {
      let api = backend(9), ui, firstScenario = true;
      try {
        ui = await driver(browser, api, width);
        await until(ui, `identity && !$('lobbyView').classList.contains('hidden')`);
        console.log('Ready:', domOnly ? 'DOM' : width);
        for (const [invite, fill] of [[false, true], [true, true], [false, false], [true, false]]) {
          if (!firstScenario) {
            assert.deepEqual(ui.errors, []);
            await ui.close(); await api.close();
            api = backend(9); ui = await driver(browser, api, width);
            await until(ui, `identity && !$('lobbyView').classList.contains('hidden')`);
          }
          firstScenario = false;
          const count = fill ? 6 : 4;
          if (invite) await ui.click('#inviteCreateEntry');
          const select = invite ? 'inviteGame' : 'gameType', picker = `#${select}RulePicker`;
          await ui.choose(invite ? '#inviteCategory' : '#gameCategory', 'dice');
          await ui.choose(`#${select}`, 'liars_dice');
          assert.equal(await ui.run(`document.querySelectorAll('${picker} select[aria-label="组队玩法"]').length`), 0);
          await ui.choose(`${picker} select[aria-label="吹牛骰子玩法"]`, 'party');
          const teams = `${picker} select[aria-label="组队玩法"]`;
          assert.equal(await ui.run(`document.querySelector('${teams}').value`), 'none');
          const before = api.posts.length;
          for (const option of ['random', 'manual', 'none', 'manual']) {
            await ui.choose(teams, option);
            const options = await ui.run(`JSON.stringify(selectedGameOptions($('${select}')))`);
            const rules = JSON.parse(options);
            assert.equal(rules.team_mode, option === 'none' ? undefined : 'teams');
            if (option !== 'none') assert.equal(rules.random_teams, option === 'random');
          }
          assert.equal(api.posts.length, before, 'switching teams must not create a room');
          assert.equal(await ui.run(`document.querySelectorAll('${picker} button, ${picker} input[aria-label="随机组队"]').length`), 0);
          assert.equal(await ui.run(`document.querySelector('${picker} details').open`), false);
          await ui.shot(invite ? 'invite-collapsed' : 'rules-collapsed', picker);
          await ui.click(`${picker} summary`);
          await ui.choose(`${picker} select[aria-label="有骰计骰"]`, 'true');
          await ui.choose(`${picker} select[aria-label="5人普通起叫"]`, '7');
          if (!domOnly) {
            const metrics = JSON.parse(await ui.run(`JSON.stringify([...document.querySelectorAll('${picker} .liars-rule-group .pixel-field')].map(n=>{const r=n.getBoundingClientRect(),s=n.querySelector('select').getBoundingClientRect();return {left:r.left,width:r.width,selectLeft:s.left,selectWidth:s.width,height:s.height,font:getComputedStyle(n).fontFamily};}))`));
            assert.equal(metrics.length, 2); assert.equal(metrics[0].left, metrics[1].left); assert.equal(metrics[0].width, metrics[1].width);
            assert.equal(metrics[0].selectWidth, metrics[1].selectWidth); assert.ok(metrics.every(m => m.height >= 44));
            evidence.push({width, invite, metrics});
          }
          await ui.shot(invite ? 'invite-expanded' : 'rules-expanded', `${picker} details`);
          await ui.click(`${picker} summary`);
          await ui.choose(invite ? '#inviteCount' : '#targetPlayerCount', String(count));
          await ui.click(invite ? '#inviteAiTrigger' : '#aiMultiTrigger');
          await ui.click(`${invite ? '#inviteAiMenu' : '#aiMultiMenu'} [data-player-id="ai-1"]`);
          if (!invite) {
            await ui.click('#aiMultiMenu [data-player-id="ai-2"]');
            if (!fill) await ui.click('#aiMultiMenu [data-player-id="ai-3"]');
          }
          await ui.click(invite ? '#inviteAiTrigger' : '#aiMultiTrigger');
          await ui.click(invite ? '#inviteSubmit' : '#createButton');
          try {
            await until(ui, `room?.status === 'waiting' && !!document.querySelector('.liars-team-seats')`);
          } catch (error) {
            console.error(await ui.run(`JSON.stringify({room:room?.status, notices:[...document.querySelectorAll('.notice')].map(n=>n.textContent), inviteError:$('inviteError').textContent, createDisabled:$('createButton').disabled, inviteDisabled:$('inviteSubmit').disabled})`));
            throw error;
          }
          const rid = await ui.run('room.room_id');
          console.log(invite ? 'Invite created' : 'Regular created', rid);
          const creates = api.posts.filter(p => ['/api/rooms', '/api/invites'].includes(p.url)).length;
          assert.equal(await ui.run(`document.querySelector('.liars-team-seats').classList.contains('hidden')`), false);
          assert.equal(await ui.run(`room.participants.some(p=>p.participant_kind==='system_npc')`), false);
          assert.equal(await ui.run(`document.querySelector('[data-action="开始游戏"]').disabled`), true);
          assert.equal(await ui.run(`document.querySelectorAll('[data-action="开始游戏"]').length`), 1);
          assert.equal(await ui.run(`document.querySelectorAll('[data-action="NPC 补满并开始"]').length`), 0);
          assert.ok(await ui.run(`document.querySelector('[data-action="解散房间"]') !== null`));
          if (!invite && fill) {
            const postCount = api.posts.length;
            const blocked = JSON.parse(await ui.run(`(() => {
              const savedRoom = room, savedIdentity = identity, results = [];
              try {
                for (const kind of ['no-provider', 'unsupported', 'npc-limit', 'no-invitee', 'not-owner']) {
                  room = JSON.parse(JSON.stringify(savedRoom));
                  room.team_selection.can_start = true;
                  room.team_selection.start_after = '2000-01-01T00:00:00Z';
                  identity = {...savedIdentity, npc_provider:{available:true}, games:savedIdentity.games.map(g => g.game_type === 'liars_dice' ? {...g, supports_npcs:true, uses_local_npc_strategy:false} : g)};
                  if (kind === 'no-provider') identity.npc_provider.available = false;
                  if (kind === 'unsupported') identity.games.find(g => g.game_type === 'liars_dice').supports_npcs = false;
                  if (kind === 'npc-limit') room.participants = room.participants.filter(p=>p.player_id === 'human-1');
                  if (kind === 'no-invitee') {room.room_kind='invite'; room.household_count=room.participants.length;}
                  if (kind === 'not-owner') room.initiator_player_id='ai-1';
                  delete $('inviteRoomPanel').dataset.diceSignature;
                  renderInviteRoomPanel();
                  const button = document.querySelector('[data-action="开始游戏"]');
                  results.push({kind, disabled:button.disabled, hint:$('inviteStartHint')?.textContent,
                    starts:document.querySelectorAll('[data-action="开始游戏"]').length,
                    fills:document.querySelectorAll('[data-action="NPC 补满并开始"]').length});
                  button.click();
                }
                // Other invite games keep their explicit normal/fill choices.
                room = {...savedRoom, game_type:'uno', room_kind:'invite', household_count:1};
                delete room.team_selection;
                identity = {...savedIdentity, games:[{game_type:'uno', supports_npcs:true, uses_local_npc_strategy:true}]};
                delete $('inviteRoomPanel').dataset.diceSignature;
                renderInviteRoomPanel();
                results.push({kind:'other-game', fills:document.querySelectorAll('[data-action="NPC 补满并开始"]').length});
              } finally {
                room = savedRoom; identity = savedIdentity;
                delete $('inviteRoomPanel').dataset.diceSignature;
                renderInviteRoomPanel();
              }
              return JSON.stringify(results);
            })()`));
            const reasons = ['NPC补位当前不可用', '不支持NPC', '最多4名NPC', '外部受邀', '房主'];
            blocked.slice(0, 5).forEach((r, i) => {
              assert.equal(r.disabled, true, r.kind); assert.equal(r.starts, 1); assert.equal(r.fills, 0);
              assert.ok(r.hint.includes(reasons[i]), r.hint);
            });
            assert.equal(blocked.at(-1).fills, 1);
            assert.equal(api.posts.length, postCount, 'blocked buttons must not send start requests');
          }
          const startsBefore = api.posts.filter(p => p.url.endsWith('/start')).length;
          await ui.run(`document.querySelector('[data-action="开始游戏"]').click()`);
          assert.equal(api.posts.filter(p => p.url.endsWith('/start')).length, startsBefore);
          if (invite) {
            assert.ok(await ui.run(`$('inviteStartHint').textContent.includes('外部受邀')`));
            const code = await ui.run('room.invite_code');
            assert.equal((await api.request('/api/invites/join', 'POST', {invite_code: code}, 'human-2')).status, 200);
            if (!fill) assert.equal((await api.request('/api/invites/join', 'POST', {invite_code: code}, 'human-3')).status, 200);
          }
          await ui.click('[data-action="返回大厅"]');
          await until(ui, `!room && !!document.querySelector('#roomList .room-open[aria-label$="${rid}"]')`);
          await ui.reload();
          await until(ui, `!!document.querySelector('#roomList .room-open[aria-label$="${rid}"]')`);
          assert.ok(await ui.run(`document.querySelector('#roomList').textContent.includes('等待组队')`));
          assert.ok(await ui.run(`document.querySelector('#roomList .room-open').getAttribute('aria-label').endsWith('${rid}')`));
          assert.ok(await ui.run(`document.querySelector('#roomList .room-open').textContent.includes('${fill ? '3/6' : '4/4'}')`));
          assert.ok(await ui.run(`identity.rooms.filter(r=>r.status==='playing').length >= 9`));
          assert.equal(await ui.run(`identity.pending_invitations.some(r=>r.room_id==='${rid}')`), false);
          assert.equal(api.posts.some(p => p.url.endsWith('/leave')), false, 'returning to lobby must not leave');
          await ui.shot(invite ? 'invite-lobby' : 'regular-lobby', '#roomList');
          await ui.click(`#roomList .room-open[aria-label$="${rid}"]`);
          await until(ui, `room?.room_id === '${rid}' && !!document.querySelector('.liars-team-seat')`);
          await ui.click('[aria-label="加入红队第1位"]');
          await until(ui, `room.team_selection.seats.red[0] === 'human-1'`);
          await ui.click(`[aria-label="加入蓝队第${count / 2}位"]`);
          await until(ui, `room.team_selection.seats.blue[${count / 2 - 1}] === 'human-1'`);
          const revision = await ui.run('room.revision');
          assert.equal((await api.request('/mcp/play', 'POST', {action: 'move', room_id: rid, revision, move: {action: 'choose_team_seat', team: 'red', seat: 2}}, 'ai-1')).status, 200);
          await ui.run('refreshRoom({quiet:true})');
          assert.equal(await ui.run(`room.team_selection.seats.red[1]`), 'ai-1');
          await ui.shot(invite ? 'invite-reentered' : 'regular-reentered', '#inviteRoomPanel');
          await until(ui, `room.team_selection.can_start`, 24000);
          assert.equal(await ui.run(`document.querySelector('.liars-team-seats').classList.contains('hidden')`), false);
          assert.equal(await ui.run(`document.querySelector('[data-action="开始游戏"]').disabled`), false);
          await ui.click('[data-action="开始游戏"]');
          await until(ui, `room.status === 'playing'`);
          assert.equal(api.posts.filter(p => p.url.endsWith('/start')).at(-1).body.fill_with_npcs, fill);
          assert.equal(await ui.run('room.participants.length'), count);
          assert.equal(await ui.run(`room.board_state.teams.blue[${count / 2 - 1}]`), 'human-1');
          assert.equal(await ui.run(`room.participants.filter(p=>p.participant_kind==='system_npc').length`), fill ? 3 : 0);
          assert.equal(api.posts.filter(p => ['/api/rooms', '/api/invites'].includes(p.url)).length, creates);
          console.log('Started:', {invite, fill, count});
          evidence.push({width, invite, fill, room_id: rid, status: 'playing', reenteredSameRoom: true, waitingFirst: true});
          await ui.click('#backButton');
          await until(ui, `!room && !$('lobbyView').classList.contains('hidden')`);
        }
        assert.deepEqual(ui.errors, []);
        console.log(`PASS ${domOnly ? 'DOM + real ASGI (no visual validation)' : width + 'px Chromium'}: three options, regular/invite create → lobby → reload → reenter → self-seat/swap → MCP seat → 20s host/NPC start`);
      } finally {if (ui) await ui.close(); await api.close();}
    }
    fs.writeFileSync(path.join(output, domOnly ? 'dom-evidence.json' : 'browser-evidence.json'), JSON.stringify(evidence, null, 2));
  } finally {
    if (browser) await browser.close();
    if (!process.env.DICE_SCREENSHOTS) fs.rmSync(output, {recursive: true, force: true});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
