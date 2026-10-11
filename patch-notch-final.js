const fs = require('fs');
const sourcePath = 'C:/jarvis-handoff/JARVIS/app/ui/notch/notch.js';
let source = fs.readFileSync(sourcePath, 'utf8');
const sourceUsesCRLF = source.includes('\r\n');
source = source.replace(/\r\n/g, '\n');
function once(before, after, label) {
  const hits = source.split(before).length - 1;
  if (hits !== 1) throw new Error(`${label}: se esperaba 1 coincidencia, hay ${hits}`);
  source = source.replace(before, after);
}
source = source.replace(/^  const PET = .*;$/m,
  `  const PET = '<i class="glow"></i><i class="shadow"></i><div class="rig"><i class="hand l"></i><div class="body"><div class="face"><div class="eyes"><i class="eye"></i><i class="eye"></i></div></div></div><i class="hand r"></i></div><i class="doc"></i><b class="star s1">✦</b><b class="star s2">✧</b><b class="star s3">✦</b><i class="mark"></i><i class="hat"></i><i class="wear"></i><b class="badge"><u></u><u></u><u></u></b>';`);
once(`      } else if (data.type === 'assistant_reply') {`, `      if (data.type === 'assistant_reply') {`, 'reparar rama de respuesta');
once(`  function expireBusyState() {
    homeWatchExpired = true;
    chatWatchExpired = true;
    clearHomeWatch();
    clearChatWatch();
    if (HOME_BUSY.has($('#petHome').dataset.state)) home('idle');
    else for (const selector of ['#petMini', '#mini1', '#mini2', '#mini3', '#mini4']) pet(selector, 'idle');
    pet('#petChat', 'idle');
    $('#status').textContent = 'Sin respuesta del cerebro';
    $('#chatStatus').textContent = 'Sin respuesta del cerebro';
  }`,
`  function expireBusyState() {
    const homeBusy = HOME_BUSY.has($('#petHome').dataset.state);
    const chatBusy = CHAT_BUSY.has($('#petChat').dataset.state);
    if (homeBusy) homeWatchExpired = true;
    if (chatBusy) chatWatchExpired = true;
    clearHomeWatch();
    clearChatWatch();
    if (homeBusy) {
      home('idle');
      $('#status').textContent = 'Sin respuesta del cerebro';
    }
    if (chatBusy) {
      pet('#petChat', 'idle');
      $('#chatStatus').textContent = 'Sin respuesta del cerebro';
    }
  }`, 'aislar watchdog de chat y panel');
once(`        if (data.text) say('ai', String(data.text));
        chatPet('finished');
        noteAgent('working', data.text);
        $('#taskLine').textContent = String(data.text).slice(0, 180);
        $('#chatStatus').textContent = 'Listo';
        if (!pendingApproval) home('finished');`,
`        if (data.text) say('ai', String(data.text));
        chatPet('finished', true);
        noteAgent('finished', data.text);
        $('#taskLine').textContent = String(data.text || 'Respuesta completada').slice(0, 180);
        $('#chatStatus').textContent = 'Listo';
        if (!pendingApproval) home('finished', true);`, 'soltar busy al recibir respuesta');
once(`          if (pendingApproval || HOME_BUSY.has($('#petHome').dataset.state)) return;
          flourish([pick(['proud', 'dance', 'love', 'wink']), pick(['dance', 'proud', 'love'])]);`,
`          if (pendingApproval || $('#petHome').dataset.state !== 'finished') return;
          flourish([pick(['proud', 'dance', 'love', 'wink']), pick(['dance', 'proud', 'love'])]);`, 'proteger ráfaga de humor de tareas nuevas');
once(`      } else if (data.type === 'conversation_state') {
        if (data.state === 'thinking' || data.state === 'speaking') {
          const state = data.state === 'thinking' ? 'thinking' : 'working';
          chatPet(state);
          home(state);
          $('#chatStatus').textContent = data.state === 'thinking' ? 'Pensando…' : 'Respondiendo…';
        } else if (data.state === 'listening') {
          chatPet('idle');
          if (HOME_BUSY.has($('#petHome').dataset.state)) home('idle');
          $('#chatStatus').textContent = 'Escuchando…';
        }
      }`,
`      } else if (data.type === 'conversation_state') {
        if (data.state === 'thinking' || data.state === 'speaking') {
          // Heartbeats tardíos no pueden volver a dejar la mascota en "hablando".
          if (homeWatchExpired || chatWatchExpired) return;
          const state = data.state === 'thinking' ? 'thinking' : 'working';
          chatPet(state);
          home(state);
          $('#chatStatus').textContent = data.state === 'thinking' ? 'Pensando…' : 'Respondiendo…';
        } else if (data.state === 'listening') {
          if (!pendingApproval) {
            homeWatchExpired = false;
            chatWatchExpired = false;
            chatPet('idle');
            if (HOME_BUSY.has($('#petHome').dataset.state)) home('idle');
          }
          $('#chatStatus').textContent = 'Escuchando…';
        }
      }`, 'ignorar heartbeats obsoletos y limpiar escucha');
once(`        pendingApproval = { kind: 'action', id: String(data.action_id || '') };
        openIsland(true);`,
`        pendingApproval = { kind: 'action', id: String(data.action_id || '') };
        chatPet('approval');
        openIsland(true);`, 'sincronizar emoción de permiso en chat');
once(`        pendingApproval = { kind: 'heal', id: String(data.id || '') };
        openIsland(true);`,
`        pendingApproval = { kind: 'heal', id: String(data.id || '') };
        chatPet('approval');
        openIsland(true);`, 'sincronizar permiso heal en chat');
once(`        home(data.stage === 'fixed' ? 'finished' : 'working');
        $('#taskLine').textContent = String(data.note || data.error || 'Revisión automática').slice(0, 180);`,
`        const fixed = data.stage === 'fixed';
        home(fixed ? 'finished' : 'working', !fixed);
        $('#taskLine').textContent = String(data.note || data.error || 'Revisión automática').slice(0, 180);`, 'watchdog durante autocorrección');
if (sourceUsesCRLF) source = source.replace(/\n/g, '\r\n');
fs.writeFileSync(sourcePath, source);

const htmlPath = 'C:/jarvis-handoff/JARVIS/app/ui/notch/index.html';
let html = fs.readFileSync(htmlPath, 'utf8');
const usesCRLF = html.includes('\r\n');
html = html.replace(/\r\n/g, '\n');
function htmlOnce(before, after, label) {
  const hits = html.split(before).length - 1;
  if (hits !== 1) throw new Error(`${label}: se esperaba 1 coincidencia, hay ${hits}`);
  html = html.replace(before, after);
}
htmlOnce(`z-index:2;\n  opacity:0;transform:scale(.55);pointer-events:none;`,
  `z-index:5;\n  opacity:0;transform:scale(.55);pointer-events:none;`, 'badge sobre accesorios');
htmlOnce(`/* color por estado (también lo lee .info) */`, `.pet .doc,.pet .star,.pet .mark{z-index:4}\n\n/* color por estado (también lo lee .info) */`, 'orden de capas');
htmlOnce(`.pet[data-acc=sunglasses] .hat{left:16%;top:43%;width:68%;height:16%;background:#101216;\n  border-radius:.02em}`, `.pet[data-acc=sunglasses] .hat{left:16%;top:47%;width:68%;height:14%;background:transparent;\n  border-radius:.02em}`, 'gafas no tapan ojos');
htmlOnce(`position:absolute;top:8%;width:40%;height:70%;background:#1c2028;border-radius:.02em}`, `position:absolute;top:8%;width:40%;height:70%;background:rgba(16,20,28,.12);border:.012em solid #101216;border-radius:.08em}`, 'lentes translúcidas');
htmlOnce(`background:rgba(20,24,32,.22);border:.012em solid #101216}`, `background:rgba(20,24,32,.08);border:.012em solid #101216}`, 'ojos legibles con gafas');
if (usesCRLF) html = html.replace(/\n/g, '\r\n');
fs.writeFileSync(htmlPath, html);
console.log('Patched notch.js and index.html');
