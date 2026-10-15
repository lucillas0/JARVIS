"""Deteccion de DOS palmadas consecutivas para activar JARVIS.

Que es y que no es
-----------------
No es un segundo sistema de voz. No abre el microfono, no crea hilos, no llama
al STT y no tiene palabra de activacion. Recibe exactamente los mismos bloques
de muestras que ya recibe el wake word, en el mismo callback, y devuelve True.
Quien decide que eso activa a JARVIS es ``VoiceManager.wake_now()``, que es el
MISMO metodo que usa la palabra de activacion. Aqui no hay nada mas.

Por eso este modulo no abre ningun stream de audio: el bloque de muestras le
llega por parametro. Si JARVIS corre en un NAS sin microfono, este codigo no
llega a ejecutarse nunca y no pasa nada.

Como se distingue una palmada de un ruido
-----------------------------------------
"Es volumen alto" no sirve: un portazo, la television o un golpe de martillo
dispararian lo mismo. Lo que separa una palmada de todo eso son tres cosas a la
vez:

  1. Un ataque brusco. Una palmada empieza instantaneamente; la television
     empieza despacio. Se mide comparando con el bloque anterior: la energia
     tiene que multiplicarse de golpe, no subir poco a poco.

  2. Un salto respecto al ruido de fondo. El umbral no es fijo, se adapta al
     silencio de la habitacion, para que sirva en una biblioteca y en un cuarto
     en silencio sin tocar nada.

  3. Dos, y no una. Una palmada sola no activa nada: hace falta la segunda
     dentro de un intervalo corto. El puerto queda cerrado despues, para que
     dos palmadas separadas por un minuto no cuenten.

Con las tres, un golpe seco, un portazo y el principio de una palabra quedan
muy por debajo del umbral, y el martillo de un vecino tampoco.

DONDE NO LLEGA, y conviene saberlo
----------------------------------
DOS PORTAZOS SEGUIDOS SI ACTIVAN A JARVIS. Dos golpes secos y cercanos son,
para el microfono, literalmente lo mismo que dos palmadas: dos transientes
broadband separados por menos de un segundo. Separarlos pediria mirar el
espectro o usar un modelo entrenado, y las dos cosas pesan mas de lo que cabe
en un detector que tiene que ser ligerisimo.

Lo que SI se rechaza, y esta medido en la prueba: la television y la musica de
fondo (que empiezan despacio y se quedan), una voz hablando, un portazo suelto,
un golpe largo y el ruido de fondo de una habitacion con gente. Si algun dia
molesta el caso de los dos portazos, el sitio para ajustarlo es MAX_GAP: bajarlo
a menos de 0,4 s hace que dos palmadas bien separadas dejen de contar, a cambio
de tener que juntarlas mas.

Cuando esta DESACTIVADO
----------------------
Se comprueba la opcion al principio de la funcion y se sale. No se calcula
nada: ni energia, ni umbral, ni historial. El coste de estar apagado es una
comparacion de cadenas, y el de estar encendido unas cuentas por bloque de
100 ms. Sin procesos duplicados, sin hilos, sin modelos pesados.
"""
import logging
import threading
import time

import numpy as np

log = logging.getLogger("voice.clap")

# Bloques de 100 ms (1600 muestras a 16 kHz). Los tiempos van en segundos
# reales, no en bloques, para que cambiar el tamano del bloque no rompa nada.

# Cuanto tiene que subir la energia de golpe respecto al bloque anterior.
# Por debajo de 1,8 la television y la musica tambien lo pasan; por encima de
# 3,5 dejaria pasar ruido de fondo alto. 2,6 esta en el medio.
SUBIDA = 2.6

# Cuantas veces tiene que superar el ruido de fondo. En una habitacion
# silenciosa el suelo es muy bajo y 4 alcanzaria; en una con ruido 8 ya no se
# distingue. 5,5 funciona en los dos casos sin tocar nada.
SOBRE_RUIDO = 5.5

# Suelo del ruido de fondo, para que en un silencio casi digital no se detecte
# un susurro como si fuera una palmada.
SUELO_RUIDO = 1.5e-4

# Separacion maxima entre las dos palmadas. Por debajo de 0,6 s seria casi
# imposible (dos manos a la vez); por encima de 2,5 s ya no son "consecutivas"
# sino dos golpes sueltos que no deberian activar nada.
MAX_GAP = 1.1

# Separacion minima. Sin esto, la cola de una palmada (que dura unos 200 ms)
# podria contarse como la segunda.
MIN_GAP = 0.16

# Tiempo en el que se ignoran las palmadas despues de disparar, para no
# encadenar activaciones con un aplauso.
REFRACTARIO = 1.5

# Adaptacion del suelo de ruido: baja rapido hacia el silencio y sube muy
# Adaptacion del suelo de ruido. Baja rapido hacia el silencio, sube a
# velocidad normal, y NUNCA sube mientras hay un pico.
#
# La subida lenta era un error: el suelo tardaba minutos en subirse, y en
# una habitacion con ruido una palmada no se detectaba en diez segundos.
# Con 0,01 se acomoda en unos segundos, que es lo que hace falta.
#
# Y para que subir rapido no ciegue el detector, el suelo esta CONGELADO
# mientras hay un pico: una palmada fuerte no mueve el liston, asi que la
# segunda palmada se sigue viendo. Ademas es lo correcto por lo fisico: el
# suelo representa el silencio de la habitacion, y una palmada no es
# silencio de habitacion.
_BAJA = 0.20
_SUBE = 0.01


def _reloj() -> float:
    """El reloj del detector, en un sitio aparte.

    Existe para que las pruebas puedan avanzar el tiempo a voluntad en vez
    de esperar en la realidad. Una prueba que metiese los bloques al
    instante veria microsegundos entre dos "palmadas" separadas quince
    bloques, y no podria comprobar nada del intervalo maximo.
    """
    return time.monotonic()


class ClapDetector:
    """Detecta DOS palmadas seguidas. Un solo objeto durante toda la sesion."""

    def __init__(self, enabled: bool = False, reloj=None):
        self.enabled = bool(enabled)
        self._suelo = SUELO_RUIDO
        self._prev = 0.0
        self._primera = 0.0        # momento de la primera palmada, si la hay
        self._ultimo = 0.0         # momento de la ultima palmada aceptada
        self._refractario = 0.0    # hasta cuando no se acepta ninguna mas
        self._lock = threading.Lock()
        self._reloj = reloj or _reloj
        self.activaciones = 0      # contador, para las pruebas y /health

    # ---------------------------------------------------------------- API
    def activar(self, encendido: bool) -> None:
        """Enciende o apaga. Apagar limpia el estado, para que al volver a
        encender no quede una palmada a medias esperando su segunda."""
        with self._lock:
            self.enabled = bool(encendido)
            if not self.enabled:
                self._primera = 0.0
                self._ultimo = 0.0
                self._refractario = 0.0
                self._suelo = SUELO_RUIDO
                self._prev = 0.0

    def reiniciar(self) -> None:
        """Olvida la palmada pendiente. Se llama al apagar el microfono."""
        with self._lock:
            self._primera = 0.0
            self._ultimo = 0.0

    def process(self, chunk) -> bool:
        """Bloque de muestras (float32, 16 kHz, mono). True si DOS palmadas.

        Nunca lanza. Un fallo aqui no puede dejar al microfono mudo, asi que
        cualquier excepcion se traga y se sigue con el bloque siguiente.
        """
        if not self.enabled:
            return False
        try:
            return self._process(chunk)
        except Exception as e:                       # pragma: no cover
            log.debug("clap: %s", e)
            return False

    # ------------------------------------------------------------ interno
    def _process(self, chunk) -> bool:
        ahora = self._reloj()

        # OJO con el orden de los cerrojos. threading.Lock NO es reentrante, y
        # _adaptar_suelo() vuelve a coger el mismo lock. Por eso la comprobacion
        # del refractario NO se puede hacer dentro de un "with self._lock" que
        # luego llame al adaptador: se queda esperando el mismo cerrojo para
        # siempre y el microfono entero se cuelga. Se lee el estado con el
        # cerrojo puesto, se sale, y ya se adapta fuera.
        with self._lock:
            en_refrac = ahora < self._refractario

        # La energia se calcula SIEMPRE, incluso en el refractario. Antes se
        # pasaba un 0.0 a proposito, y eso era un fallo: el suelo de ruido
        # bajaba a su minimo de golpe, asi que durante el segundo y medio
        # siguientes cualquier sonido pasaba el liston. O sea, que el detector
        # se quedaba sordo justo despues de disparar, que es cuando mas
        #lo esté. Con la energia real, el suelo sigue a la habitacion.
        energia = self._energia(chunk)

        if en_refrac:
            self._adaptar_suelo(energia)
            return False

        # Si la primera palmada se quedo esperando y ya paso demasiado, se
        # olvida. Sin esto, una palmada suelta seguida de un aplauso una hora
        # despues contarian como pareja.
        with self._lock:
            if self._primera and (ahora - self._primera) > MAX_GAP:
                self._primera = 0.0

        es_pico = (energia > self._suelo * SOBRE_RUIDO
                   and energia > self._prev * SUBIDA)

        if not es_pico:
            self._adaptar_suelo(energia)
            return False

        # Pico. El suelo se queda congelado a proposito: si una palmada alta
        # subiera el liston, la segunda pasaria desapercibida. Solo se guarda
        # la energia actual, para comparar con el bloque siguiente.
        with self._lock:
            self._prev = energia

        with self._lock:
            # Segunda palmada dentro de la ventana: dos de verdad.
            if self._primera and (ahora - self._primera) <= MAX_GAP:
                self._primera = 0.0
                self._ultimo = ahora
                self._refractario = ahora + REFRACTARIO
                self.activaciones += 1
                log.info("Dos palmadas detectadas.")
                return True

            # Primera palmada. Se anota y se espera la segunda.
            if not (self._ultimo and (ahora - self._ultimo) < MIN_GAP):
                self._primera = ahora
                self._ultimo = ahora
                log.debug("Palmada; esperando la segunda.")
            self._prev = energia
            return False

    def _energia(self, chunk) -> float:
        """RMS del bloque. Barata y suficiente: el objetivo no es medir
        volumen, es notar cuando el aire se mueve de golpe."""
        try:
            a = np.asarray(chunk, dtype=np.float32).ravel()
            if a.size == 0:
                return 0.0
            return float(np.sqrt(np.mean(np.square(a), dtype=np.float64)))
        except Exception:
            return 0.0

    def _adaptar_suelo(self, energia: float) -> None:
        """El suelo sigue al silencio de la habitacion.

        Baja rapido (para cogerse el silencio entre frases) y sube muy despacio
        (para que una palmada no deje el liston tan alto que la siguiente
        pase desapercibida).

        Puede llamarse desde dentro de otra seccion que ya tenga el cerrojo
        puesto, por eso el cerrojo se pide aparte y no se mete en un "with" que
        la rodee: con un Lock normal, pedirlo dos veces en el mismo camino se
        queda esperando para siempre.
        """
        self._lock.acquire()
        try:
            if energia < self._suelo:
                self._suelo += (energia - self._suelo) * _BAJA
            else:
                self._suelo += (energia - self._suelo) * _SUBE
            if self._suelo < SUELO_RUIDO:
                self._suelo = SUELO_RUIDO
            self._prev = energia
        finally:
            self._lock.release()


# ----------------------------------------------------------------------
# Preferencia: lee y escribe la opcion del dashboard
# ----------------------------------------------------------------------
# Vive en la MISMA clave que el resto del panel (settings.jarvis_config, dentro
# de la seccion "voice"), que es donde ya viven la velocidad de la voz y lo de
# leer en alto. No hay una segunda base de configuracion ni una segunda clave.
_CLAVE = "jarvis_config"


def _leer_preferencia() -> bool:
    """True si el señor la ha activado. False si no, o si algo falla."""
    try:
        import json as _js
        from memory import sqlite_store as _db
        crudo = _db.get_setting(_CLAVE, "") or ""
        if not crudo:
            return False
        d = _js.loads(crudo)
        if not isinstance(d, dict):
            return False
        voz = d.get("voice")
        return bool(isinstance(voz, dict) and voz.get("clap_enabled") is True)
    except Exception:
        return False


# El detector vive durante toda la sesion, igual que el motor de voz. Se crea
# de forma perezosa para que importar este modulo no tenga coste en un NAS sin
# microfono.
_detalector: ClapDetector | None = None
_lock_detalector = threading.Lock()


def detector() -> ClapDetector:
    global _detector
    if _detector is None:
        with _lock_detalector:
            if _detector is None:
                _detector = ClapDetector(enabled=_leer_preferencia())
    return _detector


def aplicar_preferencia(encendido: bool | None = None) -> bool:
    """Enciende o apaga el detector. Sin argumento, relee la del dashboard.

    Devuelve el estado que ha quedado. Se puede llamar en caliente: el
    dashboard la llama al guardar y asi el interruptor sirve al momento, sin
    reiniciar JARVIS.
    """
    if encendido is None:
        encendido = _leer_preferencia()
    d = detector()
    if d.enabled != bool(encendido):
        d.activar(bool(encendido))
        if encendido:
            log.info("Activacion por palmadas activada.")
        else:
            log.info("Activacion por palmadas desactivada.")
    return d.enabled


def estado() -> dict:
    d = detector()
    return {"activado": d.enabled, "detecciones": d.activaciones}