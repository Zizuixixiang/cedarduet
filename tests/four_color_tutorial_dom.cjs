/* DOM lifecycle tests only: no layout/touch/screenshot claims. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../app/static/games/four_color_tenhu.js'),'utf8');
const key='cedarduet.four_color.tutorial.v1';
function visit(remember, blockedStorage=false) {
  const dom=new JSDOM('<main id="gameView"><section id="inviteRoomPanel"></section><div id="board"></div><div id="controls"></div><button id="origin">开局</button></main>',{url:'http://four-color.test/',runScripts:'outside-only'});
  const w=dom.window, d=w.document, renderers={};
  // jsdom has no native modal implementation. Stub only its open/close API.
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  if(remember)w.localStorage.setItem(key,remember);
  if(blockedStorage)Object.defineProperty(w,'localStorage',{get(){throw new Error('storage disabled');}});
  w.DuelGameUI={register:(type,renderer)=>renderers[type]=renderer};w.eval(source);
  const c={board:d.getElementById('board'),controls:d.getElementById('controls'),room:{room_id:'WAIT',room_kind:'invite',status:'waiting'},viewer:{player_id:'p0'},state:{},uiState:{}};
  const click=text=>{const b=[...d.querySelectorAll('.fc-tutorial button')].find(n=>n.textContent===text);assert.ok(b,text);b.click();};
  const sync=()=>renderers.four_color_tenhu.syncTutorial(c);
  d.getElementById('origin').focus();sync();
  return {dom,w,d,c,click,sync,renderers};
}
(async()=>{
  let v=visit();const original=JSON.stringify(v.c.room);assert.ok(v.d.querySelector('dialog[open]'));
  assert.ok(v.d.querySelector('.fc-tutorial-spotlight').hidden);
  assert.ok(v.d.querySelector('.fc-tutorial-progress').hidden);
  assert.equal(v.d.querySelector('#fc-tutorial-copy').textContent,'上下横滑、左右竖滑，就能查看被叠住的牌。');
  v.click('查看教程');assert.equal(v.d.querySelector('.fc-tutorial-demo').dataset.axis,'x');
  assert.equal(v.d.querySelector('.fc-tutorial-spotlight').hidden,false);
  assert.ok(v.d.querySelector('.fc-tutorial').classList.contains('has-target'));
  assert.ok(!v.d.querySelector('.fc-tutorial-panel').contains(v.d.querySelector('.fc-tutorial-demo')));
  assert.equal(v.d.querySelectorAll('.fc-tutorial-demo .fc-seat-public').length,2);
  assert.equal(v.d.querySelectorAll('#board .fc-card, .fc-tutorial [data-card-id]').length,0,'demo cannot enter board or expose actionable IDs');
  v.click('下一步');assert.equal(v.d.querySelector('.fc-tutorial-demo').dataset.axis,'y');
  v.click('上一步');assert.equal(v.d.querySelector('.fc-tutorial-demo').dataset.axis,'x');
  v.click('×');assert.equal(v.w.localStorage.getItem(key),null);assert.equal(v.d.activeElement.id,'origin');
  assert.equal(v.d.querySelector('.fc-tutorial-demo'),null);
  assert.equal(v.d.querySelector('.fc-tutorial-spotlight'),null);v.sync();assert.equal(v.d.querySelector('dialog'),null,'no reopen on polling');
  assert.equal(JSON.stringify(v.c.room),original);
  // Leaving and re-entering in the SPA resets the visit after X.
  v.d.getElementById('gameView').classList.add('hidden');await Promise.resolve();
  v.d.getElementById('gameView').classList.remove('hidden');v.sync();assert.ok(v.d.querySelector('dialog[open]'));
  v.click('不再提示');const dismissed=v.w.localStorage.getItem(key);assert.equal(dismissed,'dismissed');v.dom.window.close();
  v=visit(dismissed);assert.equal(v.d.querySelector('dialog'),null);v.dom.window.close();
  v=visit();v.click('查看教程');v.click('下一步');v.click('完成');const completed=v.w.localStorage.getItem(key);assert.equal(completed,'completed');v.dom.window.close();
  v=visit(completed);assert.equal(v.d.querySelector('dialog'),null);v.dom.window.close();
  for(const game of ['tenhu','jiuzhi','qunhu']) {
    v=visit();v.click('查看教程');v.c.room.status='playing';
    v.renderers[`four_color_${game}`].syncTutorial(v.c);
    assert.equal(v.d.querySelector('.fc-tutorial, .fc-tutorial-demo'),null,'start removes simulation in same call');
    assert.equal(v.w.localStorage.getItem(key),null);v.sync();assert.equal(v.d.querySelector('dialog'),null);
    v.dom.window.close();
  }
  v=visit();v.click('×');v.c.room={room_id:'ALREADYPLAYING',room_kind:'invite',status:'playing'};
  v.d.getElementById('board').innerHTML='<div class="fc-game"></div>';
  v.sync();v.click('查看教程');assert.equal(v.d.querySelector('.fc-tutorial-demo').dataset.axis,'x','already-playing visit must include hands-on practice');
  v.click('下一步');assert.equal(v.d.querySelector('.fc-tutorial-demo').dataset.axis,'y');
  v.click('完成');assert.equal(v.d.querySelector('.fc-tutorial-demo'),null);assert.equal(v.w.localStorage.getItem(key),'completed');v.dom.window.close();
  v=visit();const event=new v.w.Event('cancel',{cancelable:true});v.d.querySelector('dialog').dispatchEvent(event);
  assert.ok(event.defaultPrevented);assert.equal(v.d.querySelector('dialog'),null);assert.equal(v.w.localStorage.getItem(key),null);v.dom.window.close();
  v=visit(null,true);v.click('不再提示');v.sync();assert.equal(v.d.querySelector('dialog'),null);v.dom.window.close();
  console.log('PASS: X/Esc, SPA re-entry, dismissed/completed persistence, storage failure, isolated demo, synchronous start cleanup in all three modes');
})().catch(e=>{console.error(e);process.exitCode=1;});
