const http = require('http');
const { CdpSocket } = require('./cdp-websocket');
(async () => {
const targets = await new Promise((resolve, reject) => {
  http.get('http://127.0.0.1:9222/json', (response) => {
    let body = '';
    response.on('data', (chunk) => body += chunk);
    response.on('end', () => resolve(JSON.parse(body)));
  }).on('error', reject);
});
const target = targets.find((item) => item.title.includes('Notch'));
if (!target) throw new Error('No se encontró el renderer del notch');
const ws = new CdpSocket(target.webSocketDebuggerUrl);
await ws.connect();
function evaluate(expression) {
  return ws.send('Runtime.evaluate', { expression, returnByValue: true });
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
try {
  const visual = await evaluate(`(() => {
    const pets = [...document.querySelectorAll('.pet')];
    const states = ['idle','working','thinking','searching','approval','question','error','finished','rate_limit','sleep','dizzy','greeting','love','surprised','proud','wink','yawn','annoyed','upload','dance'];
    const accessories = ['beanie','santahat','partyhat','crown','witchhat','sunglasses','glasses','scarf','pumpkin','bow'];
    const samples = [];
    const probe = document.createElement('div');
    probe.className = 'pet';
    probe.style.setProperty('--s','132px');
    probe.innerHTML = '<div class="body"><div class="face"><div class="eyes"><i class="eye"></i><i class="eye"></i></div></div></div><i class="hat"></i><b class="badge"><u></u><u></u><u></u></b><i class="mark"></i>';
    document.body.append(probe);
    for (const acc of accessories) for (const state of states) {
      probe.dataset.acc = acc;
      probe.dataset.state = state;
      const badge = getComputedStyle(probe.querySelector('.badge'));
      const eye = getComputedStyle(probe.querySelector('.eye'));
      if (state === 'approval' && !badge.content.includes('!')) samples.push('approval lacks ! with '+acc);
      if (state === 'question' && !badge.content.includes('?')) samples.push('question lacks ? with '+acc);
      if (state === 'working' && probe.querySelectorAll('.badge u').length !== 3) samples.push('working lacks ellipsis with '+acc);
      if (state === 'finished' && eye.opacity !== '0') samples.push('finished eyes visible with '+acc);
      if (state === 'error' && eye.height === '13.8594px') samples.push('error eyes round with '+acc);
    }
    const inventory = {petCount:pets.length,states:states.length,accessories:accessories.length,accs:[...new Set(pets.map(p=>p.dataset.acc))],samples};
    probe.remove();
    return inventory;
  })()`);
  console.log('VISUAL_INVENTORY '+JSON.stringify(visual));
  const behavior = await evaluate(`(() => {
    const s=window.__jarvisWatchTest={events:[],tasks:[]};
    const originals={timeout:window.setTimeout, clearTimeout:window.clearTimeout};
    s.originals=originals;
    s.fakeTimers=new Map();s.now=0;s.next=1;
    window.setTimeout=(fn,ms=0,...args)=>{const id=s.next++;s.fakeTimers.set(id,{at:s.now+Number(ms),fn:()=>fn(...args)});return id};
    window.clearTimeout=(id)=>s.fakeTimers.delete(id);
    s.tick=(ms)=>{const end=s.now+ms;while(true){let hit=null;for(const [id,t] of s.fakeTimers)if(t.at<=end&&(!hit||t.at<hit[1].at))hit=[id,t];if(!hit)break;s.fakeTimers.delete(hit[0]);s.now=hit[1].at;hit[1].fn()}s.now=end};
    const originalSend=window.WebSocket.prototype.send;
    window.WebSocket.prototype.send=function(raw){try{s.events.push(JSON.parse(raw))}catch{};return originalSend.call(this,raw)};
    s.restore=()=>{window.setTimeout=originals.timeout;window.clearTimeout=originals.clearTimeout;window.WebSocket.prototype.send=originalSend};
    s.run=()=>{
      const send=(data)=>socket.onmessage({data:JSON.stringify(data)});
      send({type:'conversation_state',state:'speaking'});
      const t1={home:document.querySelector('#petHome').dataset.state,chat:document.querySelector('#petChat').dataset.state,status:document.querySelector('#chatStatus').textContent};
      send({type:'conversation_state',state:'speaking'});
      s.tick(19999);
      const before={home:document.querySelector('#petHome').dataset.state,chat:document.querySelector('#petChat').dataset.state};
      s.tick(2);
      const after={home:document.querySelector('#petHome').dataset.state,chat:document.querySelector('#petChat').dataset.state,status:document.querySelector('#status').textContent,chatStatus:document.querySelector('#chatStatus').textContent};
      send({type:'conversation_state',state:'speaking'});
      const afterLate={home:document.querySelector('#petHome').dataset.state,chat:document.querySelector('#petChat').dataset.state};
      send({type:'conversation_state',state:'listening'});
      const listening={home:document.querySelector('#petHome').dataset.state,chat:document.querySelector('#petChat').dataset.state};
      send({type:'conversation_state',state:'thinking'});
      send({type:'assistant_reply',text:''});
      const reply={home:document.querySelector('#petHome').dataset.state,chat:document.querySelector('#petChat').dataset.state,chatStatus:document.querySelector('#chatStatus').textContent};
      return {t1,before,after,afterLate,listening,reply,mainMinis:[...document.querySelectorAll('#petMini,#mini1,#mini2,#mini3,#mini4')].map(p=>p.dataset.state)};
    };
    return 'ready';
  })()`);
  console.log('WATCHDOG_SETUP '+behavior);
  await sleep(150);
  const result = await evaluate(`window.__jarvisWatchTest.run()`);
  console.log('WATCHDOG_TEST '+JSON.stringify(result));
  const restored = await evaluate(`(() => {window.__jarvisWatchTest.restore();return 'restored'})()`);
  console.log('RESTORE '+restored);
} finally {
  ws.close();
}
})().catch((error) => { console.error(error); process.exitCode = 1; });
