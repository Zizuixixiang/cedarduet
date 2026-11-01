// DOM/interaction regression only; this does not measure browser layout.
// NODE_PATH=/opt/cedartoy/node_modules node tests/terminal_room_delete.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('app/static/app.js', 'utf8').split('\nensureInitialRouteState();')[0];
const html = fs.readFileSync('app/static/index.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function harness() {
  const dom = new JSDOM(html, {url:'https://offline.invalid/', runScripts:'outside-only'});
  const w = dom.window, calls = [], media = {matches:true, addEventListener(){}};
  let confirm = true, confirmations = 0, failure = false, release;
  w.matchMedia = () => media;
  w.confirm = () => { confirmations++; return confirm; };
  const fixtures = ['finished', 'archived', 'playing', 'waiting', 'pending'].map((status, i) => ({
    room_id:`TEST000${i}`, game_type:'tictactoe', game_name:'井字棋', status,
    ai_player_id:'101', ai_name:'合成小机', participant_names:['合成小机', '合成人类'],
    turn:'ai', updated_at:new Date().toISOString(), preserved:status==='archived', stake:0,
    auto_delete_at:new Date(Date.now()+6*86400000).toISOString(),
  }));
  let records = fixtures.slice();
  const identity = () => ({bound:true, human_player_id:'human:test', human_name:'合成人类',
    identity_label:'合成身份', machines:[], games:[], wallet:{balance:500}, rooms:records});
  w.fetch = async (url, options) => {
    const path = new URL(url, w.location.href).pathname;
    calls.push({path, options});
    let data = {}, status = 200;
    if (path.endsWith('/delete')) {
      await new Promise(resolve => { release = resolve; });
      if (failure) { status = 409; data = {message:'测试：当前对局不能删除'}; }
      else {
        records = records.filter(r => !path.includes(r.room_id));
        data = {message:'已删除合成对局'};
      }
    } else if (path === '/api/spectating/rooms') data = {rooms:records.map(r => ({...r, spectating:true}))};
    else if (path === '/api/whoami') data = identity();
    else if (path === '/api/notifications/unread') data = {unread:{total:0}, notifications:[]};
    else throw new Error(`Unexpected request ${path}`);
    return {ok:status===200, status, headers:{get:()=>null}, json:async()=>data};
  };
  w.eval(source + `\nwindow.testAPI = {
    setup(data, records, scope) { identity=data; setRoomListScope(scope); renderRooms(records); showView('lobbyView'); },
    deleteRoom, updateRoomPreservation, syncSpectatorRetentionControls,
    get scope(){return roomListScope;}, get perspective(){return spectatorPerspectives.get('TEST0000');},
    setPerspective(){spectatorPerspectives.set('TEST0000','102');},
  };`);
  const setup = scope => w.testAPI.setup(identity(), fixtures.map(r => ({...r, spectating:scope==='machines'})), scope);
  const button = id => w.document.querySelector(`[data-delete-room-id="${id}"]`);
  return {dom,w,calls,media,fixtures,setup,button,
    get confirmations(){return confirmations;}, setConfirm(v){confirm=v;}, setFailure(v){failure=v;},
    release(){release();}, posts(){return calls.filter(c=>c.options.method==='POST');}};
}
(async () => {
  for (const scope of ['mine','machines']) {
    const h = harness(); h.setup(scope);
    assert.equal(h.w.document.querySelectorAll('[data-delete-room-id]').length,2);
    for (const status of ['playing','waiting','pending']) {
      const room = h.fixtures.find(r=>r.status===status);
      assert.equal(h.button(room.room_id),null);
      await h.w.testAPI.deleteRoom({...room,spectating:scope==='machines'});
    }
    assert.equal(h.confirmations,0);
    h.setConfirm(false); h.button('TEST0000').click(); await tick();
    assert.equal(h.posts().length,0);
    h.setConfirm(true); h.w.testAPI.setPerspective();
    const original = h.button('TEST0000'); original.click(); original.click();
    await h.w.testAPI.deleteRoom({...h.fixtures[0],spectating:scope==='machines'});
    await h.w.testAPI.updateRoomPreservation('TEST0000',true,{spectating:scope==='machines'});
    assert.equal(h.posts().length,1);
    assert.equal(h.confirmations,2);
    assert([...original.parentNode.querySelectorAll('button')].every(b=>b.disabled));
    h.release(); await tick();
    assert.equal(h.button('TEST0000'),null);
    assert.equal(h.w.testAPI.scope,scope);
    assert.equal(h.w.testAPI.perspective,'102');
    assert(h.calls.some(c=>c.path===(scope==='machines'?'/api/spectating/rooms':'/api/whoami')));
    assert(!h.calls.some(c=>/^\/api\/rooms\/[^/]+$/.test(c.path)), 'delete must not open a room');
    h.dom.window.close();
  }
  {
    const h=harness(); h.setup('machines'); h.setFailure(true);
    const original=h.button('TEST0000'); original.click(); h.release(); await tick();
    assert.equal(h.button('TEST0000'),original, 'failed request keeps the existing card');
    assert.equal(original.disabled,false);
    assert.equal(h.w.testAPI.scope,'machines');
    assert(!h.calls.some(c=>c.path==='/api/whoami'||c.path==='/api/spectating/rooms'));
    assert(h.w.document.body.textContent.includes('测试：当前对局不能删除'));
    h.dom.window.close();
  }
  {
    const h=harness(); h.media.matches=false; h.setup('machines');
    for(const controls of h.w.document.querySelectorAll('.spectator-retention-controls')) {
      assert.equal(controls.getAttribute('aria-hidden'),'true'); assert.equal(controls.inert,true);
      assert([...controls.querySelectorAll('button')].every(b=>b.disabled));
    }
    await h.w.testAPI.deleteRoom({...h.fixtures[0],spectating:true});
    assert.equal(h.posts().length,0); assert.equal(h.confirmations,0);
    h.media.matches=true; h.w.testAPI.syncSpectatorRetentionControls();
    assert([...h.w.document.querySelectorAll('.spectator-retention-controls button')].every(b=>!b.disabled));
    h.media.matches=false; h.setup('mine'); assert.equal(h.button('TEST0000').disabled,false);
    h.dom.window.close();
  }
  console.log('PASS: terminal-only, cancel, duplicate/preserve lock, same-tab refresh, perspective, failure, no open bubbling, mobile hidden/inert, own mobile controls');
})().catch(error=>{ console.error(error); process.exitCode=1; });
