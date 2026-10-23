/* Render viewer-projected, read-only room snapshots. No live network or moves.
   FOUR_COLOR_REAL_DATA contains API-shaped JSON from the server's public/private
   projector; raw saved states and other players' private cards are never loaded. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(__dirname,'..');
const out=process.env.FOUR_COLOR_ARTIFACTS;
assert.ok(out && process.env.FOUR_COLOR_REAL_DATA,'explicit snapshot and artifact directories required');
fs.mkdirSync(out,{recursive:true});
async function completeFaces(page) {
 const failures=await page.locator('.fc-seat-public, .fc-discard-history').evaluateAll(async ports=>{
  const bad=[];
  for(const port of ports) {
   const initial=[port.scrollLeft,port.scrollTop];
   for(const face of port.querySelectorAll('.fc-card')) {
    const slot=face.closest('.fc-side-card, .fc-history-card')||face;
    let x=slot.offsetLeft,y=slot.offsetTop,parent=slot.offsetParent;
    while(parent && parent!==port) {x+=parent.offsetLeft;y+=parent.offsetTop;parent=parent.offsetParent;}
    if(!parent) {
     const r=slot.getBoundingClientRect(),p=port.getBoundingClientRect();
     x=r.left-p.left+port.scrollLeft;y=r.top-p.top+port.scrollTop;
    }
    const w=slot.offsetWidth,h=slot.offsetHeight;
    port.scrollLeft=Math.max(0,x+w-port.clientWidth+1);
    port.scrollTop=Math.max(0,y+h-port.clientHeight+1);
    await new Promise(requestAnimationFrame);
    if(x<port.scrollLeft-.5 || x+w>port.scrollLeft+port.clientWidth+.5 || y<port.scrollTop-.5 || y+h>port.scrollTop+port.clientHeight+.5)
     bad.push({id:face.dataset.cardId,x,y,w,h,width:port.clientWidth,height:port.clientHeight,left:port.scrollLeft,top:port.scrollTop});
   }
   port.scrollLeft=initial[0];port.scrollTop=initial[1];
  }
  return bad;
 });
 assert.deepEqual(failures,[],'every full border, including the last card, fits after local scrolling');
}
async function checkBadge(page) {
 if(await page.locator('#dismissWaitModeModalButton').isVisible())await page.locator('#dismissWaitModeModalButton').click();
 assert.equal(await page.locator('.fc-turn-indicator').count(),1);
 const badge=page.locator('.fc-seat-counts > .fc-turn-indicator');
 await badge.scrollIntoViewIfNeeded();
 const geometry=await badge.evaluate(n=>{
  const r=n.getBoundingClientRect(),c=n.parentElement.getBoundingClientRect();
  const count=n.parentElement.querySelector('.fc-seat-count').getBoundingClientRect();
  return {position:n.closest('.fc-seat').dataset.position,rect:r.toJSON(),counts:c.toJSON(),aligned:Math.abs(r.y+r.height/2-count.y-count.height/2)<=1,
   hits:[[2,2],[r.width-2,2],[2,r.height-2],[r.width-2,r.height-2]].map(([x,y])=>{const hit=document.elementFromPoint(r.x+x,r.y+y);return {visible:n.contains(hit),hit:hit?.className};})};
 });
 assert.ok(geometry.aligned && geometry.rect.left>=geometry.counts.left-1 && geometry.rect.right<=geometry.counts.right+1 && geometry.hits.every(h=>h.visible),`entire badge fits on the dark-card count row and is unobscured: ${JSON.stringify(geometry)}`);
 await page.evaluate(()=>scrollTo(0,0));
}
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const evidence=[];
 try {
  for(const rid of (process.env.FOUR_COLOR_REAL_ROOMS || '8K9L32Y2,H3BJRL3F,BBXFBME2').split(',')) {
   const snapshot=JSON.parse(fs.readFileSync(path.join(process.env.FOUR_COLOR_REAL_DATA,`${rid}.json`)));
   for(const width of (process.env.FOUR_COLOR_REAL_WIDTHS || '360,430,1280').split(',').map(Number)) {
    const page=await browser.newPage({viewport:{width,height:width<400?800:900},hasTouch:width<500});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',async route=>{
     const req=route.request(),url=new URL(req.url()),p=url.pathname;
     if(url.hostname!=='four-color.test')return route.abort();
     assert.ok(!(req.method()==='POST' && /\/(move|resign|leave)$/.test(p)),'no game action may be submitted');
     if(p==='/' || p.startsWith('/static/')) {
      const rel=p==='/'?'index.html':p.slice(8);
      let file=path.join(root,'app/static',rel);
      if(process.env.FOUR_COLOR_BASELINE_DIR && /^games\/four_color_tenhu\.(js|css)$/.test(rel)) file=path.join(process.env.FOUR_COLOR_BASELINE_DIR,`baseline.${rel.split('.').at(-1)}`);
      if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({body:fs.readFileSync(file),contentType:rel.endsWith('.js')?'text/javascript':rel.endsWith('.css')?'text/css':'text/html'});
     }
     let data={ok:true,rooms:[],timeline:[],notifications:[]};
     if(p==='/api/whoami')data={...data,bound:true,human_player_id:snapshot.room.viewer.player_id,human_name:'南杉',identity_label:'南杉',machines:[],wallet:{balance:0},games:['tenhu','jiuzhi','qunhu'].map(m=>({game_type:`four_color_${m}`,display_name:`四色牌·${m}`,category:'card',allowed_player_counts:m==='tenhu'?[2,3,4]:m==='jiuzhi'?[4]:[3,4,5],recommended_players:4,supports_stakes:false}))};
     else if(p===`/api/rooms/${rid}`)data=snapshot;
     return route.fulfill({json:data});
    });
    await page.goto(`http://four-color.test/?room=${rid}`);
    await page.locator('.fc-table').waitFor();
    if(await page.locator('#dismissWaitModeModalButton').isVisible())await page.locator('#dismissWaitModeModalButton').click();
    await page.screenshot({path:path.join(out,`${rid}-${width}.png`),fullPage:true});
    const measure=await page.locator('.fc-table').evaluate(table=>{
     const t=table.getBoundingClientRect(),rect=n=>{if(!n)return null;const r=n.getBoundingClientRect();return {x:r.x-t.x,y:r.y-t.y,width:r.width,height:r.height};};
     return {neutral:rect(table.querySelector('.fc-discards')),neutralCards:rect(table.querySelector('.fc-neutral-pile .fc-discard-history')),south:rect(table.querySelector('.fc-history-south')),west:rect(table.querySelector('.fc-history-west')),
      histories:[...table.querySelectorAll('.fc-discard-history')].map(n=>({key:n.dataset.historyKey,axis:n.dataset.axis,rect:rect(n),scrollLeft:n.scrollLeft,scrollTop:n.scrollTop,scrollWidth:n.scrollWidth,scrollHeight:n.scrollHeight,cards:[...n.querySelectorAll('[data-card-id]')].map(c=>c.dataset.cardId),visibleHeads:[...n.querySelectorAll('.fc-card strong:first-child')].filter(h=>{const r=h.getBoundingClientRect();return h.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}).length}))};
    });
    evidence.push({rid,width,...measure,errors});
    fs.writeFileSync(path.join(out,'measurements.json'),JSON.stringify(evidence,null,2));
    if(!process.env.FOUR_COLOR_BASELINE_DIR) {
     assert.deepEqual(errors,[]);
     assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
     const s=snapshot.room.board_state;
     assert.deepEqual(measure.histories.flatMap(h=>h.cards).sort(),s.discards.filter(id=>id!==s.offer?.card).sort(),'all actual public discards render exactly once');
     for(const h of measure.histories)assert.ok(h.visibleHeads>0,`${rid}/${width}/${h.key}: history has visible cards before any scrolling`);
     assert.equal(await page.locator('.fc-neutral-pile > .fc-meta').count(),0,'no visible neutral titles');
     assert.equal(await page.locator('.fc-seat-counts > .fc-turn-indicator').count(),1,'one acting badge inside the counts row');
     for(const pile of await page.locator('.fc-discard-history').all()) {
      const owner=await pile.getAttribute('data-player-id');
      for(const id of await pile.locator('[data-card-id]').evaluateAll(ns=>ns.map(n=>n.dataset.cardId))) {
       if(owner)assert.equal(s.discard_sources[id],owner);
       else assert.ok(!s.participant_order.includes(s.discard_sources?.[id]));
      }
     }
     const publicIds=[...s.discards,...(s.offer?[s.offer.card]:[])];
     for(const pid of s.participant_order)publicIds.push(...(s.melds[pid]||[]).flatMap(g=>g.cards),...(s.broken?.[pid]||[]),...(s.listening?.[pid]||[]));
     assert.deepEqual((await page.locator('.fc-table [data-card-id]').evaluateAll(ns=>ns.map(n=>n.dataset.cardId))).sort(),[...new Set(publicIds)].sort(),'only projected public cards appear on the table');
     await completeFaces(page);
     await checkBadge(page);
     if(process.env.FOUR_COLOR_BEFORE_MEASUREMENTS && rid==='8K9L32Y2') {
      const before=JSON.parse(fs.readFileSync(process.env.FOUR_COLOR_BEFORE_MEASUREMENTS)).find(m=>m.rid===rid&&m.width===width);
      assert.equal(measure.neutralCards.x-before.neutralCards.x,-6);
      assert.equal(measure.neutralCards.y-before.neutralCards.y,10,'actual faces move down, including the removed heading');
      assert.ok(measure.neutralCards.width<before.neutralCards.width);
      assert.deepEqual(measure.south,before.south,'south player rail does not move');
      assert.ok(measure.neutralCards.x-measure.west.x-measure.west.width>=3);
     }
     // UI-only turn regression; the saved room snapshots remain unchanged.
     const actor=snapshot.room.current_player_id;
     for(const pid of s.participant_order) {
      snapshot.room.current_player_id=pid;
      await page.reload();await page.locator('.fc-table').waitFor();
      await checkBadge(page);
      assert.equal(await page.locator('.fc-seat-current').getAttribute('data-player-id'),pid);
     }
     snapshot.room.current_player_id=actor;
     // Legacy compatibility variant of the same public cards, not a real room.
     // No invented attribution when old states contain no discard_sources.
     if(rid==='H3BJRL3F' && width===360) {
      const sources=s.discard_sources;delete s.discard_sources;
      await page.reload();await page.locator('.fc-table').waitFor();
      if(await page.locator('#dismissWaitModeModalButton').isVisible())await page.locator('#dismissWaitModeModalButton').click();
      assert.equal(await page.locator('.fc-history').count(),0);
      assert.deepEqual((await page.locator('.fc-neutral-pile [data-card-id]').evaluateAll(ns=>ns.map(n=>n.dataset.cardId))).sort(),s.discards.slice().sort());
      await completeFaces(page);
      await page.screenshot({path:path.join(out,'compatibility-only-no-sources-360.png'),fullPage:true});
      s.discard_sources=sources;
     }
    }
    await page.close();
    console.log(JSON.stringify({rid,width,visible:measure.histories.map(h=>[h.key,h.visibleHeads]),errors}));
   }
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
