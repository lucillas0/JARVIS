// Small pieces every settings section draws with.
import { h } from "../views/dom.js";
export function statusDot(ok) {
    return h("i", { class: "dot", style: `background:${ok ? "#22c55e" : "#f4505e"}` });
}
export function renderDiff(text) {
    const box = h("div", { class: "diff" });
    for (const line of text.split("\n")) {
        const cls = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
        box.append(h("div", { class: cls, text: line }));
    }
    return box;
}
