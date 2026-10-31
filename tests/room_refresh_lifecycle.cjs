// Deterministic lifecycle tests against the production functions. No browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
function functionSource(name) {
  const match = new RegExp('(?:async\\s+)?function\\s+' + name + '\\(').exec(source);
  assert(match, name);
  const rest = source.slice(match.index + match[0].length);
  const next = /\n(?:async\s+)?function\s+/.exec(rest);
  return source.slice(match.index, match.index + match[0].length + next.index);
}
const names = ['retryAfterDelay', 'roomRefreshDelay', 'deferRoomRetry', 'request',
  'refreshHumanUnreadState', 'loadIdentity', 'scheduleRoomOpen', 'openRoom', 'backToLobby',
  'roomSyncIsCurrent', 'refreshRoom', 'roomIsVisible', 'shouldPollRoom',
  'shouldLongPollRoom', 'scheduleRoomPoll', 'pollRoom', 'startRoomPolling', 'stopPolling',
  'suspendRoomRefresh', 'resumeRoomRefresh'];
function harness() {
  let now = 1700000000000, sequence = 0;
  const timers = new Map(), calls = [], renders = [], elements = new Map();
  const context = vm.createContext({console, AbortController, Date: class extends Date {static now(){return now;}},
    document: {hidden:false}, window:{history:{state:null,back(){context.backCalls++;}}}, backCalls:0,
    setTimeout(fn,delay){const id=++sequence;timers.set(id,{fn,at:now+delay,delay});return id;},
    clearTimeout(id){timers.delete(id);},
    fetch(path,options){return new Promise((resolve,reject)=>{
      const call={path,options,resolve,reject,aborted:false,ignoreAbort:false};calls.push(call);
      options.signal?.addEventListener('abort',()=>{call.aborted=true;if(!call.ignoreAbort)reject(Object.assign(new Error('aborted'),{name:'AbortError'}));});
    });},
    $(id){if(!elements.has(id))elements.set(id,{value:'draft chat',dataset:{},classList:{hidden:id!=='gameView',contains(name){return name==='hidden'&&this.hidden;}},textContent:''});return elements.get(id);},
    apiPath:p=>p, applyHumanUnreadState:()=>{}, showNotice:()=>{}, toast:()=>{},
    isTerminal:r=>['finished','archived'].includes(r?.status), viewerPlayerIdFor:r=>r.viewer?.player_id||'human',
    showRoomTransitionFeedback:async()=>{}, ackHumanNotifications:async()=>{},
    hideWaitModeModal:()=>{},closeHistory:()=>{},closeNotificationModal:()=>{},writeRoomRoute:()=>{},
    renderUnreadBadges:()=>{},loadSpectatorRooms:async()=>{},
    showView(id){context.$('gameView').classList.hidden=id!=='gameView';},
    loadCatalogGameRenderers:async()=>{},syncUnreadStateToIdentity:()=>{},renderHumanChipBalance:()=>{},
    syncGameTypeOptions:()=>{},syncMachinePicker:()=>{},renderSelectedStakeHint:()=>{},
    partitionLobbyPendingRooms:()=>({outgoing:[],remainingRooms:[]}),renderPendingInvitations:()=>{},renderRooms:()=>{},
    renders,
  });
  vm.runInContext(`
    let identity={bound:true},room=null,pollTimer=null,roomSyncGeneration=0,roomRequestController=null;
    let roomOpenIntent=null,roomRefreshSuspended=false,roomLeaving=false,roomWriteRequests=0,readRetryAt=0,rateLimitFailures=0;
    let roomRetryAt=0,roomRefreshFailures=0,unreadRefreshInFlight=false,currentTimeline=[];
    let notificationHistory=[],notificationRefreshRequest=0,latestUnreadRevision=-1,notificationHistoryError='';
    const automaticNotificationIds=new Set(),deferredUnreadAcks=new Set();
    let selectedJungleCell='A1',selectedXiangqiCell='B2',pendingMove={card:7},liarsBidDraft={count:2},roomListScope='mine';
    const ROOM_POLL_RETRY_MS=3000;
    const canHumanMove=()=>room?.turn==='human'&&!room.spectating;
    function renderGame(next,message,timeline){room=next;currentTimeline=timeline;renders.push(next);showView('gameView');}
    ${names.map(functionSource).join('\n')}
  `,context);
  const run=code=>vm.runInContext(code,context);
  async function flush(){for(let i=0;i<12;i++)await Promise.resolve();}
  async function advance(ms=0){now+=ms;const due=[...timers].filter(([,t])=>t.at<=now);for(const [id,t]of due){if(timers.delete(id))t.fn();}await flush();}
  function reply(index,{status=200,data=null,retryAfter=null,invalidJSON=false}={}){
    calls[index].resolve({ok:status>=200&&status<300,status,headers:{get:()=>retryAfter},json:async()=>{if(invalidJSON)throw new SyntaxError('HTML');return data||{};}});
  }
  function current(turn='ai',spectating=false){run(`room={room_id:'ROOM1',revision:1,status:'playing',turn:${JSON.stringify(turn)},spectating:${spectating},viewer:{player_id:'AI-2'}};`);}
  const roomData=(id='ROOM1',revision=1,turn='ai',spectating=false,perspective='AI-2')=>({room:{room_id:id,revision,status:'playing',turn,spectating,viewer:{player_id:perspective}},timeline:[]});
  return {context,run,calls,renders,timers,advance,flush,reply,current,roomData};
}
(async()=>{
  for(const [turn,spectating]of [['ai',false],['human',false],['ai',true]]){
    const h=harness();h.current(turn,spectating);
    h.run('startRoomPolling();startRoomPolling();');assert.equal(h.timers.size,1);
    await h.advance(turn==='human'?3000:0);assert.equal(h.calls.length,1);
    assert.equal(h.calls[0].path.includes('wait=true'),turn==='ai'&&!spectating);
    if(spectating)assert(h.calls[0].path.includes('perspective=AI-2'));
    h.context.document.hidden=true;h.run('suspendRoomRefresh();');assert(h.calls[0].aborted);await h.flush();
    await h.advance(60000);h.run('startRoomPolling();');assert.equal(h.calls.length,1);assert.equal(h.timers.size,0);
    h.context.document.hidden=false;h.run('resumeRoomRefresh();resumeRoomRefresh();');await h.advance();
    assert.equal(h.calls.filter(c=>c.path.startsWith('/api/rooms/')).length,2);
    assert.equal(h.calls.filter(c=>c.path==='/api/notifications/unread').length,1);
    const i=h.calls.findIndex((c,n)=>n>0&&c.path.startsWith('/api/rooms/'));
    h.reply(i,{data:h.roomData('ROOM1',2,turn,spectating)});await h.flush();
    assert.equal(h.renders.length,1);assert.equal(h.run('room.revision'),2);assert.equal(h.timers.size,1);
    h.run('stopPolling();');
  }
  console.log('PASS waiting / own turn / spectator: hidden abort, resume one chain, current updates');
  {
    const h=harness();h.current();h.run('startRoomPolling();');await h.advance();h.calls[0].ignoreAbort=true;
    const opening=h.run("openRoom('ROOM2',{perspective:'AI-9'})");assert(h.calls[0].aborted);
    h.reply(0,{data:h.roomData('ROOM1',9)});await h.flush();assert.equal(h.renders.length,0);
    h.reply(1,{data:h.roomData('ROOM2',1,'ai',true,'AI-9')});await opening;assert.equal(h.run('room.room_id'),'ROOM2');
    assert(h.calls[1].path.includes('perspective=AI-9'));
    h.context.window.history.state={duelView:'room',duelBackToLobby:true};
    await h.run('backToLobby()');h.run('resumeRoomRefresh();');await h.advance();
    assert.equal(h.context.backCalls,1);assert.equal(h.calls.filter(c=>c.path.startsWith('/api/rooms/')).length,2);
    await h.run('backToLobby({fromHistory:true})'); // spectator lobby needs no whoami mock
    assert.equal(h.run('room'),null);assert.equal(h.timers.size,0);
  }
  {
    const h=harness();h.current();const opening=h.run("openRoom('ROOM2',{perspective:'AI-9',routeRestore:true,historyMode:'none'})");
    h.calls[0].ignoreAbort=true;h.context.document.hidden=true;h.run('suspendRoomRefresh();');
    h.reply(0,{data:h.roomData('ROOM2',1,'ai',true,'AI-9')});await opening;assert.equal(h.renders.length,0);
    h.context.document.hidden=false;h.run('resumeRoomRefresh();resumeRoomRefresh();');await h.advance();
    const calls=h.calls.filter(c=>c.path.startsWith('/api/rooms/'));assert.equal(calls.length,2);assert.equal(calls[0].path,calls[1].path);
    const i=h.calls.indexOf(calls[1]);h.reply(i,{data:h.roomData('ROOM2',1,'ai',true,'AI-9')});await h.flush();assert.equal(h.renders.length,1);
    h.run('stopPolling();');
  }
  {
    const h=harness();h.context.document.hidden=true;await h.run("openRoom('ROOM3',{historyMode:'none',routeRestore:true})");assert.equal(h.calls.length,0);
    const leaving=h.run('backToLobby({fromHistory:true})');h.reply(0,{data:{bound:true,games:[],wallet:{balance:0},machines:[]}});await leaving;
    h.context.document.hidden=false;h.run('resumeRoomRefresh();');await h.advance();assert(!h.calls.some(c=>c.path.startsWith('/api/rooms/')));
  }
  {
    const h=harness();h.current('ai',true);h.run('startRoomPolling();');await h.advance();h.calls[0].ignoreAbort=true;
    await h.run('backToLobby({fromHistory:true})');h.reply(0,{data:h.roomData('ROOM1',99,'ai',true)});await h.flush();
    assert.equal(h.renders.length,0);assert.equal(h.run('room'),null);assert.equal(h.timers.size,0);
  }
  {
    const h=harness();const opening=h.run("openRoom('ROOM2',{perspective:'AI-9',historyMode:'none',routeRestore:true})");
    h.reply(0,{status:429,retryAfter:'9'});await opening;assert.equal(h.timers.size,1);assert.equal(h.run('room'),null);
    h.run('resumeRoomRefresh();resumeRoomRefresh();');await h.advance(8999);assert.equal(h.calls.length,1);
    await h.advance(1);assert.equal(h.calls.length,2);assert.equal(h.calls[0].path,h.calls[1].path);
    h.reply(1,{data:h.roomData('ROOM2',1,'ai',true,'AI-9')});await h.flush();await h.advance();
    assert(h.calls[2].path.includes('perspective=AI-9'));h.run('stopPolling();');await h.flush();
  }
  console.log('PASS switch / back / hidden opening: stale responses discarded, pending target resumes or cancels');
  for(const header of ['12',new Date(1700000012000).toUTCString()]){
    const h=harness();h.current('human');h.run('startRoomPolling({immediate:true});');await h.advance();
    h.reply(0,{status:429,retryAfter:header,invalidJSON:true});await h.flush();assert.equal(h.run('roomRefreshDelay()'),12000);
    const draft=h.run('JSON.stringify([selectedJungleCell,selectedXiangqiCell,pendingMove,liarsBidDraft])');
    h.run('resumeRoomRefresh();resumeRoomRefresh();');await h.run('refreshRoom({quiet:true})');await h.run('loadIdentity({quiet:true})');
    assert.equal(h.calls.length,1);assert(h.run('identity'));assert.equal(h.run('room.room_id'),'ROOM1');assert.equal(h.timers.size,1);
    h.context.document.hidden=true;h.run('suspendRoomRefresh();');await h.advance(6000);
    h.context.document.hidden=false;h.run('resumeRoomRefresh();');await h.advance(5999);assert.equal(h.calls.length,1);
    await h.advance(1);assert.equal(h.calls.length,2);h.reply(1,{data:h.roomData('ROOM1',1,'human')});await h.flush();
    assert.equal(h.run('JSON.stringify([selectedJungleCell,selectedXiangqiCell,pendingMove,liarsBidDraft])'),draft);
    assert.equal(h.context.$('chatInput').value,'draft chat');assert.equal(h.renders.length,1);assert.equal(h.timers.size,1);
    h.run('stopPolling();');
  }
  {
    const h=harness();h.current();h.run('startRoomPolling();');await h.advance();h.reply(0,{status:429,retryAfter:'invalid'});await h.flush();
    assert.equal(h.run('roomRefreshDelay()'),3000);await h.advance(3000);h.reply(1,{status:429});await h.flush();assert.equal(h.run('roomRefreshDelay()'),6000);
    h.run('stopPolling();');
  }
  for(const status of [401,403]){
    const h=harness();h.current('ai',true);h.run('startRoomPolling();');await h.advance();h.reply(0,{status});await h.flush();
    assert.equal(h.run('room'),null);assert.equal(h.run('readRetryAt'),0);assert.equal(h.timers.size,0);
  }
  {
    const h=harness();h.run('resumeRoomRefresh();resumeRoomRefresh();');await h.advance(60000);
    assert(h.calls.every(c=>c.path==='/api/notifications/unread'));assert.equal(h.calls.length,1);assert.equal(h.timers.size,0);
  }
  {
    const h=harness();h.current('human');
    const writing=h.run("request('/api/rooms/ROOM1/messages',{method:'POST',body:'{}'})");
    h.run('resumeRoomRefresh();');await h.advance();assert.equal(h.calls.filter(c=>c.path==='/api/rooms/ROOM1').length,0);
    h.reply(0,{data:{message:'ok'}});await writing;await h.advance(3000);assert.equal(h.calls.filter(c=>c.path==='/api/rooms/ROOM1').length,1);
    h.run('stopPolling();');await h.flush();
  }
  {
    const h=harness();h.current();h.run('startRoomPolling();');await h.advance();h.run('room.revision=3;');
    h.reply(0,{data:h.roomData('ROOM1',2)});await h.flush();assert.equal(h.renders.length,0);assert.equal(h.run('room.revision'),3);h.run('stopPolling();');
  }
  {
    const h=harness();h.current();let finish;
    h.context.showRoomTransitionFeedback=()=>new Promise(resolve=>{finish=resolve;});
    h.run('startRoomPolling();');await h.advance();h.reply(0,{data:h.roomData('ROOM1',2)});await h.flush();assert(finish);
    h.context.document.hidden=true;h.run('suspendRoomRefresh();');finish();await h.flush();
    assert.equal(h.renders.length,0);assert.equal(h.timers.size,0);
  }
  console.log('PASS pending writes are not raced by resume; lower-revision responses cannot roll back the board');
  console.log('PASS Retry-After seconds/date/HTML/fallback; cooldown survives entry points; auth distinct; lobby no room polling');
  assert(!source.includes('window.setInterval(() => {\n  if (room'));
  assert(source.includes('window.addEventListener("pagehide", suspendRoomRefresh)'));
})().catch(error=>{console.error(error);process.exitCode=1;});
