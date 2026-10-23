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
let combinations = 0;
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
        if(process.env.FOUR_COLOR_DOM_CASE && process.env.FOUR_COLOR_DOM_CASE!==`${mode}:${count}`) continue;
        combinations++;
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
        assert.equal(doc.querySelector('.fc-controls').parentElement.className, 'fc-action-slot');
        assert.equal(context.controls.children.length, 0, 'only one action area, beside the hand');
        const handBefore = JSON.stringify(context.privateState);
        context.participants[1].display_name='Codex_杉星_0828';
        if(context.participants[2]) context.participants[2].display_name='名字较长的四色牌玩家';
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
        const groups = [['R0-1'], ['Y1-1','Y1-2'], ['R6-2','Y6-2','G6-2'], ['W6-1','W6-2','W6-3','W6-4']];
        state.participant_order.forEach(pid=>{state.melds[pid]=groups.map(cards=>({cards}));});
        if (mode === 'jiuzhi') {
          state.broken.p1=['R6-1','G6-1'];
          state.hand_counts.p1=22; state.closed_pair_counts.p1=1;
          context.privateState.closed_pairs=[['R2-1','R2-2']];
        }
        if (mode === 'qunhu') state.listening.p1=['Y0-1','Y1-1','Y2-1','G0-1','G0-2'];
        state.discards=['W1-1','W2-1']; state.offer=null;
        // Rotate the table for every seat, including a viewer whose seat is not the dealer's.
        for (const pid of state.participant_order) {
          context.viewer={player_id:pid}; render();
          const self=doc.querySelector('.fc-seat-self');
          assert.equal(self.dataset.playerId,pid);
          assert.ok(self.classList.contains('fc-position-south'), 'viewer always sits below the table');
          const expectedPositions = {
            2:['south','north'], 3:['south','east','west'],
            4:['south','east','north','west'], 5:['south','east','north-east','north-west','west'],
          }[count];
          const offset=state.participant_order.indexOf(pid);
          expectedPositions.forEach((position,index)=>{
            const owner=state.participant_order[(offset+index)%count];
            assert.equal(doc.querySelector(`.fc-seat[data-player-id="${owner}"]`).dataset.position,position);
          });
          for (const seat of doc.querySelectorAll('.fc-seat')) {
            const owner=seat.dataset.playerId;
            const expected=[...state.melds[owner].flatMap(g=>g.cards),...(state.broken?.[owner]||[]),...(state.listening?.[owner]||[])];
            assert.deepEqual([...seat.querySelectorAll('[data-card-id]')].map(n=>n.dataset.cardId).sort(), expected.sort());
            assert.equal(seat.querySelectorAll('.fc-meld').length,4);
            assert.deepEqual([...seat.querySelectorAll('.fc-meld')].map(g=>[...g.querySelectorAll('[data-card-id]')].map(n=>n.dataset.cardId)),groups,'mixed-color groups retain server grouping and order');
          }
          assert.equal(doc.querySelector('.fc-discard-history .fc-card-current').dataset.cardId,'W2-1');
          assert.equal(doc.querySelectorAll('.fc-offer').length,0);
          assert.equal(doc.querySelectorAll('.fc-table [data-card-id="R2-1"]').length,0,'sealed pairs never enter the public table');
          const rack=doc.querySelector('.fc-rack'), discardPile=doc.querySelector('.fc-discards');
          assert.ok(discardPile.closest('.fc-table-center'), 'neutral history belongs beside the stock');
          assert.equal(doc.querySelectorAll('details.fc-discards').length,0);
          assert.ok(discardPile.compareDocumentPosition(rack) & window.Node.DOCUMENT_POSITION_FOLLOWING);
          for (const seat of doc.querySelectorAll('.fc-seat')) {
            assert.ok(seat.querySelector('.fc-seat-identity .room-participant-avatar'));
            assert.ok(seat.querySelector('.fc-seat-identity .fc-seat-counts'));
            const playerName=seat.querySelector('.fc-seat-name');
            assert.equal(playerName.title,playerName.textContent,'full name is available without vertical text');
            const side=/^(east|west)/.test(seat.dataset.position);
            assert.equal(seat.querySelectorAll('.fc-side-card').length, side ? seat.querySelectorAll('[data-card-id]').length : 0);
            const owner=seat.dataset.playerId, info=seat.querySelector('.fc-seat-counts');
            const shown=new Set([...(state.broken?.[owner]||[]),...(state.listening?.[owner]||[])]).size;
            const hidden=Math.max(0,state.hand_counts[owner]-shown);
            const full=[`暗牌 ${hidden} 张`], compact=[`暗${hidden}`];
            if(Number.isInteger(state.closed_pair_counts?.[owner])) {
              full.push(`扣置 ${state.closed_pair_counts[owner]} 对`); compact.push(`扣${state.closed_pair_counts[owner]}`);
            }
            assert.equal(info.title,full.join(' · '));
            assert.equal(info.getAttribute('aria-label'),info.title);
            assert.deepEqual([...info.querySelectorAll('.fc-seat-count')].map(n=>n.textContent),compact);
            assert.ok([...info.querySelectorAll('.fc-seat-count')].every(n=>n.getAttribute('aria-hidden')==='true'));
          }
          cases++;
        }
        for (const discards of [[], ['W1-1','W2-1']]) {
          for (const offer of [null, {card:'R0-1',from_deck:false}, {card:'R0-1',from_deck:true}, {card:'W2-1',from_deck:false}]) {
            state.discards=discards; state.offer=offer; render();
            const pile=doc.querySelector('.fc-discards');
            const ids=[...doc.querySelectorAll('.fc-discard-history [data-card-id], .fc-offer [data-card-id]')].map(n=>n.dataset.cardId);
            assert.deepEqual(ids.sort(), [...new Set([...discards, ...(offer?[offer.card]:[])])].sort());
            assert.equal(doc.querySelectorAll('.fc-discard-history .fc-card-current, .fc-offer .fc-card-current').length, offer || discards.length ? 1 : 0);
            if (offer) assert.equal(doc.querySelector('.fc-offer .fc-meta').textContent,offer.from_deck?'公开翻牌':'当前弃牌');
            else assert.equal(doc.querySelectorAll('.fc-offer').length,0);
            assert.doesNotMatch(pile.textContent,/暂无弃牌|弃牌区 ·/);
            assert.equal(doc.querySelectorAll('.fc-turn-indicator').length,1);
            assert.equal(doc.querySelectorAll('.fc-seat-counts > .fc-turn-indicator').length,1);
            assert.equal(doc.querySelector('.fc-turn-indicator').textContent,'行动中');
            assert.equal(new Set([...doc.querySelectorAll('.fc-seat')].map(n=>n.dataset.position)).size,count);
          }
        }
        state.discards=['W1-1','W2-1','R5-2','G5-2'];
        state.discard_sources={'W1-1':'p0','W2-1':'p1','R5-2':null,'Y0-4':'p1'};
        state.offer={card:'W2-1',source:'p1',from_deck:false}; render();
        assert.equal(doc.querySelectorAll('.fc-history').length,1,'pending card does not also appear in its history');
        assert.equal(doc.querySelector('.fc-history').dataset.playerId,'p0');
        assert.equal(doc.querySelector('.fc-history').dataset.position,doc.querySelector('.fc-seat[data-player-id="p0"]').dataset.position);
        assert.deepEqual([...doc.querySelectorAll('.fc-neutral-pile [data-card-id]')].map(n=>n.dataset.cardId),['R5-2','G5-2'],'deck and old unknown cards remain neutral');
        assert.equal(doc.querySelectorAll('.fc-neutral-pile').length,1);
        assert.equal(doc.querySelectorAll('.fc-neutral-pile > .fc-meta').length,0);
        assert.match(doc.querySelector('.fc-neutral-pile [data-card-id="R5-2"]').getAttribute('aria-label'),/^无人收取的公开翻牌/);
        assert.match(doc.querySelector('.fc-neutral-pile [data-card-id="G5-2"]').getAttribute('aria-label'),/^来源未记录的历史弃牌/);
        assert.equal(doc.querySelectorAll('.fc-table [data-card-id="Y0-4"]').length,0,'metadata alone never exposes a card');
        for(const pile of doc.querySelectorAll('.fc-discard-history')) assert.equal(pile.dataset.axis,count!==5 && /^(west|east)$/.test(pile.dataset.position)?'y':'x','player and neutral histories declare their single scroll axis');
        state.discards=['G4-1','G6-1']; state.discard_sources={}; state.offer=null; render();
        assert.equal(doc.querySelectorAll('.fc-discard-history').length,1);
        assert.equal(doc.querySelector('.fc-discard-history').dataset.axis,'x');
        assert.equal(doc.querySelectorAll('.fc-history').length,0,'two legacy greens stay neutral');
        state.offer=null;
        context.canMove=false; render();
        assert.equal(doc.querySelectorAll('.fc-hand button:not(:disabled)').length,0);
        context.isTerminal=true; state.flow.phase='finished'; state.result={reason:'wall_exhausted'}; render();
        assert.equal(doc.querySelectorAll('.fc-controls button').length,0);
        assert.equal(doc.querySelectorAll('.fc-seat-current').length,0);
        assert.equal(doc.querySelectorAll('.fc-turn-indicator').length,0);
        assert.match(doc.querySelector('.fc-result').textContent,/流局/);
        context.privateState={}; render();
        assert.equal(doc.querySelectorAll('.fc-hand button').length,0);
        assert.equal(doc.querySelectorAll('.fc-closed').length,0);
        if (count === 4) {
          for (const kind of ['hu','empty',...(mode==='qunhu'?['listen']:[])]) {
            const scenarioDB=path.join(tmp,`${mode}-${kind}.db`);
            const scenario=backend({action:'seed',game_type:`four_color_${mode}`,fixture:kind,count:4},scenarioDB);
            context={...context,state:scenario.room.board_state,room:scenario.room,
              participants:scenario.room.participants,privateState:scenario.room.private_state,
              viewer:{player_id:'p0'},uiState:{},canMove:true,isTerminal:false};
            render();
            if (kind==='listen') doc.querySelector('.fc-hand [data-card-id="R4-4"]').click();
            const buttons=[...doc.querySelectorAll('.fc-controls button')];
            for (const button of buttons.filter(b=>!b.disabled)) {
              sent.length=0;
              button.click(); await new Promise(resolve=>setImmediate(resolve));
              assert.equal(sent.length,1,`${mode}/${kind}/${button.dataset.fcAction}: one submission`);
              assert.ok(context.privateState.legal_actions.includes(sent[0]),'send the exact server action object');
              // Each response branches from the same real engine snapshot.
              const branchDB=path.join(tmp,'branch.db'); fs.copyFileSync(scenarioDB,branchDB);
              const result=backend({action:'move',room_id:scenario.room.room_id,
                revision:scenario.room.revision,move:sent[0]},branchDB);
              assert.equal(result.ok,true,`${mode}/${kind}/${button.dataset.fcAction}: engine accepts UI action`);
              if (button.dataset.fcAction.startsWith('hu')) assert.equal(result.room.board_state.result.reason,'hu');
              if (kind==='empty') assert.equal(result.room.board_state.result.reason,'wall_exhausted');
              fs.rmSync(branchDB,{force:true});
            }
            assert.ok(buttons.some(b=>!b.disabled),'fixture exposes at least one actionable button');
          }
        }
        dom.window.close();
      }
    }
    assert.ok(combinations,'FOUR_COLOR_DOM_CASE must match a mode:count case');
    console.log(`PASS: ${cases} seat perspectives; ${combinations} real-engine mode/count combinations; public ownership, sealed pairs, selection/submit, response actions, hu/listen/draw and terminal states`);
  } finally {fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
