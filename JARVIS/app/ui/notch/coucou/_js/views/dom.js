// Minimal DOM helpers — no framework, as specified.
import { Msg, onLanguageChange } from "../i18n/i18n.js";
const bindings = new Set();
let sincePrune = 0;
function read(node, attr) {
    if (attr === "text")
        return node.nodeType === 3 ? node.data : node.textContent;
    return node.getAttribute(attr);
}
function write(node, attr, value) {
    if (attr === "text") {
        if (node.nodeType === 3)
            node.data = value;
        else
            node.textContent = value;
    }
    else {
        node.setAttribute(attr, value);
    }
}
function bind(node, attr, msg, shown) {
    bindings.add({ node: new WeakRef(node), attr, msg, shown });
    // Views rebuild rows all the time: drop what has been collected now and then.
    if (++sincePrune >= 500) {
        sincePrune = 0;
        for (const b of bindings)
            if (!b.node.deref())
                bindings.delete(b);
    }
}
/** Redraws every live `tl(…)` text in the current language. */
export function relabel() {
    for (const b of bindings) {
        const node = b.node.deref();
        if (!node || read(node, b.attr) !== b.shown) {
            bindings.delete(b);
            continue;
        }
        const next = String(b.msg);
        if (next !== b.shown) {
            write(node, b.attr, next);
            b.shown = next;
        }
    }
}
/** How many texts follow the language (tests). */
export function liveTextCount() {
    return bindings.size;
}
onLanguageChange(() => relabel());
export function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false)
            continue;
        if (v instanceof Msg) {
            const text = String(v);
            write(el, k, text);
            bind(el, k, v, text);
            continue;
        }
        if (k === "class")
            el.className = String(v);
        else if (k === "text")
            el.textContent = String(v);
        else if (k.startsWith("on") && typeof v === "function") {
            el.addEventListener(k.slice(2).toLowerCase(), v);
        }
        else if (k === "style")
            el.setAttribute("style", String(v));
        else
            el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children) {
        if (c == null || c === false)
            continue;
        if (c instanceof Msg) {
            const node = document.createTextNode(String(c));
            bind(node, "text", c, String(c));
            el.append(node);
            continue;
        }
        el.append(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return el;
}
export function svg(path, size = 14, opts = {}) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("width", String(size));
    el.setAttribute("height", String(size));
    el.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", path);
    if (opts.stroke) {
        p.setAttribute("fill", "none");
        p.setAttribute("stroke", "currentColor");
        p.setAttribute("stroke-width", String(opts.stroke));
        p.setAttribute("stroke-linecap", "round");
        p.setAttribute("stroke-linejoin", "round");
    }
    else {
        p.setAttribute("fill", opts.fill ?? "currentColor");
        // Lets a filled glyph carry a cut-out (checkmark.seal.fill, xmark.octagon.fill).
        if (opts.evenOdd)
            p.setAttribute("fill-rule", "evenodd");
    }
    el.append(p);
    return el;
}
export function clear(el) {
    while (el.firstChild)
        el.removeChild(el.firstChild);
}
/** Card dot used in every "who" row. */
export function dot(color, size = 7) {
    return h("i", {
        class: "dot",
        style: `width:${size}px;height:${size}px;background:${color}`,
    });
}
