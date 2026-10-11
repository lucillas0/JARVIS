const fs = require('fs');
const root = 'C:/jarvis-handoff/JARVIS/';
function update(rel, edits) {
  const path = root + rel;
  let text = fs.readFileSync(path, 'utf8');
  const crlf = text.includes('\r\n');
  text = text.replace(/\r\n/g, '\n');
  for (const [before, after, label] of edits) {
    const count = text.split(before).length - 1;
    if (count !== 1) throw new Error(`${rel} ${label}: coincidencias ${count}`);
    text = text.replace(before, after);
  }
  if (crlf) text = text.replace(/\n/g, '\r\n');
  fs.writeFileSync(path, text);
}
update('app/ui/notch/notch.js', [
  [`  let chatPetTimer = null;\n  const CHAT_STUCK_MS = 20000;`, `  let chatPetTimer = null;\n  let responseActive = false;\n  const CHAT_STUCK_MS = 45000;`, 'vigencia de chat'],
  [`  const HOME_STUCK_MS = 20000;`, `  const HOME_STUCK_MS = 45000;`, 'vigencia de panel'],
  [`      if (data.type === 'assistant_reply') {\n        if (data.text) say('ai', String(data.text));`,
   `      if (data.type === 'assistant_reply') {\n        if (data.text) say('ai', String(data.text));\n        if (data.progress) {\n          responseActive = true;\n          if (!pendingApproval) {\n            home('working', true);\n            chatPet('working', true);\n            $('#chatStatus').textContent = 'Sigo trabajando…';\n          }\n          $('#taskLine').textContent = String(data.text || 'Tarea en curso').slice(0, 180);\n          return;\n        }\n        responseActive = false;`, 'proteger acuses intermedios'],
  [`      } else if (data.type === 'conversation_state') {\n        if (data.state === 'thinking' || data.state === 'speaking') {`,
   `      } else if (data.type === 'user_message_complete') {\n        responseActive = false;\n        if (!pendingApproval && HOME_BUSY.has($('#petHome').dataset.state)) {\n          home('error');\n          $('#status').textContent = 'JARVIS terminó sin responder';\n          setTimeout(() => { if ($('#petHome').dataset.state === 'error') home('idle'); }, 3200);\n        }\n        if (CHAT_BUSY.has($('#petChat').dataset.state)) {\n          chatPet('error');\n          $('#chatStatus').textContent = 'JARVIS terminó sin responder';\n        }\n      } else if (data.type === 'conversation_state') {\n        if (data.state === 'thinking' || data.state === 'speaking') {`, 'gestionar fin de petición'],
  [`        } else if (data.state === 'listening') {\n          if (!pendingApproval) {`,
   `        } else if (data.state === 'listening') {\n          if (responseActive) {\n            $('#chatStatus').textContent = 'Sigo trabajando…';\n            return;\n          }\n          if (!pendingApproval) {`, 'proteger tarea del estado de audio'],
  [`      } else if (data.type === 'task_step') {\n        const taskState`, `      } else if (data.type === 'task_step') {\n        responseActive = true;\n        const taskState`, 'pasos de tarea'],
  [`      } else if (data.type === 'action_status') {\n        const label`, `      } else if (data.type === 'action_status') {\n        if (data.state !== 'done' && data.state !== 'failed' && data.state !== 'cancelled') responseActive = true;\n        const label`, 'estados de acción'],
  [`      } else if (data.type === 'user_heard') {\n        home('surprised');`, `      } else if (data.type === 'user_heard') {\n        responseActive = true;\n        home('surprised');`, 'inicio de solicitud'],
  [`      } else if (data.type === 'confirmation_request') {\n        noteAgent`, `      } else if (data.type === 'confirmation_request') {\n        responseActive = false;\n        noteAgent`, 'permiso corta tarea activa'],
  [`      } else if (data.type === 'heal_approval_request') {\n        pendingApproval`, `      } else if (data.type === 'heal_approval_request') {\n        responseActive = false;\n        pendingApproval`, 'permiso heal corta tarea activa'],
]);
update('brain/main.py', [
  [`    async def _say(text, voice_text=None):\n        await brain._broadcast({"type": "assistant_reply", "text": text})`,
   `    async def _say(text, voice_text=None):\n        await brain._broadcast({"type": "assistant_reply", "text": text, "progress": True})`, 'marcar acuse como progreso'],
  [`    finally:\n        await brain._broadcast({"type": "conversation_state", "state": "idle"})\n\ndef _providers_str()`,
   `    finally:\n        await brain._broadcast({"type": "user_message_complete"})\n        await brain._broadcast({"type": "conversation_state", "state": "idle"})\n\ndef _providers_str()`, 'fin explícito de petición'],
]);
console.log('Actualizado notch y protocolo de fin de petición/progreso.');
