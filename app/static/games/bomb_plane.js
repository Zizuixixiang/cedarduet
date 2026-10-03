(function registerBombPlane() {
  'use strict';
  const DIRS = ['N', 'E', 'S', 'W'];
  const ARROWS = {N: '↑', E: '→', S: '↓', W: '←'};
  const DIRECTIONS = {N: '上', E: '右', S: '下', W: '左'};
  const RESULTS = {miss: '空', hit: '伤', head: '头'};
  // The host resets uiState on every revision. Keep only view/event memory here;
  // selection, deployment drafts and request state still belong to that revision.
  const roomViews = new Map();
  let presentation = null;
  function cancelAutoSwitch(expected=presentation) {
    if (presentation!==expected || !presentation) return;
    if (presentation.pending) {
      const {timer, observer, win, doc, onHidden}=presentation.pending;
      win.clearTimeout(timer); observer.disconnect();
      doc.removeEventListener('visibilitychange',onHidden);
      win.removeEventListener('pagehide',onHidden);
    }
    presentation.pending=null; presentation.stage=null; presentation.incomingQueued=false;
  }
  function keepPresentation(c, ui) {
    const key=viewKey(c);
    if (presentation?.key!==key || presentation.revision!==c.room.revision || presentation.ui!==ui) {
      // A fast reply may advance the authoritative revision while the outgoing
      // result is still on screen. Rebind its context without restarting time.
      if (ui.continueOutgoing && presentation?.key===key && presentation.stage==='outgoing') {
        presentation.incomingQueued=true;
        Object.assign(presentation,{revision:c.room.revision,ui,context:c});
      } else {
        cancelAutoSwitch();
        presentation={key,revision:c.room.revision,ui,context:c,root:null,pending:null,stage:null,incomingQueued:false};
      }
      ui.continueOutgoing=false;
    }
    presentation.context=c;
    if(c.isTerminal || c.state.phase!=='play') cancelAutoSwitch();
  }
  function showResultBeforeSwitch(c, ui, root) {
    presentation.root=root;
    if (ui.presentationRequested) {
      presentation.stage=ui.presentationRequested;
      ui.presentationRequested=null;
    }
    if (!presentation.stage || presentation.pending) return;
    const current=presentation, doc=c.board.ownerDocument, win=doc.defaultView;
    const hasHiddenAncestor=()=>{
      for(let n=current.root;n;n=n.parentElement)
        if(n.hidden || n.classList.contains('hidden') || n.style.display==='none' || n.style.visibility==='hidden') return true;
      return false;
    };
    const isMounted=()=>presentation===current && current.root?.isConnected && !doc.hidden
      && current.context.board.querySelector('.bp-game')===current.root
      && !hasHiddenAncestor();
    if (!isMounted()) {cancelAutoSwitch(current);return;}
    const observer=new win.MutationObserver(()=>{if(!isMounted()) cancelAutoSwitch(current);});
    const onHidden=e=>{if(e.type==='pagehide' || doc.hidden) cancelAutoSwitch(current);};
    const timer=win.setTimeout(()=>{
      const valid=isMounted(), stage=current.stage, queued=current.incomingQueued;
      cancelAutoSwitch(current);
      if (!valid) return;
      const next=stage==='outgoing'?'own':current.context.room.current_player_id===current.context.viewer?.player_id?'attack':'own';
      current.ui.view=next; current.ui.head=null; roomViews.get(current.key).view=next;
      // Only one timer exists: the incoming dwell starts after outgoing finishes.
      if (stage==='outgoing' && queued) current.stage='incoming';
      current.context.helpers.rerender();
    },2000);
    current.pending={timer,observer,win,doc,onHidden};
    observer.observe(doc.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class','hidden','style']});
    doc.addEventListener('visibilitychange',onHidden);
    win.addEventListener('pagehide',onHidden);
  }
  function viewKey(c) { return JSON.stringify([c.room.room_id, c.viewer?.player_id]); }
  const OFFSETS = [[0,0],[-2,1],[-1,1],[0,1],[1,1],[2,1],[0,2],[-1,3],[0,3],[1,3]];
  function el(c, tag, cls, text) {
    const n = c.board.ownerDocument.createElement(tag);
    n.className = cls || '';
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function setup(c) {
    const doc = c.board.ownerDocument;
    if (!doc.getElementById('bomb-plane-styles')) {
      const link = el(c, 'link'); link.id = 'bomb-plane-styles';
      link.rel = 'stylesheet'; link.href = '/static/games/bomb_plane.css'; doc.head.append(link);
    }
    const key=viewKey(c);
    if (c.uiState.bombPlane?.viewKey!==key) {
      const pid=c.viewer?.player_id, opponent=c.state.participant_order?.find(p=>p!==pid);
      const opponentShotCount=(c.state.shots?.[opponent] || []).length;
      const ownShotCount=(c.state.shots?.[pid] || []).length;
      const currentPlayerId=c.room.current_player_id;
      const previous=roomViews.get(key);
      let view=previous?.view;
      let presentationRequested=null, continueOutgoing=false;
      if (!previous || previous.phase!==c.state.phase) {
        view=c.state.phase==='setup' || (!c.isTerminal && c.room.current_player_id!==pid) ? 'own' : 'attack';
      }
      // Consume shot/turn events only when the host creates this revision's UI.
      // Polls and manual redraws must not reclaim a player's chosen view.
      if (previous && c.state.phase==='play' && !c.isTerminal) {
        const incoming=opponentShotCount>previous.lastOpponentShotCount;
        const outgoing=ownShotCount>previous.lastOwnShotCount;
        continueOutgoing=incoming && !outgoing && presentation?.key===key
          && presentation.stage==='outgoing' && Boolean(presentation.pending);
        if (continueOutgoing) view='attack';
        else if (outgoing && previous.lastCurrentPlayerId===pid && currentPlayerId!==pid) {
          presentationRequested='outgoing'; view='attack';
        } else if (previous.lastCurrentPlayerId!==pid && currentPlayerId===pid) {
          presentationRequested=incoming?'incoming':null;
          view=incoming?'own':'attack';
        } else if (currentPlayerId!==pid && (previous.lastCurrentPlayerId===pid || incoming)) view='own';
      }
      roomViews.set(key,{view,phase:c.state.phase,lastOpponentShotCount:opponentShotCount,lastOwnShotCount:ownShotCount,lastCurrentPlayerId:currentPlayerId});
      c.uiState.bombPlane={viewKey:key,view,head:null,direction:'N',busy:false,presentationRequested,continueOutgoing};
    }
    keepPresentation(c,c.uiState.bombPlane);
    return c.uiState.bombPlane;
  }
  function redraw(c, key) {
    c.helpers.rerender();
    if (key) c.board.ownerDocument.querySelector(`[data-bp-key="${key}"]`)?.focus({preventScroll:true});
  }
  function btn(c, text, key, action, disabled = false, primary = false) {
    const n = el(c, 'button', `pixel-btn compact bp-button ${primary ? 'bp-primary' : 'secondary'}`, text);
    n.type = 'button'; n.dataset.bpKey = key; n.disabled = disabled;
    n.addEventListener('click', () => { if (!n.disabled) action(); });
    return n;
  }
  function geometry(plane) {
    const x = plane.head.charCodeAt(0) - 65, y = Number(plane.head.slice(1)) - 1;
    return OFFSETS.map(([dx,dy]) => {
      for (let i=0; i<DIRS.indexOf(plane.direction); i++) [dx,dy] = [-dy,dx];
      const cx=x+dx, cy=y+dy;
      return {cell: `${String.fromCharCode(65+cx)}${cy+1}`, valid: cx>=0 && cx<10 && cy>=0 && cy<10};
    });
  }
  function planesFor(c, own) {
    if (own) return c.privateState?.planes || [];
    if (!c.isTerminal) return [];
    const opponent = c.state.participant_order?.find(p => p !== c.viewer?.player_id);
    return c.state.revealed_planes?.[opponent] || [];
  }
  function preview(c) {
    const ui=setup(c), planes=planesFor(c,true);
    if (planes.length>=3) return {cells:[],valid:false,reason:'已放满3架，可以完成布阵'};
    if (!ui.head) return {cells:[],valid:false,reason:'先点棋盘选机头'};
    const cells=geometry({head:ui.head,direction:ui.direction});
    const reasons=[];
    if (cells.some(c=>!c.valid)) reasons.push('飞机出界');
    if (planes.some(p=>p.head===ui.head)) reasons.push('机头不能重合');
    return {cells,valid:!reasons.length,reason:reasons.join('、')};
  }
  function canAct(c) { return c.canMove && !c.isTerminal && !setup(c).busy; }
  function canDeploy(c) { return canAct(c) && !c.state.ready?.[c.viewer?.player_id]; }
  async function submit(c, move) {
    if (!canAct(c) || !c.helpers.canMove()) return;
    const ui=setup(c), doc=c.board.ownerDocument;
    const focusKey=doc.activeElement?.dataset.bpKey;
    ui.busy=true; redraw(c);
    try { await c.helpers.submitMove(move); }
    finally {
      ui.busy=false; redraw(c);
      // Revision changes replace the draft and DOM. Keep keyboard users in this
      // workflow, but do not steal focus after navigation or another interaction.
      if(focusKey && doc.activeElement===doc.body && c.board.querySelector('.bp-game')?.dataset.roomId===c.room.room_id) {
        const target=doc.querySelector(`[data-bp-key="${focusKey}"]:enabled`)
          || doc.querySelector('[data-bp-key="ready"]:enabled')
          || c.board.querySelector('.bp-cell:enabled')
          || doc.querySelector('[data-bp-key="own"]:enabled');
        target?.focus({preventScroll:true});
      }
    }
  }
  function renderTabs(c) {
    const ui=setup(c), deploying=c.state.phase==='setup' && !c.isTerminal;
    const tabs=el(c,'div','bp-tabs');
    tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','棋盘视图');
    const views=deploying?[['own','我的布阵'],['attack','攻击对方']]:[['attack','攻击对方'],['own','我的布阵']];
    for(const [view,label] of views) {
      const b=btn(c,label,view,()=>{cancelAutoSwitch();ui.presentationRequested=null;ui.view=view;roomViews.get(viewKey(c)).view=view;ui.head=null;redraw(c,view);},deploying && view==='attack');
      b.setAttribute('aria-pressed',String(ui.view===view));
      if(deploying && view==='attack') b.title='双方布阵完成后可攻击';
      tabs.append(b);
    }
    return tabs;
  }
  function renderBoard(c) {
    const ui=setup(c), own=ui.view==='own', deploying=c.state.phase==='setup' && !c.isTerminal;
    const count=planesFor(c,true).length, editable=deploying && canDeploy(c) && count<3;
    c.helpers.setBoardLayout({rows:10,cols:10,large:true,ariaLabel:own?'我的布阵':'攻击对方棋盘'});
    const root=el(c,'section','bp-game'); root.dataset.roomId=c.room.room_id;
    root.append(renderTabs(c));
    const pid=c.viewer?.player_id, opponent=c.state.participant_order?.find(p=>p!==pid);
    const locked=c.state.ready?.[pid];
    const status=el(c,'p','bp-status'); status.setAttribute('role','status');
    status.textContent=deploying
      ? `已放置 ${count}/3 架${ui.busy?' · 提交中…':locked?' · 已完成，等待对方':'（可重叠）'}`
      : c.isTerminal?'对局结束 · 全图公开':ui.busy?'正在提交…':canAct(c)?'轮到你攻击':'等待对方攻击';
    root.append(status);
    const incoming=c.state.shots?.[opponent] || [], latestIncoming=incoming[incoming.length-1];
    if (!deploying) {
      const notice=el(c,'p','bp-incoming',latestIncoming
        ? `对方刚攻击 ${latestIncoming.cell} · ${RESULTS[latestIncoming.result]}` : '尚未收到对方攻击');
      notice.setAttribute('role','status');
      root.append(notice);
    }
    const shots=c.state.shots?.[own?opponent:pid] || [];
    const grid=el(c,'div','bp-grid'); grid.setAttribute('aria-label',own?'己方十行十列':'对方十行十列');
    grid.append(el(c,'span','bp-axis',''));
    for (const col of 'ABCDEFGHIJ') grid.append(el(c,'span','bp-axis',col));
    const occupancy=new Map();
    planesFor(c,own).forEach((p,i)=>geometry(p).forEach(({cell})=>{
      // A later plane's body must never obscure an earlier plane's head.
      if(!occupancy.get(cell)?.head) occupancy.set(cell,{head:cell===p.head,index:i,direction:p.direction});
    }));
    const feedback=new Map(shots.map(s=>[s.cell,s.result]));
    const pending=own && editable?preview(c):{cells:[],valid:false};
    const previewCells=new Set(pending.cells.filter(p=>p.valid).map(p=>p.cell));
    for(let row=1;row<=10;row++) {
      grid.append(el(c,'span','bp-axis',row));
      for(const col of 'ABCDEFGHIJ') {
        const cell=`${col}${row}`, plane=occupancy.get(cell), result=feedback.get(cell);
        const selectable=deploying?own && editable:canAct(c) && !own && !result;
        const selected=ui.head===cell, inPreview=previewCells.has(cell);
        const latest=shots[shots.length-1]?.cell===cell;
        const head=plane?.head || (inPreview && selected);
        const b=el(c,'button',`bp-cell${plane?' bp-plane':''}${head?' bp-plane-head':''}${result?` bp-${result}`:''}${inPreview?` bp-preview ${pending.valid?'':'bp-invalid'}`:''}${selected?' bp-selected':''}${latest?own?' bp-latest-incoming':' bp-latest-outgoing':''}`,
          result?RESULTS[result]:(inPreview && selected?ARROWS[ui.direction]:plane?(plane.head?ARROWS[plane.direction]:String(plane.index+1)):selected?'◎':''));
        b.type='button'; b.dataset.cell=cell; b.dataset.bpKey=cell; b.disabled=!selectable;
        b.setAttribute('aria-label',`${cell}，${result?RESULTS[result]:plane?`飞机${plane.index+1}${plane.head?`机头朝${DIRECTIONS[plane.direction]}`:'机体'}`:own?'空格':'未攻击'}${inPreview?'，摆放预览':''}${latest?own?'，对方最新攻击':'，我方最新攻击':''}`);
        b.setAttribute('aria-pressed',String(selected));
        b.addEventListener('click',()=>{ui.head=cell;redraw(c,cell);});
        grid.append(b);
      }
    }
    root.append(grid);
    if(!deploying) root.append(el(c,'p','bp-help bp-legend','空 = 未命中 · 伤 = 机体 · 头 = 机头'));
    c.board.append(root); showResultBeforeSwitch(c,ui,root); return true;
  }
  function renderFleet(c, root) {
    const planes=planesFor(c,true);
    if(!planes.length) return;
    const list=el(c,'ol','bp-fleet'); list.setAttribute('aria-label','已放置的飞机');
    planes.forEach((p,i)=>{
      const item=el(c,'li');
      item.textContent=`飞机${i+1} ${p.head} ${ARROWS[p.direction]}`;
      item.setAttribute('aria-label',`飞机${i+1}，机头 ${p.head}，朝${DIRECTIONS[p.direction]}`);
      list.append(item);
    });
    root.append(list);
  }
  function renderControls(c) {
    const ui=setup(c), deploying=c.state.phase==='setup', own=ui.view==='own', active=canAct(c);
    const root=el(c,'section','bp-controls');
    const count=planesFor(c,true).length, pid=c.viewer?.player_id, locked=c.state.ready?.[pid];
    if(deploying && !c.isTerminal) {
      if(!locked) {
        if(count<3) {
          const primary=el(c,'div','bp-placement'), p=preview(c);
          if(ui.head && !p.valid) {
            const error=el(c,'p','bp-help bp-error',p.reason);
            error.id='bp-selection'; error.setAttribute('role','status'); root.append(error);
          }
          const rotate=btn(c,`旋转 ↻ · ${ARROWS[ui.direction]}${DIRECTIONS[ui.direction]}`,'rotate',()=>{
            ui.direction=DIRS[(DIRS.indexOf(ui.direction)+1)%DIRS.length];redraw(c,'rotate');
          },!canDeploy(c));
          rotate.setAttribute('aria-label',`旋转，当前朝${DIRECTIONS[ui.direction]}`);
          const place=btn(c,'放置这架飞机','place',()=>submit(c,{action:'place',head:ui.head,direction:ui.direction}),!canDeploy(c)||!p.valid,true);
          if(ui.head && !p.valid) place.setAttribute('aria-describedby','bp-selection');
          if(!ui.head) place.setAttribute('aria-label','放置这架飞机，请先选择机头');
          primary.append(rotate,place); root.append(primary);
        } else {
          root.append(btn(c,'完成布阵','ready',()=>submit(c,{action:'ready'}),!canDeploy(c),true));
        }
      }
      renderFleet(c,root);
      if(!locked) {
        const tools=el(c,'div','bp-tools');
        const shuffle=btn(c,'随机布置3架','shuffle',()=>submit(c,{action:'shuffle'}),!canDeploy(c));
        const undo=btn(c,'撤销上一架','undo',()=>submit(c,{action:'undo'}),!canDeploy(c)||!count);
        const clear=btn(c,'清空布阵','clear',()=>submit(c,{action:'clear'}),!canDeploy(c)||!count);
        clear.classList.add('bp-clear'); tools.append(shuffle,undo,clear); root.append(tools);
      }
    } else if(!c.isTerminal && !own) {
      const guessed=(c.state.shots?.[pid]||[]).some(s=>s.cell===ui.head);
      root.append(btn(c,ui.head?`确认攻击 ${ui.head}`:'确认攻击','attack-confirm',()=>submit(c,{action:'attack',cell:ui.head}),!active||!ui.head||guessed,true));
    }
    c.controls.append(root);
  }
  window.DuelGameUI.register('bomb_plane',{
    glyph:'炸', boardLabel:'炸飞机', participantPresentation:'generic',
    ownsPrivateStatePresentation:true, usesStandardMoveConfirmation:false, usesEmbeddedActionFeedback:true,
    renderBoard,renderControls,
  });
}());
