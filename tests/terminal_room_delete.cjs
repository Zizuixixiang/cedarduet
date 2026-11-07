// DOM/interaction regression only; this does not measure browser layout.
// NODE_PATH=/opt/cedartoy/node_modules node tests/terminal_room_delete.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('app/static/app.js', 'utf8').split('\nensureInitialRouteState();')[0];
const html = fs.readFileSync('app/static/index.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function harness(desktop = true) {
  const dom = new JSDOM(html, {url:'https://offline.invalid/', runScripts:'outside-only'});
  const w = dom.window, calls = [], media = {matches:desktop, addEventListener(){}};
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
    calls.push({path, url, options});
    let data = {}, status = 200;
    if (path.endsWith('/delete')) {
      await new Promise(resolve => { release = resolve; });
      if (failure) { status = 409; data = {message:'测试：当前对局不能删除'}; }
      else {
        records = records.filter(r => !path.includes(r.room_id));
        data = {message:'已删除合成对局'};
      }
    } else if (path.endsWith('/retention')) {
      await new Promise(resolve => { release = resolve; });
      if (failure) { status = 403; data = {message:'测试：绑定已失效'}; }
      else {
        const record = records.find(r => path.includes(r.room_id));
        record.preserved = JSON.parse(options.body).preserved;
        data = {message:'已更新留存', room:{...record, spectating:true,
          viewer:{role:'ai', player_id:new URL(url,w.location.href).searchParams.get('perspective') || '101'}}};
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
    showRoom(data) { room=data; renderRetention(room); },
    // Isolate the retention response from unrelated board renderers.
    stubRoomRenderer() { renderGame = data => { room=data; renderRetention(room); }; },
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
  for (const desktop of [true,false]) for (const scope of ['mine','machines']) {
    const h = harness(desktop); h.setup(scope);
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
    const h=harness(false); h.setup('machines'); h.setFailure(true);
    const original=h.button('TEST0000'); original.click(); h.release(); await tick();
    assert.equal(h.button('TEST0000'),original, 'failed request keeps the existing card');
    assert.equal(original.disabled,false);
    assert.equal(h.w.testAPI.scope,'machines');
    assert(!h.calls.some(c=>c.path==='/api/whoami'||c.path==='/api/spectating/rooms'));
    assert(h.w.document.body.textContent.includes('测试：当前对局不能删除'));
    h.dom.window.close();
  }
  {
    const h=harness(false); h.setup('machines');
    for(const controls of h.w.document.querySelectorAll('.spectator-retention-controls')) {
      assert.notEqual(controls.getAttribute('aria-hidden'),'true'); assert.notEqual(controls.inert,true);
      assert([...controls.querySelectorAll('button')].every(b=>!b.disabled));
    }
    h.w.testAPI.setPerspective();
    const preserve = () => h.w.document.querySelector('[data-retention-room-id="TEST0000"]');
    for (const value of [true,false]) {
      assert.equal(preserve().textContent,value?'保留':'取消保留');
      const previousPosts=h.posts().length;
      preserve().click(); preserve().click(); h.button('TEST0000').click();
      assert.equal(h.posts().length,previousPosts+1);
      assert.equal(h.confirmations,0);
      assert.equal(h.button('TEST0000').disabled,true);
      assert.equal(JSON.parse(h.posts().at(-1).options.body).preserved,value);
      assert.match(h.posts().at(-1).url,/perspective=102$/);
      h.release(); await tick();
      assert.equal(preserve().disabled,false);
      assert.equal(preserve().textContent,value?'取消保留':'保留');
      assert.match(preserve().parentNode.querySelector('.room-retention').textContent,
        value?/已保留/:/天后自动删除/);
      assert.equal(h.w.testAPI.scope,'machines');
      assert.equal(h.w.testAPI.perspective,'102');
    }
    h.setFailure(true); preserve().click(); h.release(); await tick();
    assert.equal(preserve().textContent,'保留');
    assert.equal(preserve().disabled,false);
    assert(h.w.document.body.textContent.includes('测试：绑定已失效'));
    h.setFailure(false);
    h.w.testAPI.stubRoomRenderer();
    for (const fixture of h.fixtures) {
      h.w.testAPI.showRoom({...fixture,spectating:true,viewer:{role:'ai',player_id:'102'}});
      const terminal=['finished','archived'].includes(fixture.status);
      const toggle=h.w.document.querySelector('#togglePreserveButton');
      assert.equal(toggle.disabled,!terminal);
      assert.equal(toggle.getAttribute('aria-hidden'),String(!terminal));
      if (terminal) {
        const previousPosts=h.posts().length;
        toggle.click(); toggle.click();
        assert.equal(h.posts().length,previousPosts+1);
        assert.equal(toggle.disabled,true);
        assert.match(h.posts().at(-1).url,/perspective=102$/);
        h.release(); await tick();
        assert.equal(toggle.disabled,false);
        assert.equal(toggle.textContent,fixture.preserved?'取消保留':'保留此对局');
      } else {
        const previousPosts=h.posts().length;
        assert.equal(await h.w.testAPI.updateRoomPreservation(fixture.room_id,true),false);
        assert.equal(h.posts().length,previousPosts);
      }
    }
    h.dom.window.close();
  }
  console.log('PASS: desktop/touch terminal-only controls, cancel, duplicate/preserve lock, same-tab refresh, perspective, failure, preserve/unpreserve, room retention, no open bubbling');
})().catch(error=>{ console.error(error); process.exitCode=1; });
