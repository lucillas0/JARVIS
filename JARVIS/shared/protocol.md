# Protocolo WebSocket app ↔ brain (sección 12.2)
# Puerto 8000, ruta /ws. Mensajes JSON.

# App → Brain
# {"type":"user_message","message":"abre spotify","input_mode":"voice","history":[...]}
# {"type":"confirmation_response","action_id":"a1b2c3","approved":true}
# {"type":"voice_activate"}                    # activar wake word manualmente
# {"type":"ping"}

# Brain → App
# {"type":"hello","message":"Sistemas en línea..."}
# {"type":"assistant_reply","text":"..."}
# {"type":"action_status","label":"Abriendo Spotify","state":"running"|"done"|"failed"|"cancelled"}
# {"type":"confirmation_request","action_id":"...","description":"...","timeout_seconds":30}
# {"type":"confirmation_resolved","action_id":"...","ok":true}
# {"type":"speech_start"} / {"type":"speech_end","ok":true}
# {"type":"audio_level","level":0.42}          # mover línea de voz
# {"type":"conversation_state","state":"idle"|"listening"|"thinking"|"speaking"}
# {"type":"voice_result","text":"..."}
# {"type":"screen_status","state":"continuous"|"off"}
# {"type":"music_start","track":"wake-up-theme"}
# {"type":"context_update","context":"coding"}
# {"type":"proactive_notice","context":"meeting","message":"..."}