/* Exercise the production renderer with real private/public projections, without a server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'four-color-table-dom-'));
const source = fs.readFileSync(path.join(root, 'app/static/games/four_color_tenhu.js'), 'utf8');
let cases = 0;
function backend(request, db) {
  const input=path.join(tmp,'input.json'), output=path.join(tmp,'output.json'), error=path.join(tmp,'error.txt');
  fs.writeFileSync(input, JSON.stringify(request));
  const stdio=[fs.openSync(input,'r'),fs.openSync(output,'w'),fs.openSync(error,'w')];
  try {
    const result = spawnSync(path.join(root, '.venv/bin/python'), [path.join(__dirname, 'four_color_ui_fixture.py')], {
      cwd: root, env: {...process.env, PYTHONDONTWRITEBYTECODE:'1', PYTHONPATH:root, DUEL_DB_PATH:db},
      stdio, timeout:15000,
    });
    assert.equal(result.status, 0, `${result.error || result.signal || ''} ${fs.readFileSync(error,'utf8')}`);
    return JSON.parse(fs.readFileSync(output,'utf8'));
  } finally {stdio.forEach(fd=>fs.closeSync(fd));}
}
(async () => {
  try {
    for (const [mode, counts] of [['tenhu',[2,3,4]], ['jiuzhi',[4]], ['qunhu',[3,4,5]]]) {
      for (const count of counts) {
        const db = path.join(tmp, `${mode}-${count}.db`);
        const fixture = backend({action:'seed',game_type:`four_color_${mode}`,count}, db);
        assert.equal(fixture.ok, true);
        const dom = new JSDOM('<div id="board"></div><div id="controls"></div>', {runScripts:'outside-only'});
        const {window} = dom, doc = window.document, renderers = {};
        window.DuelGameUI = {register: (kind, renderer) => { renderers[kind] = renderer; }};
        window.eval(source);
        const renderer = renderers[fixture.room.game_type], sent = [];
        assert.equal(renderer.participantPresentation, 'embedded');
        let context;
        function render() {
          context.board.replaceChildren(); context.controls.replaceChildren();
          renderer.renderBoard(context); renderer.renderControls(context);
        }
        context = {
          board:doc.getElementById('board'), controls:doc.getElementById('controls'),
          state:fixture.room.board_state, room:fixture.room, participants:fixture.room.participants,
          viewer:{player_id:'p0'}, privateState:fixture.room.private_state, uiState:{},
          canMove:true, isTerminal:false,
          helpers:{setBoardLayout(){},renderParticipantAvatar(target){target.textContent='人';},rerender:render,
            canMove:()=>context.canMove,submitMove:async move=>sent.push(move)},
        };
        render();
        const handBefore = JSON.stringify(context.privateState);
        assert.equal(doc.querySelectorAll('.fc-seat').length, count);
        assert.equal(doc.querySelectorAll('.fc-hand button').length, context.privateState.hand.length);
        const selectable = doc.querySelector('.fc-hand button:not(:disabled)');
        assert.ok(selectable);
        const id = selectable.dataset.cardId;
        selectable.click();
        assert.equal(sent.length, 0, 'selecting does not submit');
        assert.equal(doc.querySelector('.fc-hand [aria-pressed="true"]').dataset.cardId, id);
        doc.querySelector('[data-fc-action="discard"]').click();
        await Promise.resolve();
        assert.equal(sent.length, 1);
        assert.equal(sent[0].action_id, `discard:${id}`);
        assert.equal(JSON.stringify(context.privateState), handBefore, 'rendering never mutates private state');
        const moved = backend({action:'move',room_id:fixture.room.room_id,revision:fixture.room.revision,move:sent[0]}, db);
        assert.equal(moved.ok, true, 'unchanged action is accepted by the real engine');
        assert.equal(moved.room.revision, fixture.room.revision + 1);

        // Stress public presentation separately from rules. Forbidden raw fields must never be read.
        const state = context.state;
        for (const key of ['hands','closed_pairs','wall','reserve']) {
          Object.defineProperty(state, key, {get(){throw new Error(`read forbidden state.${key}`);}});
        }
        const groups = [['R0-1'], ['Y1-1','Y1-2'], ['G3-1','G4-1','G5-1'], ['W6-1','W6-2','W6-3','W6-4']];
        state.participant_order.forEach(pid=>{state.melds[pid]=groups.map(cards=>({cards}));});
        if (mode === 'jiuzhi') {
          state.broken.p1=['R6-1','G6-1'];
          context.privateState.closed_pairs=[['R2-1','R2-2']];
        }
        if (mode === 'qunhu') state.listening.p1=['Y0-1','Y1-1','Y2-1','G0-1','G0-2'];
        state.discards=['W1-1','W2-1']; state.offer=null;
        // Rotate the table for every seat, including a viewer whose seat is not the dealer's.
        for (const pid of state.participant_order) {
          context.viewer={player_id:pid}; render();
          const self=doc.querySelector('.fc-seat-self');
          assert.equal(self.dataset.playerId,pid);
          assert.ok(self.classList.contains(count<4?'fc-position-south':'fc-position-se'));
          for (const seat of doc.querySelectorAll('.fc-seat')) {
            const owner=seat.dataset.playerId;
            const expected=[...state.melds[owner].flatMap(g=>g.cards),...(state.broken?.[owner]||[]),...(state.listening?.[owner]||[])];
            assert.deepEqual([...seat.querySelectorAll('[data-card-id]')].map(n=>n.dataset.cardId).sort(), expected.sort());
            assert.equal(seat.querySelectorAll('.fc-meld').length,4);
          }
          assert.equal(doc.querySelector('.fc-offer [data-card-id]').dataset.cardId,'W2-1');
          assert.equal(doc.querySelectorAll('.fc-table [data-card-id="R2-1"]').length,0,'sealed pairs never enter the public table');
          cases++;
        }
        context.canMove=false; render();
        assert.equal(doc.querySelectorAll('.fc-hand button:not(:disabled)').length,0);
        context.isTerminal=true; state.flow.phase='finished'; state.result={reason:'wall_exhausted'}; render();
        assert.equal(doc.querySelectorAll('.fc-controls button').length,0);
        assert.equal(doc.querySelectorAll('.fc-seat-current').length,0);
        assert.match(doc.querySelector('.fc-result').textContent,/流局/);
        context.privateState={}; render();
        assert.equal(doc.querySelectorAll('.fc-hand button').length,0);
        assert.equal(doc.querySelectorAll('.fc-closed').length,0);
        dom.window.close();
      }
    }
    console.log(`PASS: ${cases} seat perspectives; 7 real-engine mode/count combinations; public ownership, sealed pairs, selection/submit, waiting and terminal states`);
  } finally {fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
