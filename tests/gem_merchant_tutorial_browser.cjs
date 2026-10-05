// Invoked by test_gem_merchant_frontend.py when Playwright is available.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '..');
const key = 'cedarduet.gem_merchant.tutorial.v1';
const screenshots = process.env.GM_TUTORIAL_SCREENSHOTS;
const shell = `<!doctype html><html lang="zh-CN"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/styles.css"><body><main class="duel-main"><section id="gameView"><section id="battleStage" class="battle-stage pixel-card" data-game-type="gem_merchant"><div class="battle-main-column"><div class="table-layout layout-duel count-2"><div class="board-zone"><div class="board-frame"><div id="board" class="board"></div></div><div id="gameControls" class="game-controls"></div></div></div></div></section></section></main></body></html>`;
(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 360, height: 800}, hasTouch: true});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://gm.test/**', route => {
      const url = new URL(route.request().url());
      return url.pathname === '/' ? route.fulfill({contentType: 'text/html', body: shell})
        : route.fulfill({path: path.join(root, 'app', url.pathname)});
    });
    const frames = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    async function mount() {
      await page.goto('http://gm.test/');
      await page.evaluate(() => { window.DuelGameUI = {register: (_, r) => { window.renderer = r; }}; });
      await page.addScriptTag({path: path.join(root, 'app/static/games/gem_merchant.js')});
      await page.evaluate(fixtures => {
        window.fixtures = fixtures;
        window.submitted = [];
        window.show = (kind = 'opening', viewer = 'human', roomId = 'room-one') => {
          document.querySelector('#gameView').classList.remove('hidden');
          const fx = fixtures[kind], priv = fx.private[viewer];
          const context = window.context = {
            board: document.querySelector('#board'), controls: document.querySelector('#gameControls'),
            state: structuredClone(fx.state), privateState: priv, legalActions: priv.legal_actions,
            uiState: {}, canMove: !fx.terminal && fx.current === viewer, isTerminal: fx.terminal,
            room: {room_id: roomId, revision: 1, current_player_id: fx.current, status: fx.terminal ? 'finished' : 'playing'},
            viewer: {player_id: viewer}, participants: [{player_id: 'human', display_name: '南山'}, {player_id: 'machine', display_name: '小机'}],
            helpers: {canMove: () => context.canMove, rerender: () => {
              context.board.replaceChildren(); context.controls.replaceChildren();
              renderer.renderBoard(context); renderer.renderControls(context);
            }, submitMove: move => { window.submitted.push(move); return Promise.resolve(true); }},
          };
          context.helpers.rerender();
        };
        show();
      }, fixtures);
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.gm-game')).display === 'grid');
      await frames();
    }
    const modal = page.locator('.gm-tutorial');
    const button = name => modal.getByRole('button', {name, exact: true});
    const stored = () => page.evaluate(key => localStorage.getItem(key), key);
    async function intro() { await modal.waitFor(); assert.equal(await modal.locator('h2').innerText(), '第一次来宝石商人？'); }
    async function leave() {
      await page.evaluate(() => document.querySelector('#gameView').classList.add('hidden'));
      await frames(); assert.equal(await modal.count(), 0);
    }
    async function enter(kind = 'opening', viewer = 'human') {
      await page.evaluate(([kind, viewer]) => show(kind, viewer), [kind, viewer]); await frames();
    }
    async function fresh() { await page.evaluate(key => localStorage.removeItem(key), key); await leave(); await enter(); await intro(); }
    async function snap(name) {
      if (!screenshots) return;
      fs.mkdirSync(screenshots, {recursive: true});
      await page.screenshot({path: path.join(screenshots, name + '.png')});
    }
    async function step(n) { await page.waitForFunction(n => document.querySelector('.gm-tutorial')?.dataset.step === String(n), n); await frames(); }

    await mount(); await intro();
    await button('关闭本次教程').click(); assert.equal(await stored(), null);
    await page.evaluate(() => { context.uiState = {}; context.room.revision++; context.helpers.rerender(); });
    await frames(); assert.equal(await modal.count(), 0, 'revision must not reprompt');
    await leave(); await enter(); await intro();
    await button('不再提示').click(); assert.equal(await stored(), 'dismissed');
    await mount(); assert.equal(await modal.count(), 0, 'persistent opt-out survives reload');
    console.log('PASS first visit, temporary close/re-entry, revision, persistent opt-out/reload');

    await fresh(); await button('查看教程').click(); await step(1);
    assert.equal(await button('上一步').isDisabled(), true);
    await button('下一步').click(); await step(2);
    await button('上一步').click(); await step(1);
    await button('关闭本次教程').click(); assert.equal(await stored(), null);
    await leave(); await enter(); await intro();
    await button('查看教程').click();
    for (let i = 1; i < 7; i++) { await step(i); await button('下一步').click(); }
    await step(7); assert.ok((await modal.innerText()).includes('拿宝石 → 买卡 → 获得永久加成'));
    assert.equal(await page.locator('.gm-detail').count(), 0, 'no fabricated card');
    assert.equal(await stored(), null, 'only the final completion click persists');
    await button('完成').click(); assert.equal(await stored(), 'completed');
    await mount(); assert.equal(await modal.count(), 0);
    assert.deepEqual(await page.evaluate(() => submitted), []);
    console.log('PASS previous/next, interrupted tutorial, final completion/reload, no game actions');

    for (const width of [360, 390, 430, 1280]) {
      await page.setViewportSize({width, height: 800});
      await fresh(); await snap(`${width}-intro`);
      const stateBefore = await page.evaluate(() => JSON.stringify(context.state));
      await button('查看教程').tap();
      let previousScroll = 0, moved = false;
      for (let i = 1; i <= 7; i++) {
        await step(i);
        const lesson = await modal.locator('.gm-tutorial-copy').innerText();
        const essentials = [
          ['20 分', '10 顶', '同一种颜色的卡累计 10 分'],
          ['只选一个', '①拿宝石', '②保留一张卡', '③买一张卡'],
          ['1–3', '非金色', '2–3', '横、竖或斜', '直线', '不能隔空格或金色', '中间隔空不行'],
          ['圆点', '价格', '先拿宝石凑够', '买下', '赚分', '永久加成', '抵扣'],
          ['金色宝石', '拿金保留', '拿 1 枚金', '以后买', '任意颜色', '最多保留 3 张'],
          ['先不用全背', '高亮和按钮', '超过 10 枚', '弃到只剩 10 枚'],
          ['拿宝石 → 买卡 → 获得永久加成 → 更容易买更贵的卡 → 达成胜利条件', '3 顶、6 顶'],
        ];
        for (const phrase of essentials[i - 1]) assert.ok(lesson.includes(phrase), `step ${i}: ${phrase}`);
        const metrics = await page.evaluate(() => {
          const dialog = document.querySelector('.gm-tutorial');
          const highlight = dialog.querySelector('.gm-tutorial-spotlight').getBoundingClientRect();
          const panel = dialog.querySelector('.gm-tutorial-panel').getBoundingClientRect();
          const targetSelectors = ['.gm-player.is-opponent', '.gm-controls', '.gm-gem-board', '.gm-pyramid', '.gm-pyramid', '.gm-controls', '.gm-player.is-viewer'];
          const target = document.querySelector(targetSelectors[Number(dialog.dataset.step) - 1]).getBoundingClientRect();
          const overlap = Math.min(panel.right, target.right) > Math.max(panel.left, target.left)
            && Math.min(panel.bottom, target.bottom) > Math.max(panel.top, target.top);
          const next = [...dialog.querySelectorAll('button')].find(b => /^(下一步|完成)$/.test(b.textContent));
          const r = next.getBoundingClientRect();
          return {overlap, aligned: Math.abs(highlight.x - target.x + 4) < 1 && Math.abs(highlight.y - target.y + 4) < 1,
            panelInView: panel.left >= 0 && panel.right <= innerWidth && panel.top >= 0 && panel.bottom <= innerHeight,
            clickable: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === next,
            width: document.documentElement.scrollWidth, scroll: scrollY};
        });
        assert.ok(!metrics.overlap && metrics.aligned && metrics.panelInView && metrics.clickable, `${width}px step ${i}: ${JSON.stringify(metrics)}`);
        assert.equal(metrics.width, width);
        moved ||= metrics.scroll !== previousScroll; previousScroll = metrics.scroll;
        await snap(`${width}-step-${i}`);
        if (i === 3) {
          const cell = page.locator('.gm-cell:not(:disabled)').first();
          const r = await cell.boundingBox();
          await page.touchscreen.tap(r.x + r.width / 2, r.y + r.height / 2);
          assert.equal(await page.locator('.gm-cell.is-selected').count(), 0, 'modal blocks real moves');
          await page.evaluate(() => window.scrollBy(0, 25)); await frames();
          assert.ok(await page.evaluate(() => {
            const h = document.querySelector('.gm-tutorial-spotlight').getBoundingClientRect();
            const t = document.querySelector('.gm-gem-board').getBoundingClientRect();
            return Math.abs(h.y - t.y + 4) < 1;
          }), 'highlight follows scrolling');
          await page.evaluate(() => { context.uiState = {}; context.room.revision++; context.helpers.rerender(); });
          await frames(); await step(3);
        }
        await button(i === 7 ? '完成' : '下一步').tap();
      }
      assert.ok(moved, 'steps scroll to their real targets');
      assert.equal(await page.evaluate(() => JSON.stringify(context.state)), stateBefore);
      assert.deepEqual(await page.evaluate(() => submitted), []);
      await page.locator('.gm-cell:not(:disabled)').first().tap();
      assert.equal(await page.locator('.gm-cell.is-selected').count(), 1, 'game usable after closing');
      console.log(`PASS ${width}px: all seven steps, no overlap/overflow, scrolling, refreshed targets, isolated touch, restored controls`);
    }

    // An existing detail remains intact; the buying lesson still highlights the cards.
    await page.setViewportSize({width: 360, height: 740});
    await page.locator('.gm-pyramid .gm-card[data-card-id]').first().click();
    const savedDraft = await page.evaluate(() => JSON.stringify(context.uiState.gm));
    await page.evaluate(key => {
      localStorage.removeItem(key);
      context.room.room_id = 'room-with-detail';
      context.helpers.rerender();
      document.querySelector('.gm-detail-close').focus();
    }, key);
    await intro(); await button('查看教程').click();
    for (let i = 1; i < 4; i++) await button('下一步').click();
    await step(4);
    assert.ok((await modal.innerText()).includes('卡上彩色圆点里的数字就是价格'));
    await page.setViewportSize({width: 430, height: 740}); await frames();
    assert.ok(await page.evaluate(() => {
      const h = document.querySelector('.gm-tutorial-spotlight').getBoundingClientRect();
      const t = document.querySelector('.gm-pyramid').getBoundingClientRect();
      const p = document.querySelector('.gm-tutorial-panel').getBoundingClientRect();
      return Math.abs(h.width - t.width - 8) < 1 && Math.abs(h.y - t.y + 4) < 1
        && (p.bottom <= t.top || p.top >= t.bottom);
    }));
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      assert.ok(await page.evaluate(() => !!document.activeElement.closest('.gm-tutorial')));
    }
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => JSON.stringify(context.uiState.gm)), savedDraft);
    assert.ok(await page.locator('.gm-detail-close').evaluate(node => node === document.activeElement));
    console.log('PASS existing detail, resize alignment, keyboard focus isolation and restoration');

    // Missing/replaced targets, waiting and finished rooms remain navigable.
    for (const [kind, viewer] of [['opening', 'machine'], ['terminal', 'human']]) {
      await page.setViewportSize({width: 360, height: 800});
      await leave(); await page.evaluate(key => localStorage.removeItem(key), key); await enter(kind, viewer);
      await intro(); await button('查看教程').click();
      await page.evaluate(() => document.querySelector('.gm-controls').remove());
      await button('下一步').click(); await step(2);
      await page.evaluate(() => { document.querySelector('.gm-board-zone').remove(); });
      await button('下一步').click(); await step(3);
      assert.ok(await page.locator('.gm-tutorial-spotlight').isHidden());
      await button('下一步').click(); await step(4);
      await page.keyboard.press('Escape'); assert.equal(await modal.count(), 0); assert.equal(await stored(), null);
    }
    await leave(); await enter(); await intro();
    await page.evaluate(() => { context.board.replaceChildren(); context.board.className = 'board chess'; });
    await frames(); assert.equal(await modal.count(), 0, 'other game removes tutorial');
    // Storage denial must not stop rendering, navigation or cleanup.
    await page.evaluate(() => { Storage.prototype.getItem = () => { throw new Error('denied'); }; Storage.prototype.setItem = () => { throw new Error('denied'); }; show(); });
    await intro(); await button('不再提示').click(); assert.equal(await modal.count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS missing targets, waiting/terminal, Escape, other-game cleanup and unavailable storage');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
