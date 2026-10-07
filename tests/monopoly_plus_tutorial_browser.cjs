// Invoked by test_monopoly_plus_frontend.py when Playwright is available.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '..');
const key = 'cedarduet.monopoly_plus.tutorial.v1';
const screenshots = process.env.MP_TUTORIAL_SCREENSHOTS;
const shell = `<!doctype html><html lang="zh-CN"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/styles.css"><body><main class="duel-main"><section id="gameView" class="game-view"><header class="game-header pixel-card"><div class="game-toolbar"><div class="game-toolbar-actions"><button id="refreshButton" class="pixel-btn secondary compact" type="button">刷新</button><button id="rulesButton" class="pixel-btn secondary compact" type="button">规则</button><button id="resignButton" class="pixel-btn danger compact" type="button">认输</button></div></div></header><section id="battleStage" class="battle-stage pixel-card" data-game-type="monopoly_plus"><div class="battle-main-column"><div class="table-layout layout-duel count-4"><div class="board-zone"><div class="board-frame"><div id="board" class="board"></div></div><div id="gameControls" class="game-controls"></div></div></div></div></section></section></main><div id="waitModeModal" class="wait-mode-modal-backdrop hidden"></div></body></html>`;
// Where each lesson's highlight belongs on the opening table seen by the current player.
const OPENING_TARGETS = ['.mp-center', '.mp-turn-banner', '.mp-ring', '.mp-dice', '.mp-hand', '.mp-event-bar',
  '.mp-loan', '.mp-bets', '.mp-controls .mp-actions.tools'];
const ESSENTIALS = [
  ['经典大富翁的加强版', '只介绍，不替你出手'],
  ['轮到谁', '轮到你了'],
  ['小圆章＝这块地是谁的', '带尖角的实心棋子＝人现在站在哪'],
  ['两颗白骰', '第一次经过起点后', '速度骰', '1/2/3', '巴士', '大富翁先生'],
  ['只能', '抽到', '强行交易', '收购', '「不行」'],
  ['事件卡', '讲一段话', '投赞成或反对'],
  ['额度', '利息', '第 3 次过起点要还清'],
  ['没轮到你', '大（≥8）', '小（≤6）'],
  ['建房', '抵押', '起个名字', '牌子'],
];
(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 360, height: 800}, hasTouch: true});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://mp.test/**', route => {
      const url = new URL(route.request().url());
      return url.pathname === '/' ? route.fulfill({contentType: 'text/html', body: shell})
        : route.fulfill({path: path.join(root, 'app', url.pathname)});
    });
    const frames = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    async function mount() {
      await page.goto('http://mp.test/');
      await page.evaluate(() => { window.DuelGameUI = {register: (_, r) => { window.renderer = r; }}; });
      await page.addScriptTag({path: path.join(root, 'app/static/games/monopoly_plus.js')});
      await page.evaluate(fixtures => {
        window.fixtures = fixtures;
        window.submitted = [];
        window.show = (kind = 'opening', viewer = 'h', roomId = 'room-one') => {
          document.querySelector('#gameView').classList.remove('hidden');
          const fx = fixtures[kind], priv = fx.private[viewer];
          const context = window.context = {
            board: document.querySelector('#board'), controls: document.querySelector('#gameControls'),
            state: structuredClone(fx.state), privateState: priv, legalActions: priv.legal_actions,
            uiState: {}, canMove: fx.current === viewer, isTerminal: false,
            room: {room_id: roomId, revision: 1, current_player_id: fx.current, status: 'playing'},
            viewer: {player_id: viewer, role: viewer === 'h' ? 'human' : 'ai'},
            participants: [['h', '欣欣', 0], ['a', '小机', 1], ['n3', '下棋助手 3', 2], ['n4', '下棋助手 4', 3]]
              .map(([id, n, i]) => ({player_id: id, display_name: n, seat_index: i})),
            helpers: {canMove: () => context.canMove, renderParticipantAvatar() { return true; }, announce() {},
              rerender: () => {
                context.board.replaceChildren(); context.controls.replaceChildren();
                renderer.renderBoard(context); renderer.renderControls(context);
              },
              submitMove: move => { window.submitted.push(move); return Promise.resolve(true); },
              submitSideMove: move => { window.submitted.push(move); return Promise.resolve(true); }},
          };
          context.helpers.rerender();
        };
        show();
      }, fixtures);
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.mp-game')).display === 'grid');
      await frames();
    }
    const modal = page.locator('.mp-tutorial');
    const button = name => modal.getByRole('button', {name, exact: true});
    const entry = page.locator('#mpTutorialButton');
    const stored = () => page.evaluate(key => localStorage.getItem(key), key);
    async function intro() { await modal.waitFor(); assert.equal(await modal.locator('h2').innerText(), '第一次来大富翁·改？'); }
    async function leave() {
      await page.evaluate(() => document.querySelector('#gameView').classList.add('hidden'));
      await frames(); assert.equal(await modal.count(), 0); assert.equal(await entry.count(), 0, 'entry leaves with the game');
    }
    async function enter(kind = 'opening', viewer = 'h') {
      await page.evaluate(([kind, viewer]) => show(kind, viewer), [kind, viewer]); await frames();
    }
    async function fresh(kind, viewer) { await page.evaluate(key => localStorage.removeItem(key), key); await leave(); await enter(kind, viewer); await intro(); }
    async function snap(name) {
      if (!screenshots) return;
      fs.mkdirSync(screenshots, {recursive: true});
      await page.screenshot({path: path.join(screenshots, name + '.png')});
    }
    async function step(n) { await page.waitForFunction(n => document.querySelector('.mp-tutorial')?.dataset.step === String(n), n); await frames(); }
    async function highlighted(selector) {
      return page.evaluate(selector => {
        const s = document.querySelector('.mp-tutorial-spotlight');
        const node = document.querySelector(selector);
        if (s.hidden || !node) return false;
        const h = s.getBoundingClientRect(), t = node.getBoundingClientRect();
        return Math.abs(h.x - Math.max(2, t.x - 4)) < 1 && Math.abs(h.y - Math.max(2, t.y - 4)) < 1;
      }, selector);
    }

    await mount(); await intro();
    assert.equal(await entry.innerText(), '教程');
    assert.ok(await page.evaluate(() => document.querySelector('#mpTutorialButton').previousElementSibling.id === 'rulesButton'), 'entry sits right after 规则');
    await button('关闭本次教程').click(); assert.equal(await stored(), null);
    await page.evaluate(() => { context.uiState = {}; context.room.revision++; context.helpers.rerender(); });
    await frames(); assert.equal(await modal.count(), 0, 'revision must not reprompt');
    assert.equal(await entry.count(), 1, 'one entry, never duplicated by rerenders');
    await leave(); await enter(); await intro();
    await button('不再提示').click(); assert.equal(await stored(), 'dismissed');
    await mount(); assert.equal(await modal.count(), 0, 'persistent opt-out survives reload');
    await entry.click(); await step(1);
    assert.equal(await modal.locator('h2').innerText(), '欢迎来到大富翁·改');
    assert.ok(await button('不再提示').isHidden());
    await page.keyboard.press('Escape'); assert.equal(await modal.count(), 0); assert.equal(await stored(), 'dismissed');
    console.log('PASS first visit, temporary close/re-entry, revision, persistent opt-out/reload, reopen entry beside 规则');

    await fresh(); await button('查看教程').click(); await step(1);
    assert.equal(await button('上一步').isDisabled(), true);
    await button('下一步').click(); await step(2);
    await button('上一步').click(); await step(1);
    await button('关闭本次教程').click(); assert.equal(await stored(), null);
    await leave(); await enter(); await intro();
    await button('查看教程').click();
    for (let i = 1; i < 9; i++) { await step(i); await button('下一步').click(); }
    await step(9); assert.equal(await stored(), null, 'only the final completion click persists');
    await button('完成').click(); assert.equal(await stored(), 'completed');
    await mount(); assert.equal(await modal.count(), 0);
    await entry.click(); await step(1);
    for (let i = 1; i < 9; i++) await button('下一步').click();
    await button('完成').click(); assert.equal(await stored(), 'completed');
    assert.deepEqual(await page.evaluate(() => submitted), []);
    console.log('PASS previous/next, interrupted tutorial, final completion/reload, replay from entry, no game actions');

    for (const width of [360, 390, 430, 1280]) {
      await page.setViewportSize({width, height: 800});
      await fresh(); await snap(`${width}-intro`);
      const stateBefore = await page.evaluate(() => JSON.stringify(context.state));
      await button('查看教程').tap();
      let previousScroll = 0, moved = false;
      for (let i = 1; i <= 9; i++) {
        await step(i);
        const lesson = await modal.locator('.mp-tutorial-copy').innerText();
        for (const phrase of ESSENTIALS[i - 1]) assert.ok(lesson.includes(phrase), `step ${i}: ${phrase} in ${lesson}`);
        const metrics = await page.evaluate(selector => {
          const dialog = document.querySelector('.mp-tutorial');
          const spotlight = dialog.querySelector('.mp-tutorial-spotlight');
          const highlight = spotlight.getBoundingClientRect();
          const panel = dialog.querySelector('.mp-tutorial-panel').getBoundingClientRect();
          const target = document.querySelector(selector).getBoundingClientRect();
          const visible = {top: Math.max(0, target.top), bottom: Math.min(innerHeight, target.bottom)};
          const overlap = Math.min(panel.right, target.right) > Math.max(panel.left, target.left)
            && Math.min(panel.bottom, visible.bottom) > Math.max(panel.top, visible.top);
          const next = [...dialog.querySelectorAll('button')].find(b => /^(下一步|完成)$/.test(b.textContent));
          const r = next.getBoundingClientRect();
          return {overlap, shown: !spotlight.hidden,
            aligned: Math.abs(highlight.x - Math.max(2, target.x - 4)) < 1 && Math.abs(highlight.y - Math.max(2, target.y - 4)) < 1,
            panelInView: panel.left >= 0 && panel.right <= innerWidth && panel.top >= 0 && panel.bottom <= innerHeight,
            clickable: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === next,
            width: document.documentElement.scrollWidth, scroll: scrollY};
        }, OPENING_TARGETS[i - 1]);
        assert.ok(metrics.shown && !metrics.overlap && metrics.aligned && metrics.panelInView && metrics.clickable,
          `${width}px step ${i}: ${JSON.stringify(metrics)}`);
        assert.equal(metrics.width, width);
        moved ||= metrics.scroll !== previousScroll; previousScroll = metrics.scroll;
        await snap(`${width}-step-${i}`);
        if (i === 3) {
          const tile = page.locator('.mp-tile[data-tile-id="6"]');
          const r = await tile.boundingBox();
          await page.touchscreen.tap(r.x + r.width / 2, r.y + r.height / 2);
          assert.equal(await page.locator('dialog.mp-dialog').count(), 0, 'modal blocks real taps');
          await page.evaluate(() => window.scrollBy(0, 25)); await frames();
          assert.ok(await highlighted('.mp-ring'), 'highlight follows scrolling');
          await page.evaluate(() => { context.uiState = {}; context.room.revision++; context.helpers.rerender(); });
          await frames(); await step(3); assert.ok(await highlighted('.mp-ring'), 'highlight follows a fresh render');
        }
        await button(i === 9 ? '完成' : '下一步').tap();
      }
      assert.ok(moved, 'steps scroll to their real targets');
      assert.equal(await page.evaluate(() => JSON.stringify(context.state)), stateBefore);
      assert.deepEqual(await page.evaluate(() => submitted), []);
      await page.locator('.mp-tile[data-tile-id="6"]').tap();
      assert.equal(await page.locator('dialog.mp-dialog').count(), 1, 'game usable after closing');
      await page.locator('dialog.mp-dialog').getByRole('button', {name: '关闭对话框'}).click();
      console.log(`PASS ${width}px: all nine steps, no overlap/overflow, scrolling, refreshed targets, isolated taps, restored board`);
    }

    // A chat card on the table: lesson 6 points at the real card, not the message bar.
    await page.setViewportSize({width: 390, height: 800});
    await fresh('vote', fixtures.vote.current); await button('查看教程').click();
    for (let i = 1; i < 6; i++) await button('下一步').click();
    await step(6); assert.ok(await highlighted('.mp-choice'));
    assert.ok((await modal.locator('.mp-tutorial-copy').innerText()).includes('超时按不通过'));
    await button('关闭本次教程').click();
    // A spectator sees the betting chips highlighted and cannot tap through the modal.
    await fresh('opening', 'a'); await button('查看教程').click();
    for (let i = 1; i < 8; i++) await button('下一步').click();
    await step(8); assert.ok(await highlighted('.mp-bets'));
    const chip = await page.locator('.mp-chip').first().boundingBox();
    await page.touchscreen.tap(chip.x + chip.width / 2, chip.y + chip.height / 2);
    assert.deepEqual(await page.evaluate(() => submitted), [], 'modal blocks side bets');
    await page.keyboard.press('Escape');
    console.log('PASS chat card and spectator bet lessons highlight the live areas');

    // Edition setup has no hand, loan, bets or tools yet: fallback copy, no highlight, still navigable.
    await fresh('setup', 'h'); await button('查看教程').click();
    for (let i = 1; i <= 9; i++) {
      await step(i);
      const copy = await modal.locator('.mp-tutorial-copy').innerText();
      if ([5, 7, 8, 9].includes(i)) {
        assert.ok(await page.locator('.mp-tutorial-spotlight').isHidden(), `setup step ${i} has no highlight`);
        assert.ok(copy.includes(i === 8 ? '押注区' : '开局后'), `setup step ${i} explains where it will appear`);
      }
      const panel = await page.locator('.mp-tutorial-panel').boundingBox();
      assert.ok(panel.y >= 0 && panel.y + panel.height <= 800);
      if (i < 9) await button('下一步').click();
    }
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      assert.ok(await page.evaluate(() => !!document.activeElement.closest('.mp-tutorial')), 'focus stays inside');
    }
    await page.keyboard.press('Escape'); assert.equal(await modal.count(), 0); assert.equal(await stored(), null);
    console.log('PASS setup phase fallback copy, keyboard focus isolation, Escape');

    await leave(); await enter(); await intro();
    await page.evaluate(() => { context.board.replaceChildren(); context.board.className = 'board chess'; });
    await frames(); assert.equal(await modal.count(), 0, 'other game removes tutorial');
    assert.equal(await entry.count(), 0, 'other game removes the entry');
    // Storage denial must not stop rendering, navigation or cleanup.
    await page.evaluate(() => { Storage.prototype.getItem = () => { throw new Error('denied'); }; Storage.prototype.setItem = () => { throw new Error('denied'); }; show(); });
    await intro(); await button('不再提示').click(); assert.equal(await modal.count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS other-game cleanup and unavailable storage');

    // The site's own room tip comes first; the tour waits for it, then pops once.
    await mount(); await page.evaluate(() => { Storage.prototype.getItem = () => null; Storage.prototype.setItem = () => {}; });
    await leave();
    const waitTip = hidden => page.evaluate(h => document.querySelector('#waitModeModal').classList.toggle('hidden', h), hidden);
    await waitTip(false); await enter(); await frames();
    assert.equal(await modal.count(), 0, 'tour waits behind the room tip');
    await waitTip(true); await frames(); await intro();
    await waitTip(false); await frames(); assert.equal(await modal.count(), 0, 'a late room tip pauses the tour');
    await waitTip(true); await frames(); await intro();
    assert.deepEqual(errors, []);
    console.log('PASS site room tip takes precedence; tour follows once it closes');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
