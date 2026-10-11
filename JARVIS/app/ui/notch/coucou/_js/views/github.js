// The GitHub card once the pulse is in — DOM ports of GitHubPulseCardView,
// GitHubDetailView, GitHubPRRowView, GitHubRepoCIRowView and
// GitHubActivityDetailContent (IslandViewContent.swift). Sizes, colours and
// wording follow the Swift views; the logic lives in core/github.ts.
//
// Without a pulse (no token scope for it yet, or the first poll still to come)
// the card stays the stars + repositories overview in integrations.ts.
import { h, svg, dot } from "./dom.js";
import { ICONS } from "./icons.js";
import { Bridge } from "../core/bridge.js";
import { CI_COLORS, GH_STRINGS, GRID_WEEKS, activityHeader, actionsUrl, ciWord, contributionColor, formatCount, lastDays, lastWeeks, mainCISummary, myPRsValue, profileUrl, safeGitHubUrl, sectionTitle, shortRepo, weekColumn, worstCI, } from "../core/github.js";
const GITHUB_RED = "#F4505E";
/** Which list the detail view shows; kept across re-renders of the card. */
let openSection = "myPRs";
function open(url) {
    const safe = safeGitHubUrl(url);
    if (safe)
        void Bridge.openUrl(safe);
}
/** Seven small squares: the last week of contributions. */
function miniRow(activity) {
    const row = h("span", { class: "gh-mini" });
    for (const day of lastDays(activity, 7)) {
        row.append(h("i", { style: `background:${contributionColor(day.level)}` }));
    }
    return row;
}
function statButton(icon, color, label, value, onClick) {
    return h("button", { class: "int-stat gh-stat", onclick: onClick }, h("i", { class: "int-stat-icon", style: `color:${color}` }, icon), h("span", { class: "int-stat-label", text: label }), h("span", { class: "int-stat-value", text: value }));
}
// ── Card ──────────────────────────────────────────────────────────────────────
export function githubPulseCard(pulse, stats, activity, openDetail) {
    const show = (section) => {
        openSection = section;
        void Bridge.githubRefresh(section === "activity" ? "activity" : "pulse");
        openDetail();
    };
    const head = h("div", { class: "int-head" }, dot(GITHUB_RED, 7), h("b", { text: GH_STRINGS.title }));
    if (stats || activity) {
        const spark = h("button", { class: "gh-spark", onclick: () => show("activity") });
        if (stats)
            spark.append(h("span", { text: `★ ${formatCount(stats.totalStars)}` }));
        if (activity)
            spark.append(miniRow(activity));
        head.append(spark);
    }
    else {
        head.append(h("span", { text: GH_STRINGS.overview }));
    }
    const prColor = CI_COLORS[worstCI(pulse.myPRs.map((p) => p.ci))];
    const reviews = pulse.toReview.length;
    const main = mainCISummary(pulse.mainCI);
    return h("div", { class: "int-card" }, head, h("div", { class: "int-stats gh-rows" }, statButton(svg(ICONS.pull, 10, { stroke: 2.2 }), prColor, GH_STRINGS.myPRs, myPRsValue(pulse.myPRs), () => show("myPRs")), statButton(svg(ICONS.eye, 10, { stroke: 2.2 }), reviews > 0 ? "#8AB4F8" : "#6B7079", GH_STRINGS.toReview, String(reviews), () => show("toReview")), statButton(svg(main.failing ? ICONS.octagonX : ICONS.seal, 10, { evenOdd: true }), main.color, GH_STRINGS.mainCI, main.value, () => show("mainCI"))));
}
// ── Detail: lists ─────────────────────────────────────────────────────────────
function backButton(title, onBack) {
    return h("button", { class: "gh-back", onclick: onBack }, svg(ICONS.chevronLeft, 9, { stroke: 2.6 }), h("span", { text: title }));
}
function ciDot(ci) {
    const el = dot(CI_COLORS[ci], 5);
    if (ci === "unknown")
        el.style.opacity = "0";
    return el;
}
/** What each list row opens: its click, and Ctrl+O when it is highlighted. */
const rowOpens = new WeakMap();
function row(onOpen, ...children) {
    const el = h("button", { class: "gh-row", onclick: onOpen }, ...children);
    rowOpens.set(el, onOpen);
    return el;
}
function prRow(pr, showCI) {
    return row(() => open(pr.url), showCI ? ciDot(pr.ci) : h("i", { class: "gh-gap" }), h("span", { class: "gh-ref", text: `${shortRepo(pr.repo)}#${pr.number}` }), h("span", { class: "gh-title", text: pr.title }), pr.isDraft ? h("span", { class: "gh-draft", text: GH_STRINGS.draft }) : null);
}
function repoRow(repo) {
    const word = ciWord(repo.ci);
    return row(() => open(actionsUrl(repo.url)), ciDot(repo.ci), h("span", { class: "gh-ref", text: shortRepo(repo.repo) }), h("span", { class: "gh-title", text: repo.branch }), word ? h("span", { class: "gh-word", style: `color:${CI_COLORS[repo.ci]}`, text: word }) : null);
}
// ── Keyboard: Ctrl+↓ Ctrl+↑ Ctrl+O (the Mac's cardSelection) ──────────────────
/** The rows of the GitHub list shown in `root`, top to bottom; none without one. */
export function listRows(root) {
    const list = root.querySelector(".gh-list");
    return list ? Array.from(list.children) : [];
}
/**
 * Highlights row `selection` and no other (GitHubPRRowView's `selected`).
 * `reveal`: the highlight just moved, so its row scrolls into the three the
 * list shows.
 */
export function highlightRow(rows, selection, reveal) {
    rows.forEach((el, i) => el.classList.toggle("selected", i === selection));
    if (reveal && selection != null)
        rows[selection]?.scrollIntoView({ block: "nearest" });
}
/** Ctrl+O: does what a click on the row does. */
export function openRow(el) {
    if (el)
        rowOpens.get(el)?.();
}
function listDetail(section, pulse, onBack) {
    const rows = section === "mainCI"
        ? pulse.mainCI.map(repoRow)
        : (section === "myPRs" ? pulse.myPRs : pulse.toReview).map((pr) => prRow(pr, section === "myPRs"));
    const body = rows.length === 0
        ? h("div", { class: "gh-empty", text: GH_STRINGS.nothingHere })
        : h("div", { class: rows.length > 3 ? "gh-list fade" : "gh-list" }, ...rows);
    return h("div", { class: "int-card" }, h("div", { class: "gh-detail-head" }, backButton(sectionTitle(section), onBack)), body);
}
// ── Detail: contribution grid ─────────────────────────────────────────────────
function activityDetail(activity, stats, login, onBack) {
    const right = h("button", {
        class: "gh-head-right",
        text: activityHeader(activity, stats, null),
        onclick: () => open(profileUrl(login)),
    });
    let picked = null;
    const show = (day) => {
        picked = day;
        right.textContent = activityHeader(activity, stats, day);
    };
    const head = h("div", { class: "gh-detail-head" }, backButton(GH_STRINGS.activity, onBack));
    if (activity)
        head.append(right);
    if (!activity) {
        return h("div", { class: "int-card" }, head, h("div", { class: "gh-empty", text: GH_STRINGS.loading }));
    }
    const grid = h("div", { class: "gh-grid" });
    for (const week of lastWeeks(activity, GRID_WEEKS)) {
        const column = h("div", { class: "gh-week" });
        for (const day of weekColumn(week)) {
            if (!day) {
                column.append(h("i", { class: "gh-cell empty" }));
                continue;
            }
            const cell = h("i", { class: "gh-cell", style: `background:${contributionColor(day.level)}` });
            cell.addEventListener("mouseenter", () => show(day));
            cell.addEventListener("mouseleave", () => show(null));
            cell.addEventListener("click", () => show(picked?.date === day.date ? null : day));
            column.append(cell);
        }
        grid.append(column);
    }
    return h("div", { class: "int-card" }, head, grid);
}
export function githubDetail(pulse, stats, activity, onBack) {
    return openSection === "activity"
        ? activityDetail(activity, stats, pulse.login, onBack)
        : listDetail(openSection, pulse, onBack);
}
