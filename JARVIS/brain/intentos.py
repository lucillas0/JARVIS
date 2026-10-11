# -*- coding: utf-8 -*-
"""Lee el mensaje en el acto y decide qué hay que hacer, sin pensarlo.

Antes, cada mensaje passing por la misma puerta: o cabía en un atajo exacto,
o se le entregaba al modelo y había 3 segundos (o 30) de espera. Aquí el
mensaje se mira una vez, en microsegundos, y se clasifica:

  abrir    → se abre al instante (programa, web, máquina, página del ciclo)
  leer     → se responde al instante con apuntes, notas, cursos o un archivo
  local    → acción del equipo, inmediata (volumen, ventanas, capturas)
  analizar → hace falta pensar: va al modelo, pero con reloj y sin pasarse
  charla   → conversación normal

Solo esto: mirar. Ninguna acción aquí, ninguna red, ningún módulo pesado.
Las decisiones se toman y luego se ejecutan, para que el señor no espere ni
un segundo de más.
"""
from __future__ import annotations

import re
import time
import unicodedata

# Todo se compila una vez al importar: luego es question de milisegundos.
# Solo se quitan las palabras de cortesía, NO las que son respuesta en sí
# mismas: 'hola' o 'gracias' quitados dejan el mensaje vacío y se acaban
# tomando por una orden de abrir algo.
_VACIOS = ("por favor", "porfa", "oye", "a ver", "venga", "anda", "hey",
          "buenas", "ahora", "ya", "vale", "ok")


def _sin(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


RE_ABRIR = re.compile(
    r"\b(abre|abrir|abreme|abreme|lanza|lanzame|inicia|iniciar|arranca|"
    r"arrancar|ejecuta|corre|pon|ponme|ense(n|ñ)ame|abrir el|quiero ver|"
    r"quiero que abras|visita|entra en|ve a|accede a|descarga(me)?)\b")
RE_LEER = re.compile(
    r"\b(dime|diga|digame|muestrame|ensename|leheme|lee|leer|busca(me)?|"
    r"buscar|encuentra(me)?|qu(é|e) hay|que hay|qu(é|e) pone|que dice|"
    r"de que va|de que trata|contenido|apuntes|notas|asignaturas|cursos|"
    r"entregas|tareas|resumen(me)?|resume|resume(me)|listar|liste|"
    r"queFiles|cuantas(me)?|cuantos(me)?|cursos que|que cursos)\b")
RE_LOCAL = re.compile(
    r"\b(sube| bajar| pausa|pausa|continua|siguiente|anterior|silencio|"
    r"volumen|brillo|captura|captura(me)? pantalla|graba|ventana|"
    r"minimiza|maximiza|cambia de|bloquea|despierta|dormir|apagar|"
    r"reiniciar|copia|mueve|borra|renombra)\b")
RE_PENSAR = re.compile(
    r"\b(explica(me)?|explicame|explicas|por que|para que|como (?:se|puedo|"
    r"hago|funciona|puedo)|cmo |diferencia|compara(me)?|comparacion|"
    r"semejanza|analiza(me)?|analizar|analisis|estudia(me)?|investiga(me)?|"
    r"averigua|resuelve(me)?|ayudame a|recomiendame|sugerencia|plan|pasos|"
    r"opinion|crees|ventajas|desventajas|pros|contras|resumen del|"
    r"deberia|conviene|me recomiendas| opinion)\b")
RE_SALUDO = re.compile(
    r"^\s*(hola|buenas|hey|que tal|buenos dias|buenas tardes|buenas noches|"
    r"gracias|mil gracias|nada|adios|hasta luego|buenos)\b")
# Empieza así -> es pregunta, no orden. Aunque dentro haya un verbo de acción.
RE_INTERROGA = re.compile(
    r"^\s*(por que|para que|como|cuando|donde|quien|cual|cuales|que|"
    r"si se|cuantas|cuantos|hay alguna|se puede|se puede saber)\b")

# Palabras que delatan un tema de su coursework, aunque no diga "dime".
TEMAS_CAMPUS = re.compile(
    r"\b(dhcp|nftables|iptables|samba|sssd|dns|wireshark|tcpdump|lynis|pam|"
    r"nmap|apache|nginx|kerberos|ldap|firewall|router|red|redes|vlan|"
    r"subnetting|enrutamiento|segmentacion|seguridad informatica|"
    r"sistemas operativos|servicios en rede|criptografia|auditoria|"
    r"planificacion|respaldo|economia circular|aplicaciones web|"
    r"debian|mint|windows server|aula|aulas|tarea|tareas|practica|"
    r"examen|nota|notas|entrega|apuntes|examenes|ud[0-9]|tema)\b")


def limpiar(texto: str) -> str:
    t = (texto or "").strip().strip("?!.,¡¿'\"")
    for v in _VACIOS:
        t = re.sub(r"^\s*" + re.escape(v) + r"\b\s*", "", t, flags=re.I)
        t = re.sub(r"\s*" + re.escape(v) + r"\s*$", "", t, flags=re.I)
    return t.strip(" ?!.,")


def clasificar(texto: str) -> dict:
    """Mira el mensaje y dice qué hay que hacer. En microsegundos."""
    t0 = time.perf_counter()
    limpio = limpiar(texto)
    t = _sin(limpio)
    solo_hola = bool(RE_SALUDO.search(t)) and len(t) < 40
    palabras = t.split()

    # Una pregunta nunca es una orden. 'por que no me arranca la maquina'
    # lleva dentro un 'arranca' y, sin esta mirada, se tomaba por una
    # petición de encender la máquina.
    pregunta = bool(RE_INTERROGA.search(t))

    if solo_hola:
        tipo = "charla"
    # 'enséñame el tema de seguridad informática': lee, no abre.
    elif not pregunta and re.search(r"ensen|muestrame|leheme", t) and \
            (TEMAS_CAMPUS.search(t) or RE_LEER.search(t)):
        tipo = "analizar" if RE_PENSAR.search(t) else "leer"
    elif not pregunta and RE_ABRIR.search(t):
        tipo = "abrir"
    # Una o dos palabras sueltas: puede ser un programa, una web o una máquina.
    elif len(palabras) <= 2 and not RE_PENSAR.search(t) and \
            not RE_LEER.search(t) and not pregunta:
        tipo = "abrir"
    elif RE_LOCAL.search(t) and not pregunta:
        tipo = "local"
    elif RE_PENSAR.search(t):
        tipo = "analizar"
    elif RE_LEER.search(t):
        tipo = "leer"
    # Algo de sus cursos por el tema, aunque no pida nada concreto.
    elif TEMAS_CAMPUS.search(t) and len(palabras) <= 6:
        tipo = "leer"
    else:
        tipo = "charla"

    ms = (time.perf_counter() - t0) * 1000
    return {
        "tipo": tipo,
        "texto": limpio,
        "ms": ms,
        "es_tema_campus": bool(TEMAS_CAMPUS.search(t)),
        "urgente": tipo in ("abrir", "leer", "local"),
    }


def resumen() -> str:
    return ("abrir=al instante | leer=al instante | local=al instante | "
            "analizar=con reloj | charla=rápido y corto")
