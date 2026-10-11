// El sonido de Coucou. Los .wav no vienen en el repo, asi que los 30 nombres
// se sintetizan; la API es la que espera el resto del codigo.
export const SOUND_NAMES = [
    "peek", "open", "close", "hover", "blip", "slap", "annoyed", "dizzy", "greet",
    "work", "finish", "error", "approval", "question", "approve", "gulp", "tick",
    "send", "love", "pop", "proud", "wink", "yawn", "attach", "think", "search",
    "rate", "sleep", "greeting",
];
const TONOS = {
    peek: [520, 0.09, "sine", 0.5], open: [660, 0.10, "sine", 0.5],
    close: [420, 0.10, "sine", 0.4], hover: [880, 0.05, "sine", 0.3],
    blip: [720, 0.05, "sine", 0.4], slap: [180, 0.09, "triangle", 0.6],
    annoyed: [240, 0.11, "square", 0.35], dizzy: [300, 0.22, "sine", 0.35],
    greet: [740, 0.16, "sine", 0.5], work: [520, 0.06, "sine", 0.35],
    finish: [880, 0.16, "sine", 0.5], error: [200, 0.20, "sawtooth", 0.4],
    approval: [660, 0.14, "sine", 0.5], question: [600, 0.13, "sine", 0.45],
    approve: [760, 0.12, "sine", 0.5], gulp: [160, 0.13, "triangle", 0.5],
    tick: [1200, 0.03, "sine", 0.25], send: [940, 0.06, "sine", 0.4],
    love: [1000, 0.18, "sine", 0.45], pop: [840, 0.05, "sine", 0.45],
    proud: [900, 0.14, "sine", 0.5], wink: [700, 0.07, "sine", 0.4],
    yawn: [300, 0.24, "sine", 0.35], attach: [560, 0.06, "sine", 0.35],
    think: [640, 0.07, "sine", 0.35], search: [700, 0.07, "sine", 0.35],
    rate: [400, 0.14, "square", 0.35], sleep: [320, 0.30, "sine", 0.3],
    greeting: [780, 0.20, "sine", 0.5],
};
let ctx = null;
let volumen = 0.12;
let habilitado = true;
function contexto() {
    if (typeof window === "undefined")
        return null;
    if (!ctx) {
        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor)
            return null;
        try {
            ctx = new Ctor();
        }
        catch (e) {
            return null;
        }
    }
    return ctx;
}
class Sonido {
    setVolume(v) { volumen = Math.max(0, Math.min(1, v)); }
    setEnabled(on) { habilitado = !!on; }
    isEnabled() { return habilitado; }
    async preload() { contexto(); }
    async reload() { }
    resume() {
        const c = contexto();
        if (c && c.state === "suspended")
            void c.resume();
    }
    /** Corta un sonido a mitad, como hace el original. */
    fadeOut(name, seconds) {
        const c = contexto();
        if (!c)
            return;
        const actual = this.ganando.get(name);
        if (!actual)
            return;
        try {
            actual.gain.gain.cancelScheduledValues(c.currentTime);
            actual.gain.gain.setValueAtTime(Math.max(0.0001, actual.gain.gain.value), c.currentTime);
            actual.gain.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + seconds);
        }
        catch (e) { /* ya termino */ }
    }
    /** Silencia lo que este sonando sin cortar el contexto. */
    idle() {
        const c = contexto();
        if (!c)
            return;
        for (const [, v] of this.ganando) {
            try {
                v.gain.gain.cancelScheduledValues(c.currentTime);
                v.gain.gain.setValueAtTime(0.0001, c.currentTime);
            }
            catch (e) { /* ya termino */ }
        }
        this.ganando.clear();
    }
    play(name, opts) {
        if (!habilitado)
            return;
        const tono = TONOS[String(name)];
        if (!tono)
            return;
        const c = contexto();
        if (!c)
            return;
        if (c.state === "suspended")
            void c.resume();
        const [freq, dur, type, amp] = tono;
        const osc = c.createOscillator();
        const g = c.createGain();
        osc.type = type;
        osc.frequency.value = freq;
        const v = (opts && opts.volume != null ? opts.volume : 1) * volumen * amp;
        g.gain.setValueAtTime(Math.max(0.0001, v), c.currentTime);
        g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
        osc.connect(g);
        g.connect(c.destination);
        osc.start();
        osc.stop(c.currentTime + dur);
        this.ganando.set(String(name), { osc, gain: g });
        osc.onended = () => { this.ganando.delete(String(name)); };
    }
    ganando = new Map();
}
export const Sound = new Sonido();
