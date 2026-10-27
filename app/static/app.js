"use strict";

/*
 * Roll Call web UI. Plain DOM, no build step.
 * Every value from the API is inserted with textContent (via el()), never innerHTML,
 * so names, reasons, and notifications cannot inject markup.
 * The access token lives in an HTTP-only cookie set by /api/v1/auth/login; codes and
 * QR tokens are only ever held in memory.
 */

const API = "/api/v1";
const QR_REFRESH_MS = 20_000;
const BOARD_POLL_MS = 4_000;
const STUDENT_POLL_MS = 15_000;

const state = {
  user: null,
  sections: [],
  sectionId: null,
  tab: "sessions",
  pendingQr: null, // QR token from a scanned link, held until the student is signed in
  timers: new Set(),
};

/* ---------- DOM helpers ---------- */

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style") node.setAttribute("style", value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svgIcon(path, size = 20) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = path; // static markup from this file only
  return svg;
}

const ICONS = {
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
  mark: '<rect width="24" height="24" rx="5" fill="#F6D44A"/><path d="M6.5 12.5l3.5 3.5 7.5-8" stroke="#1B2430" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
};

function brand() {
  const mark = svgIcon(ICONS.mark, 28);
  mark.classList.add("brand-mark");
  return el("span", { class: "brand" }, mark, "Roll Call");
}

// Like replaceChildren, but skips null/false so conditional sections don't render as the text "null".
function fill(host, ...children) {
  host.replaceChildren(...children.flat().filter((child) => child !== null && child !== undefined && child !== false));
}

function mount(...nodes) {
  clearTimers();
  const root = document.getElementById("app");
  root.replaceChildren(...nodes);
}

function every(ms, fn) {
  const id = setInterval(fn, ms);
  state.timers.add(id);
  return id;
}

function clearTimers() {
  for (const id of state.timers) clearInterval(id);
  state.timers.clear();
}

let toastTimer;
function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
}

function confirmDialog({ title, body, confirmLabel, danger = false }) {
  return new Promise((resolve) => {
    const dialog = el(
      "dialog",
      { "aria-labelledby": "dialog-title" },
      el("h2", { id: "dialog-title" }, title),
      el("p", { class: "muted", style: "margin-top:8px" }, body),
      el(
        "div",
        { class: "row" },
        el("button", { class: "btn secondary", value: "no", onclick: () => dialog.close("no") }, "Keep it"),
        el("button", { class: danger ? "btn danger" : "btn", onclick: () => dialog.close("yes") }, confirmLabel),
      ),
    );
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "yes");
      dialog.remove();
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

const tooltip = {
  show(target, text) {
    const node = document.getElementById("tooltip");
    node.textContent = text;
    node.hidden = false;
    const box = target.getBoundingClientRect();
    const tip = node.getBoundingClientRect();
    const left = Math.min(Math.max(8, box.left + box.width / 2 - tip.width / 2), window.innerWidth - tip.width - 8);
    const top = box.top - tip.height - 8 < 8 ? box.bottom + 8 : box.top - tip.height - 8;
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
  },
  hide() {
    document.getElementById("tooltip").hidden = true;
  },
  attach(target, text) {
    target.setAttribute("aria-label", text);
    target.addEventListener("mouseenter", () => tooltip.show(target, text));
    target.addEventListener("focus", () => tooltip.show(target, text));
    target.addEventListener("mouseleave", tooltip.hide);
    target.addEventListener("blur", tooltip.hide);
    return target;
  },
};

/* ---------- Formatting ---------- */

const dateFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

const formatDate = (iso) => dateFmt.format(new Date(iso.length === 10 ? `${iso}T12:00:00` : iso));
const formatTime = (iso) => timeFmt.format(new Date(iso));

function timeLeft(expiresAt) {
  const ms = new Date(expiresAt) - Date.now();
  if (ms <= 0) return null;
  const total = Math.ceil(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function minutesLeftText(expiresAt) {
  const ms = new Date(expiresAt) - Date.now();
  if (ms <= 0) return "Ended";
  const minutes = Math.ceil(ms / 60000);
  return minutes <= 1 ? "Less than a minute left" : `${minutes} minutes left`;
}

const fullName = (person) => `${person.first_name} ${person.last_name}`;
const percent = (value) => `${Math.round(value)}%`;
const STATUS_LABEL = { present: "Present", late: "Late", excused: "Excused", absent: "Absent", open: "Open", expired: "Ended", closed: "Ended", cancelled: "Cancelled", pending: "Waiting for review", approved: "Approved", rejected: "Rejected" };
const statusBadge = (status) => el("span", { class: `status ${status}` }, STATUS_LABEL[status] || status);

/* ---------- API ---------- */

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function describeDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = (item.loc || []).filter((part) => part !== "body").join(" ");
        return field ? `${field.replaceAll("_", " ")}: ${item.msg}` : item.msg;
      })
      .join(". ");
  }
  return "Something went wrong.";
}

async function api(path, { method = "GET", json, form, files, headers = {} } = {}) {
  const init = { method, headers: { ...headers }, credentials: "same-origin" };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (files) {
    init.body = files; // FormData; the browser sets the multipart boundary
  } else if (form) {
    init.body = new URLSearchParams(form);
  }
  let response;
  try {
    response = await fetch(API + path, init);
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new ApiError(response.status, describeDetail(body.detail));
    if (response.status === 401 && state.user && !path.startsWith("/auth/")) {
      state.user = null;
      renderAuth("Your sign-in expired. Sign in again to continue.");
    }
    throw error;
  }
  return body;
}

function deviceId() {
  // A random per-browser id so the server can stop one phone confirming for several students.
  try {
    let id = localStorage.getItem("rollcall-device");
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem("rollcall-device", id);
    }
    return id;
  } catch {
    return undefined;
  }
}

/* ---------- Start-up & routing ---------- */

function takeQrFromUrl() {
  const match = location.hash.match(/qr=([^&]+)/);
  if (!match) return false;
  state.pendingQr = decodeURIComponent(match[1]);
  history.replaceState(null, "", location.pathname);
  return true;
}

// A QR link opened in a tab where Roll Call is already running only changes the hash.
window.addEventListener("hashchange", () => {
  if (!takeQrFromUrl()) return;
  if (state.user?.role === "student") renderStudent();
  else if (!state.user) renderAuth("Sign in to confirm your attendance.");
  else {
    state.pendingQr = null;
    toast("That QR is for students. Sign in with a student account to use it.");
  }
});

async function start() {
  takeQrFromUrl();
  try {
    state.user = await api("/users/me");
  } catch {
    state.user = null;
  }
  if (!state.user) {
    renderAuth(state.pendingQr ? "Sign in to confirm your attendance." : null);
    return;
  }
  renderHome();
}

function renderHome() {
  if (state.user.role === "professor") renderProfessor();
  else renderStudent();
}

/* ---------- Sign in / create account ---------- */

function renderAuth(notice) {
  let mode = "signin";
  const errorBox = el("p", { class: "error-text", role: "alert" });
  const formHost = el("div");
  const tabs = el("div", { class: "tabs", role: "tablist" });

  function draw() {
    fill(tabs,
      el("button", { role: "tab", "aria-selected": String(mode === "signin"), onclick: () => switchTo("signin") }, "Sign in"),
      el("button", { role: "tab", "aria-selected": String(mode === "register"), onclick: () => switchTo("register") }, "Create account"),
    );
    errorBox.textContent = "";
    fill(formHost, mode === "signin" ? signInForm() : registerForm());
    formHost.querySelector("input")?.focus();
  }

  function switchTo(next) {
    mode = next;
    draw();
  }

  function signInForm(prefillEmail = "") {
    return el(
      "form",
      { onsubmit: submit },
      notice ? el("p", { class: "muted" }, notice) : null,
      el("label", { class: "field" }, el("span", {}, "Email"), el("input", { type: "email", name: "username", autocomplete: "username", required: true, value: prefillEmail })),
      el("label", { class: "field" }, el("span", {}, "Password"), el("input", { type: "password", name: "password", autocomplete: "current-password", required: true })),
      errorBox,
      el("button", { class: "btn", type: "submit" }, "Sign in"),
    );

    async function submit(event) {
      event.preventDefault();
      const button = event.target.querySelector("button[type=submit]");
      button.disabled = true;
      errorBox.textContent = "";
      try {
        state.user = await api("/auth/login", { method: "POST", form: new FormData(event.target) });
        renderHome();
      } catch (error) {
        errorBox.textContent =
          error.status === 401
            ? "That email and password don't match an account."
            : error.status === 429
              ? "Too many wrong passwords for this account. Wait 15 minutes, then try again."
              : error.message;
        button.disabled = false;
      }
    }
  }

  function registerForm() {
    const codeInput = el("input", { type: "text", name: "professor_code", autocomplete: "off", spellcheck: "false" });
    const codeField = el(
      "label",
      { class: "field", hidden: true },
      el("span", {}, "Professor access code ", el("span", { class: "hint" }, "from your department")),
      codeInput,
    );
    function onRoleChange(event) {
      const isProfessor = event.target.value === "professor";
      codeField.hidden = !isProfessor;
      codeInput.required = isProfessor;
      if (isProfessor) codeInput.focus();
    }
    return el(
      "form",
      { onsubmit: submit },
      el(
        "fieldset",
        { class: "choice-group" },
        el("legend", {}, "I am a"),
        el("label", { class: "choice" }, el("input", { type: "radio", name: "role", value: "student", checked: true, onchange: onRoleChange }), el("span", {}, "Student")),
        el("label", { class: "choice" }, el("input", { type: "radio", name: "role", value: "professor", onchange: onRoleChange }), el("span", {}, "Professor")),
      ),
      codeField,
      el(
        "div",
        { class: "row", style: "flex-wrap:nowrap" },
        el("label", { class: "field", style: "flex:1" }, el("span", {}, "First name"), el("input", { type: "text", name: "first_name", autocomplete: "given-name", required: true, maxlength: 100 })),
        el("label", { class: "field", style: "flex:1" }, el("span", {}, "Last name"), el("input", { type: "text", name: "last_name", autocomplete: "family-name", required: true, maxlength: 100 })),
      ),
      el("label", { class: "field" }, el("span", {}, "University email"), el("input", { type: "email", name: "email", autocomplete: "email", required: true })),
      el(
        "label",
        { class: "field" },
        el("span", {}, "Password ", el("span", { class: "hint" }, "at least 12 characters")),
        el("input", { type: "password", name: "password", autocomplete: "new-password", required: true, minlength: 12 }),
      ),
      errorBox,
      el("button", { class: "btn", type: "submit" }, "Create account"),
    );

    async function submit(event) {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.target));
      if (data.role !== "professor") delete data.professor_code;
      const button = event.target.querySelector("button[type=submit]");
      button.disabled = true;
      errorBox.textContent = "";
      try {
        await api("/auth/register", { method: "POST", json: data });
        state.user = await api("/auth/login", { method: "POST", form: { username: data.email, password: data.password } });
        toast("Account created. You're signed in.");
        renderHome();
      } catch (error) {
        errorBox.textContent =
          error.status === 409
            ? "An account with this email already exists. Sign in instead."
            : error.status === 403
              ? "That professor access code isn't right. Ask your department for the current code."
              : error.message;
        button.disabled = false;
      }
    }
  }

  const art = el(
    "div",
    { class: "auth-art" },
    brand(),
    el("div", { class: "auth-code", "aria-hidden": "true" }, "K7QX9 2PAB3"),
    el("p", {}, "Professors put a code on the board. Students type it or scan it, and they're marked present."),
  );

  mount(
    el(
      "main",
      { class: "auth" },
      art,
      el("div", { class: "auth-form-wrap" }, el("div", { class: "auth-form" }, el("h1", {}, "Welcome"), tabs, formHost)),
    ),
  );
  draw();
}

/* ---------- Shared shell ---------- */

function topbar() {
  const dot = el("span", { class: "notif-dot", hidden: true });
  const bell = el("button", { class: "btn secondary small notif-btn", "aria-label": "Notifications", "aria-expanded": "false" }, svgIcon(ICONS.bell, 18), dot);
  let panel = null;

  async function refreshDot() {
    try {
      const notes = await api("/notifications");
      dot.hidden = !notes.some((note) => !note.read_at);
    } catch {
      /* the bell is a convenience; ignore failures */
    }
  }

  bell.addEventListener("click", async () => {
    if (panel) {
      closePanel();
      return;
    }
    const notes = await api("/notifications");
    panel = el(
      "div",
      { class: "popover", role: "dialog", "aria-label": "Notifications" },
      el("h2", {}, "Notifications"),
      notes.length
        ? el(
            "ul",
            { style: "list-style:none;margin:0;padding:0" },
            notes.map((note) =>
              el(
                "li",
                { class: note.read_at ? "" : "unread" },
                el("strong", {}, note.title),
                el("p", { class: "muted small" }, note.message),
                el("p", { class: "small muted" }, `${formatDate(note.created_at)}, ${formatTime(note.created_at)}`),
              ),
            ),
          )
        : el("p", { class: "muted small", style: "padding:0 16px 12px" }, "Nothing yet. Cancelled classes and absence decisions show up here."),
    );
    document.body.append(panel);
    bell.setAttribute("aria-expanded", "true");
    if (notes.some((note) => !note.read_at)) {
      await api("/notifications/read", { method: "POST" });
      dot.hidden = true;
    }
    setTimeout(() => document.addEventListener("click", outside), 0);
  });

  function outside(event) {
    if (panel && !panel.contains(event.target) && !bell.contains(event.target)) closePanel();
  }
  function closePanel() {
    panel?.remove();
    panel = null;
    bell.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", outside);
  }

  refreshDot();
  const roleText = state.user.role === "professor" ? "Professor" : "Student";
  return el(
    "header",
    { class: "topbar" },
    brand(),
    el(
      "div",
      { class: "row", style: "flex-wrap:nowrap" },
      el("div", { class: "who" }, el("strong", {}, fullName(state.user)), el("span", { class: "muted small" }, roleText)),
      bell,
      el("button", { class: "btn secondary small", onclick: signOut }, "Sign out"),
    ),
  );
}

async function signOut() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  state.user = null;
  state.sections = [];
  state.sectionId = null;
  document.querySelector(".popover")?.remove();
  renderAuth("You're signed out.");
}

/* ---------- Charts ---------- */

const STAT_ORDER = ["present", "late", "excused", "absent"];

function splitBar(counts) {
  const parts = STAT_ORDER.map((status) => [status, counts[status] || 0]).filter(([, count]) => count > 0);
  const shown = STAT_ORDER.filter((status) => status !== "excused" || counts.excused);
  return el(
    "div",
    {},
    el(
      "div",
      { class: "split-bar", role: "img", "aria-label": parts.map(([status, count]) => `${count} ${status}`).join(", ") },
      parts.map(([status, count]) => el("span", { class: status, style: `flex:${count}` })),
    ),
    el(
      "div",
      { class: "legend" },
      shown.map((status) => el("span", { class: `status ${status}` }, `${STATUS_LABEL[status]} ${counts[status] || 0}`)),
    ),
    counts.excused ? el("p", { class: "muted small", style: "margin-top:6px" }, "Excused classes don't count toward your percentage.") : null,
  );
}

function attendanceSummary({ attended_sessions: attended, total_sessions: total, excused }) {
  const expected = total - excused;
  const counted = `${attended} of ${expected} ${expected === 1 ? "class" : "classes"} attended`;
  return excused ? `${counted}, plus ${excused} excused` : counted;
}

// Sessions carry their UTC start time; showing it in the viewer's timezone gives the right day.
const sessionDay = (session) => formatDate(session.starts_at || session.date);

function timeline(sessions) {
  return el(
    "div",
    { class: "timeline", role: "list" },
    sessions.map((session) =>
      tooltip.attach(el("button", { type: "button", class: session.status, role: "listitem" }), `${sessionDay(session)}: ${STATUS_LABEL[session.status]}`),
    ),
  );
}

// First and last session under the chart; add the time when both fall on the same day.
function axisLabels(bySession) {
  const first = bySession[0];
  const last = bySession.at(-1);
  if (bySession.length === 1) return [el("span", {}, sessionDay(first))];
  const sameDay = sessionDay(first) === sessionDay(last) && first.starts_at;
  const label = (session) => (sameDay ? `${sessionDay(session)}, ${formatTime(session.starts_at)}` : sessionDay(session));
  return [el("span", {}, label(first)), el("span", {}, label(last))];
}

function sessionBars(bySession) {
  return el(
    "div",
    {},
    el(
      "div",
      { class: "bars", role: "list", "aria-label": "Attendance by class session" },
      bySession.map((session) =>
        tooltip.attach(
          el("button", { type: "button", role: "listitem" }, el("span", { style: `height:${Math.max(session.attendance_percentage, 1)}%` })),
          `${sessionDay(session)}: ${session.confirmed} of ${session.enrolled} present${session.excused ? `, ${session.excused} excused` : ""} (${percent(session.attendance_percentage)})`,
        ),
      ),
    ),
    el("div", { class: "axis" }, ...axisLabels(bySession)),
    el(
      "details",
      {},
      el("summary", {}, "Show as table"),
      el(
        "table",
        { class: "data" },
        el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Present"), el("th", {}, "Excused"), el("th", {}, "Rate"))),
        el(
          "tbody",
          {},
          bySession.map((session) =>
            el("tr", {}, el("td", {}, sessionDay(session)), el("td", {}, `${session.confirmed} of ${session.enrolled}`), el("td", {}, session.excused || 0), el("td", {}, percent(session.attendance_percentage))),
          ),
        ),
      ),
    ),
  );
}

/* ================================================================
   Student
   ================================================================ */

async function renderStudent() {
  const liveHost = el("div");
  const statsHost = el("div", { class: "section-block" });
  const absenceHost = el("div", { class: "section-block" });

  mount(
    topbar(),
    el(
      "main",
      { class: "main narrow" },
      el("div", { class: "page-head" }, el("div", {}, el("h1", {}, `Hi, ${state.user.first_name}`), el("p", { class: "muted" }, "Mark yourself present when your professor opens attendance."))),
      liveHost,
      statsHost,
      absenceHost,
    ),
  );

  let stats = null;

  async function loadLive() {
    state.sections = await api("/sections");
    drawLive();
  }

  function drawLive() {
    const open = state.sections.filter((section) => section.open_session);
    if (!state.sections.length) {
      fill(liveHost,
        el("div", { class: "empty" }, el("strong", {}, "You're not in any classes yet"), `Ask your professor to add you using your email, ${state.user.email}.`),
      );
      return;
    }
    if (!open.length) {
      fill(liveHost,
        el(
          "div",
          { class: "empty" },
          el("strong", {}, "No attendance open right now"),
          "When your professor starts attendance it appears here automatically. You can also scan the QR on the board with your phone camera.",
        ),
      );
      return;
    }
    // Keep a card that is mid-typing in place, so a background refresh never wipes the code.
    const active = document.activeElement;
    if (active?.classList.contains("code-input") && active.value) return;
    fill(liveHost, ...open.map((section) => liveCard(section, onConfirmed)));
  }

  async function onConfirmed() {
    await loadLive();
    await loadStats();
  }

  async function loadStats() {
    stats = await api("/students/me/statistics");
    drawStats();
    drawAbsence();
  }

  function drawStats() {
    const bySection = new Map();
    for (const session of stats.by_session) {
      if (!bySection.has(session.section_id)) bySection.set(session.section_id, { name: session.section_name, sessions: [] });
      bySection.get(session.section_id).sessions.push(session);
    }
    fill(statsHost,
      el("h2", {}, "My attendance"),
      stats.total_sessions
        ? el(
            "div",
            { class: "panel" },
            el(
              "div",
              { class: "hero-stat" },
              el("span", { class: "figure" }, percent(stats.attendance_percentage)),
              el("span", { class: "muted" }, attendanceSummary(stats)),
            ),
            splitBar(stats),
          )
        : el("p", { class: "muted" }, "Your attendance shows up here after your first class takes attendance."),
      bySection.size
        ? el(
            "ul",
            { class: "list", style: "margin-top:16px" },
            [...bySection.values()].map(({ name, sessions }) => {
              const attended = sessions.filter((session) => session.status === "present" || session.status === "late").length;
              return el(
                "li",
                {},
                el("div", { class: "row spread" }, el("h3", {}, name), el("span", { class: "muted small" }, `${attended} of ${sessions.length} attended`)),
                timeline(sessions),
              );
            }),
          )
        : null,
    );
  }

  function drawAbsence() {
    fill(absenceHost, el("h2", {}, "Absence requests"), absenceForm(stats, loadAbsences), el("div", { id: "absence-history", style: "margin-top:16px" }));
    loadAbsences();
  }

  async function loadAbsences() {
    const requests = await api("/absence-requests/me");
    const host = document.getElementById("absence-history");
    if (!host) return;
    fill(host,
      requests.length
        ? el(
            "ul",
            { class: "list" },
            requests.map((request) =>
              el(
                "li",
                {},
                el("div", { class: "row spread" }, el("strong", {}, request.section_name), statusBadge(request.status)),
                el("p", { class: "muted small" }, request.session_date ? `For the class on ${formatDate(request.session_date)}. Sent ${formatDate(request.created_at)}.` : `Sent ${formatDate(request.created_at)}.`),
                el("p", { style: "margin-top:4px" }, request.reason),
                attachmentLine(request, loadAbsences),
              ),
            ),
          )
        : el("p", { class: "muted small" }, "You haven't sent any absence requests."),
    );
  }

  // Sections first: the absence form built by loadStats lists them.
  await loadLive();
  await loadStats();
  if (state.pendingQr) confirmFromQr();
  every(STUDENT_POLL_MS, () => loadLive().catch(() => {}));
  every(1000, () => document.querySelectorAll("[data-expires]").forEach((node) => (node.textContent = minutesLeftText(node.dataset.expires))));
}

function liveCard(section, onConfirmed) {
  const open = section.open_session;
  if (open.already_confirmed) {
    return el(
      "div",
      { class: "live-card done" },
      el("div", { class: "row", style: "flex-wrap:nowrap" }, el("span", { class: "check-big" }, svgIcon(ICONS.check, 24)), el("div", {}, el("h2", {}, section.name), el("p", { class: "muted" }, "You're marked present."))),
    );
  }
  const errorBox = el("p", { class: "error-text", role: "alert" });
  const input = el("input", {
    type: "text",
    class: "code-input",
    name: "code",
    placeholder: "ABCDE 12345",
    autocomplete: "off",
    autocapitalize: "characters",
    spellcheck: "false",
    "aria-label": `Attendance code for ${section.name}`,
    required: true,
  });
  const button = el("button", { class: "btn", type: "submit" }, "Mark me present");
  const card = el(
    "div",
    { class: "live-card" },
    el("div", { class: "row spread" }, el("h2", {}, section.name), el("strong", { "data-expires": open.expires_at }, minutesLeftText(open.expires_at))),
    el("p", { class: "muted" }, "Type the code from the board, or scan its QR with your phone camera."),
    open.location_required ? el("p", { class: "muted small", style: "margin-top:6px" }, "This class checks that you're in the room, so your browser will ask for your location once. It's only compared with the classroom's position.") : null,
    el("form", { onsubmit: submit, novalidate: true }, input, button),
    errorBox,
  );

  async function submit(event) {
    event.preventDefault();
    const code = input.value.replace(/\s+/g, "").toUpperCase();
    if (code.length < 6) {
      errorBox.textContent = "Enter the full code shown on the board.";
      input.focus();
      return;
    }
    button.disabled = true;
    button.textContent = "Checking…";
    errorBox.textContent = "";
    const result = await confirmAttendance(open.id, { code }, open.location_required);
    if (result.ok) {
      toast(confirmedMessage(result, section.name));
      await onConfirmed();
    } else {
      errorBox.textContent = result.message;
      button.disabled = false;
      button.textContent = "Mark me present";
      input.select();
    }
  }
  return card;
}

function currentPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("This browser can't share your location, which this class requires. Try another browser or your phone."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
      () => reject(new Error("Location is needed for this class. Allow location access in your browser and try again.")),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  });
}

async function confirmAttendance(sessionId, credentials, locationRequired) {
  const body = { ...credentials };
  if (locationRequired) {
    try {
      Object.assign(body, await currentPosition());
    } catch (error) {
      return { ok: false, message: error.message };
    }
  }
  const id = deviceId();
  try {
    const record = await api(`/attendance-sessions/${encodeURIComponent(sessionId)}/confirm`, {
      method: "POST",
      json: body,
      headers: id ? { "X-Device-Id": id } : {},
    });
    return { ok: true, status: record.status };
  } catch (error) {
    if (error.status === 422 && /location is required/i.test(error.message) && !locationRequired) {
      return confirmAttendance(sessionId, credentials, true);
    }
    if (error.status === 409 && /already confirmed/i.test(error.message)) return { ok: true, already: true };
    return { ok: false, message: confirmErrorMessage(error, Boolean(credentials.qr_token)) };
  }
}

function confirmedMessage(result, sectionName) {
  if (result.already) return "You're already marked present.";
  const where = sectionName ? ` in ${sectionName}` : "";
  return result.status === "late" ? `You're marked late${where}.` : `You're marked present${where}.`;
}

function confirmErrorMessage(error, viaQr) {
  const detail = error.message.toLowerCase();
  switch (error.status) {
    case 400:
      return viaQr
        ? "That QR has expired. Scan the one on the board now, or type the code."
        : "That code doesn't match. Check the board and try again.";
    case 403:
      return detail.includes("outside")
        ? "You seem to be outside the classroom. Move closer and try again."
        : "You're not in this class. Ask your professor to add you.";
    case 404:
    case 410:
      return "Attendance for this class has ended.";
    case 409:
      if (detail.includes("qr")) return "That QR was already used. Scan the new one on the board, or type the code.";
      if (detail.includes("device")) return "This device was already used to mark another student present. Use your own phone or computer.";
      return error.message;
    case 429:
      return "Too many wrong tries. Wait a few minutes, then try again.";
    default:
      return error.message;
  }
}

function sessionIdFromQr(token) {
  // Only reads the session id to pick the URL; the server verifies the signature.
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(payload)).session_id || null;
  } catch {
    return null;
  }
}

async function confirmFromQr() {
  const token = state.pendingQr;
  state.pendingQr = null;
  const sessionId = sessionIdFromQr(token);
  if (!sessionId) {
    toast("That QR link isn't valid. Type the code from the board instead.");
    return;
  }
  const section = state.sections.find((item) => item.open_session?.id === sessionId);
  if (section?.open_session.already_confirmed) {
    toast("You're already marked present.");
    return;
  }
  toast("Confirming your attendance…");
  const result = await confirmAttendance(sessionId, { qr_token: token }, Boolean(section?.open_session.location_required));
  if (result.ok) {
    toast(confirmedMessage(result, section?.name));
    renderStudent();
  } else {
    toast(result.message);
  }
}

const ATTACHMENT_ACCEPT = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;

function checkAttachment(file) {
  if (file.size > MAX_ATTACHMENT_BYTES) return "That file is larger than 5 MB. Attach a smaller scan or photo.";
  if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) return "Attach a PDF, PNG, or JPEG file.";
  return null;
}

function uploadAttachment(requestId, file) {
  const body = new FormData();
  body.append("file", file);
  return api(`/absence-requests/${encodeURIComponent(requestId)}/attachment`, { method: "POST", files: body });
}

function attachmentLink(request) {
  return el(
    "a",
    { href: `${API}/absence-requests/${encodeURIComponent(request.id)}/attachment`, target: "_blank", rel: "noopener", class: "small" },
    `Open ${request.attachment_name || "attached document"}`,
  );
}

// Shows the attached file, or lets the student attach one while the request is still pending.
function attachmentLine(request, onChange) {
  if (request.has_attachment) return el("p", {}, attachmentLink(request));
  if (request.status !== "pending") return null;
  const errorBox = el("span", { class: "error-text", role: "alert" });
  const input = el("input", { type: "file", accept: ATTACHMENT_ACCEPT, class: "visually-hidden", "aria-label": "Attach a document" });
  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    const problem = checkAttachment(file);
    if (problem) {
      errorBox.textContent = problem;
      return;
    }
    try {
      await uploadAttachment(request.id, file);
      toast("Document attached.");
      onChange();
    } catch (error) {
      errorBox.textContent = error.message;
    }
  });
  return el("div", { class: "row" }, el("button", { type: "button", class: "btn link small", onclick: () => input.click() }, "Attach a document"), input, errorBox);
}

function absenceForm(stats, onSent) {
  if (!state.sections.length) return el("p", { class: "muted small" }, "Once you're in a class you can ask to be excused from it here.");
  const errorBox = el("p", { class: "error-text", role: "alert" });
  const sectionSelect = el(
    "select",
    { name: "section_id", required: true },
    state.sections.map((section) => el("option", { value: section.id }, section.name)),
  );
  const sessionSelect = el("select", { name: "attendance_session_id" });
  const reason = el("textarea", { name: "reason", required: true, minlength: 10, maxlength: 2000, placeholder: "For example: doctor's appointment, I'll bring the note next week." });
  const counter = el("span", { class: "hint" });
  const fileInput = el("input", { type: "file", name: "file", accept: ATTACHMENT_ACCEPT });

  function fillSessions() {
    // Only classes you missed can be excused; a future class is excused by your professor after it happens.
    const missed = stats.by_session.filter((session) => session.section_id === sectionSelect.value && session.status === "absent").reverse();
    fill(sessionSelect,
      missed.map((session) => el("option", { value: session.session_id }, `${sessionDay(session)} (missed)`)),
      el("option", { value: "" }, "A class that hasn't happened yet"),
    );
  }
  function updateCounter() {
    const remaining = 10 - reason.value.trim().length;
    counter.textContent = remaining > 0 ? `${remaining} more characters needed` : "";
  }
  sectionSelect.addEventListener("change", fillSessions);
  reason.addEventListener("input", updateCounter);
  fillSessions();
  updateCounter();

  async function submit(event) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    if (data.reason.trim().length < 10) {
      errorBox.textContent = "Give a short reason, at least 10 characters.";
      reason.focus();
      return;
    }
    const file = fileInput.files[0];
    const fileProblem = file ? checkAttachment(file) : null;
    if (fileProblem) {
      errorBox.textContent = fileProblem;
      return;
    }
    const payload = { section_id: data.section_id, reason: data.reason.trim() };
    if (data.attendance_session_id) payload.attendance_session_id = data.attendance_session_id;
    errorBox.textContent = "";
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const request = await api("/absence-requests", { method: "POST", json: payload });
      let message = "Absence request sent. You'll get a notification when it's reviewed.";
      if (file) {
        try {
          await uploadAttachment(request.id, file);
        } catch (error) {
          message = `Request sent, but the file wasn't attached: ${error.message} You can attach it from the list below.`;
        }
      }
      event.target.reset();
      fillSessions();
      updateCounter();
      toast(message);
      onSent();
    } catch (error) {
      errorBox.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  return el(
    "form",
    { class: "panel stack", onsubmit: submit, novalidate: true },
    el(
      "div",
      { class: "row", style: "align-items:start" },
      el("label", { class: "field", style: "flex:1;min-width:180px" }, el("span", {}, "Class"), sectionSelect),
      el("label", { class: "field", style: "flex:1;min-width:180px" }, el("span", {}, "Which day"), sessionSelect),
    ),
    el("label", { class: "field" }, el("span", {}, "Reason ", counter), reason),
    el("p", { class: "small muted" }, "If your professor approves a request for a missed class, it no longer counts against your attendance."),
    el("label", { class: "field" }, el("span", {}, "Supporting document ", el("span", { class: "hint" }, "optional, PDF or photo up to 5 MB")), fileInput),
    errorBox,
    el("button", { class: "btn", type: "submit" }, "Send request"),
  );
}

/* ================================================================
   Professor
   ================================================================ */

async function renderProfessor() {
  state.sections = await api("/sections");
  if (!state.sectionId || !state.sections.some((section) => section.id === state.sectionId)) {
    state.sectionId = state.sections[0]?.id || null;
  }
  const main = el("main", { class: "main" });
  mount(topbar(), el("div", { class: "layout" }, professorSidebar(), main));
  if (!state.sectionId) {
    main.append(
      el("h1", {}, "Your classes"),
      el(
        "div",
        { class: "empty", style: "margin-top:20px;max-width:520px" },
        el("strong", {}, "Create your first class"),
        "Name it in the box on the left, for example \"Databases, group 2\". Then add students and start attendance.",
      ),
    );
    return;
  }
  renderSection(main);
}

function professorSidebar() {
  const input = el("input", { type: "text", name: "name", placeholder: "New class name", required: true, maxlength: 200, "aria-label": "New class name" });
  async function create(event) {
    event.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const section = await api("/sections", { method: "POST", json: { name } });
    state.sectionId = section.id;
    state.tab = "students";
    toast(`Created ${name}. Add your students next.`);
    renderProfessor();
  }
  return el(
    "nav",
    { class: "sidebar", "aria-label": "Classes" },
    el("h2", {}, "Classes"),
    el(
      "ul",
      { class: "class-list" },
      state.sections.map((section) =>
        el(
          "li",
          {},
          el(
            "button",
            {
              "aria-current": String(section.id === state.sectionId),
              onclick: () => {
                state.sectionId = section.id;
                state.tab = "sessions";
                renderProfessor();
              },
            },
            el("span", {}, section.name),
            section.open_session ? el("span", { class: "live-pill" }, "Live") : null,
          ),
        ),
      ),
    ),
    el("form", { onsubmit: create }, input, el("button", { class: "btn secondary small", type: "submit" }, "Add class")),
  );
}

async function renderSection(main) {
  const section = state.sections.find((item) => item.id === state.sectionId);
  const absences = await api(`/sections/${section.id}/absence-requests`);
  const pending = absences.filter((request) => request.status === "pending").length;

  const tabsList = [
    ["sessions", "Attendance"],
    ["scan", "Scan sheet"],
    ["students", "Students"],
    ["absences", "Absence requests", pending],
    ["stats", "Statistics"],
  ];
  const body = el("div", { id: "section-body", style: "margin-top:24px" });
  const tabs = el(
    "div",
    { class: "tabs", role: "tablist" },
    tabsList.map(([key, label, count]) =>
      el(
        "button",
        {
          role: "tab",
          "aria-selected": String(state.tab === key),
          onclick: () => {
            state.tab = key;
            renderSection(main);
          },
        },
        label,
        count ? el("span", { class: "count" }, count) : null,
      ),
    ),
  );

  const action = section.open_session
    ? el("button", { class: "btn", onclick: () => resumeBoard(section) }, "Show attendance board")
    : el("button", { class: "btn", onclick: () => openStartPanel(main) }, "Start attendance");

  fill(main,
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, section.name), el("p", { class: "muted" }, section.student_count === 1 ? "1 student" : `${section.student_count} students`)),
      action,
    ),
    tabs,
    body,
  );

  const views = { sessions: sessionsView, scan: scanView, students: studentsView, absences: (item, host) => absencesView(item, host, absences), stats: statsView };
  await views[state.tab](section, body);
}

async function openStartPanel(main) {
  if (state.tab !== "sessions") {
    state.tab = "sessions";
    await renderSection(main);
  }
  const panel = document.getElementById("start-attendance");
  if (!panel) return;
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  panel.querySelector("input:checked")?.focus({ preventScroll: true });
}

async function sessionsView(section, host) {
  const sessions = await api(`/sections/${section.id}/sessions`);
  const live = sessions.find((session) => session.status === "open");
  const panel = live ? null : startPanel(section);
  // A first-time class shows the form straight away; afterwards the "Start attendance" button opens it.
  if (panel && sessions.length && panel.id === "start-attendance") panel.hidden = true;
  fill(host,
    panel,
    el(
      "div",
      { class: "section-block", style: live ? "margin-top:0" : "" },
      el("h2", {}, "Past and current sessions"),
      sessions.length
        ? el(
            "ul",
            { class: "list" },
            sessions.map((session) => sessionRow(section, session)),
          )
        : el("p", { class: "muted" }, "No sessions yet. Each time you start attendance it's listed here."),
    ),
  );
}

const ROSTER_CHOICES = ["present", "late", "excused", "absent"];
const METHOD_LABEL = { qr: "by QR", code: "by code", manual: "set by you", absence_request: "approved absence request" };

function sessionMeta(session, attended = session.confirmed) {
  const parts = [`${attended} of ${session.eligible} present`];
  if (session.late_after_minutes) parts.push(`late after ${session.late_after_minutes} min`);
  if (session.location_required) parts.push("in-room check on");
  return parts.join(", ");
}

function sessionRow(section, session) {
  const roster = el("div");
  const meta = el("p", { class: "meta" }, sessionMeta(session));
  const actions = el("div", { class: "row" });
  if (session.status === "open") actions.append(el("button", { class: "btn small", onclick: () => resumeBoard(section) }, "Show board"));
  if (session.status !== "cancelled") {
    actions.append(el("button", { class: "btn secondary small", "aria-expanded": "false", onclick: toggle }, "Attendance list"));
  }
  const li = el(
    "li",
    {},
    el(
      "div",
      { class: "session-row" },
      el(
        "div",
        {},
        el("div", { class: "row" }, el("strong", {}, `${formatDate(session.starts_at)}, ${formatTime(session.starts_at)}`), statusBadge(session.status)),
        meta,
      ),
      actions,
    ),
    roster,
  );

  async function toggle(event) {
    const button = event.currentTarget;
    if (roster.childElementCount) {
      fill(roster);
      button.setAttribute("aria-expanded", "false");
      return;
    }
    const rows = await api(`/attendance-sessions/${session.id}/roster`);
    button.setAttribute("aria-expanded", "true");
    drawRoster(rows);
  }

  function drawRoster(rows) {
    meta.textContent = sessionMeta(session, rows.filter((row) => row.status === "present" || row.status === "late").length);
    if (!rows.length) {
      fill(roster, el("div", { class: "records" }, el("p", { class: "muted" }, "Nobody was in this class when it took place.")));
      return;
    }
    fill(
      roster,
      el(
        "div",
        { class: "records" },
        el("p", { class: "muted small" }, "Change a status if something went wrong, for example a student whose phone was dead. The student is notified."),
        el(
          "ul",
          { class: "roster" },
          rows.map((row) => {
            const select = el(
              "select",
              { "aria-label": `Attendance for ${fullName(row.student)}` },
              row.status === "pending" ? el("option", { value: "pending", selected: true, disabled: true }, "Not yet") : null,
              ROSTER_CHOICES.map((choice) => el("option", { value: choice, selected: row.status === choice }, STATUS_LABEL[choice])),
            );
            select.addEventListener("change", async () => {
              select.disabled = true;
              try {
                const updated = await api(`/attendance-sessions/${session.id}/students/${row.student.id}`, { method: "PUT", json: { status: select.value } });
                toast(`${fullName(row.student)} marked ${STATUS_LABEL[select.value].toLowerCase()}.`);
                drawRoster(rows.map((item) => (item.student.id === row.student.id ? updated : item)));
              } catch (error) {
                toast(error.message);
                select.disabled = false;
              }
            });
            const detail = row.confirmed_at && row.confirmation_method !== "manual"
              ? `${formatTime(row.confirmed_at)}, ${METHOD_LABEL[row.confirmation_method] || row.confirmation_method}`
              : METHOD_LABEL[row.confirmation_method] || "";
            return el(
              "li",
              {},
              el("span", { class: `status ${row.status}`, "aria-hidden": "true" }),
              el("span", { class: "roster-name" }, el("strong", {}, fullName(row.student)), detail ? el("span", { class: "muted small" }, detail) : null),
              select,
            );
          }),
        ),
      ),
    );
  }
  return li;
}

function startPanel(section) {
  const errorBox = el("p", { class: "error-text", role: "alert" });
  const geoStatus = el("p", { class: "small muted" }, "Your current position becomes the classroom location.");
  const geoBox = el(
    "div",
    { class: "geo-row", hidden: true },
    geoStatus,
    el(
      "fieldset",
      { class: "choice-group" },
      el("legend", {}, "How far from you can students be?"),
      [50, 100, 200].map((meters) =>
        el("label", { class: "choice" }, el("input", { type: "radio", name: "radius", value: meters, checked: meters === 100 }), el("span", {}, `${meters} m`)),
      ),
    ),
  );
  const geoToggle = el("input", { type: "checkbox", name: "geo" });
  geoToggle.addEventListener("change", () => (geoBox.hidden = !geoToggle.checked));

  async function submit(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = { section_id: section.id, duration_minutes: Number(form.get("duration")) };
    if (form.get("late")) payload.late_after_minutes = Number(form.get("late"));
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    errorBox.textContent = "";
    try {
      if (geoToggle.checked) {
        geoStatus.textContent = "Getting your location…";
        const position = await currentPosition();
        Object.assign(payload, {
          location_latitude: position.latitude,
          location_longitude: position.longitude,
          allowed_radius_meters: Number(form.get("radius")),
        });
      }
      const session = await api("/attendance-sessions", { method: "POST", json: payload });
      openBoard(section, session.id, session.code, session.expires_at, session.qr_token);
    } catch (error) {
      errorBox.textContent = error.message;
      button.disabled = false;
    }
  }

  if (!section.student_count) {
    return el(
      "div",
      { class: "empty" },
      el("strong", {}, "Add students before starting attendance"),
      "Students who aren't in the class can't mark themselves present. ",
      el("button", { class: "btn link", onclick: () => ((state.tab = "students"), renderProfessor()) }, "Add students"),
    );
  }

  return el(
    "section",
    { class: "panel start-panel", id: "start-attendance", "aria-labelledby": "start-title" },
    el(
      "form",
      { onsubmit: submit },
      el("h2", { id: "start-title" }, "Start attendance"),
      el(
        "fieldset",
        { class: "choice-group" },
        el("legend", {}, "Students can mark themselves present for"),
        [5, 10, 15, 30, 60].map((minutes) =>
          el("label", { class: "choice" }, el("input", { type: "radio", name: "duration", value: minutes, checked: minutes === 15 }), el("span", {}, `${minutes} min`)),
        ),
      ),
      el(
        "fieldset",
        { class: "choice-group" },
        el("legend", {}, "Mark students late if they confirm after"),
        [
          ["", "Never"],
          ["5", "5 min"],
          ["10", "10 min"],
          ["15", "15 min"],
        ].map(([value, label]) =>
          el("label", { class: "choice" }, el("input", { type: "radio", name: "late", value, checked: value === "" }), el("span", {}, label)),
        ),
      ),
      el("label", { class: "toggle" }, geoToggle, "Only count students who are in the room"),
      geoBox,
      errorBox,
      el("div", {}, el("button", { class: "btn", type: "submit" }, "Start attendance")),
    ),
  );
}

async function resumeBoard(section) {
  const ok = await confirmDialog({
    title: "Show the attendance board?",
    body: "The board shows a new code. The previous code stops working, but everyone already marked present stays present.",
    confirmLabel: "Show board",
  });
  if (!ok) return;
  try {
    const rotation = await api(`/attendance-sessions/${section.open_session.id}/rotate-code`, { method: "POST" });
    openBoard(section, section.open_session.id, rotation.code, section.open_session.expires_at, rotation.qr_token);
  } catch (error) {
    toast(error.message);
    renderProfessor();
  }
}

function formatCode(code) {
  const half = Math.ceil(code.length / 2);
  return [el("span", { class: "half" }, code.slice(0, half)), el("span", { class: "half" }, code.slice(half))];
}

function qrSvg(token) {
  if (typeof qrcode !== "function") return el("p", { class: "small" }, "QR unavailable offline. Students can type the code.");
  const qr = qrcode(0, "M");
  qr.addData(`${location.origin}/#qr=${encodeURIComponent(token)}`);
  qr.make();
  const holder = document.createElement("div");
  holder.innerHTML = qr.createSvgTag({ cellSize: 6, margin: 0, scalable: true }); // generated locally from our own URL
  const svg = holder.firstElementChild;
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "QR code for marking attendance");
  return svg;
}

function openBoard(section, sessionId, code, expiresAt, firstQr) {
  clearTimers();
  const seen = new Set();
  let firstPoll = true;
  let ended = false;

  const codeNode = el("div", { class: "board-code", "aria-label": `Code ${code.split("").join(" ")}` }, formatCode(code));
  const timeNode = el("div", { class: "board-time" });
  const qrHost = el("div");
  const qrBar = el("span");
  const qrMeta = el("p", { class: "qr-meta" }, "QR changes every 20 seconds");
  const countNode = el("div", { class: "board-count" }, `0 of ${section.student_count} present`);
  const names = el("div", { class: "board-names", "aria-live": "polite" });
  const instructions = el("p", { class: "board-instructions" }, "Open Roll Call and type this code, or scan the QR with your phone camera.");

  const board = el(
    "div",
    { class: "board", role: "dialog", "aria-modal": "true", "aria-label": `Attendance board for ${section.name}` },
    el(
      "div",
      { class: "board-top" },
      el("h1", {}, section.name),
      el(
        "div",
        { class: "row" },
        el("button", { class: "btn small", onclick: newCode }, "New code"),
        el("button", { class: "btn small", onclick: fullscreen }, "Full screen"),
        el("button", { class: "btn small", onclick: cancel }, "Cancel class"),
        el("button", { class: "btn small primary", onclick: endNow }, "End attendance"),
        el("button", { class: "btn small", onclick: close }, "Close board"),
      ),
    ),
    el(
      "div",
      { class: "board-main" },
      el("div", {}, instructions, codeNode, timeNode),
      el("div", { class: "qr-frame" }, qrHost, el("div", { class: "qr-progress" }, qrBar), qrMeta),
    ),
    el("div", { class: "board-foot" }, countNode, names),
  );

  function drawQr(token) {
    fill(qrHost, qrSvg(token));
    qrBar.animate([{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }], { duration: QR_REFRESH_MS, easing: "linear" });
  }

  async function refreshQr() {
    if (ended) return;
    try {
      const fresh = await api(`/attendance-sessions/${sessionId}/qr-token`, { method: "POST" });
      drawQr(fresh.qr_token);
    } catch (error) {
      if (error.status === 409 || error.status === 410) finish();
    }
  }

  async function pollRecords() {
    try {
      const rows = await api(`/attendance-sessions/${sessionId}/records`);
      countNode.textContent = `${rows.length} of ${section.student_count} present`;
      fill(names,
        ...rows.slice(0, 12).map((row) => el("span", { class: !firstPoll && !seen.has(row.student.id) ? "new" : "" }, fullName(row.student))),
        rows.length > 12 ? el("span", {}, `+${rows.length - 12} more`) : null,
      );
      rows.forEach((row) => seen.add(row.student.id));
      firstPoll = false;
    } catch {
      /* keep showing the last list */
    }
  }

  function tick() {
    const left = timeLeft(expiresAt);
    if (!left) {
      finish();
      return;
    }
    timeNode.textContent = `${left} left`;
  }

  function finish() {
    if (ended) return;
    ended = true;
    clearTimers();
    board.classList.add("ended");
    instructions.textContent = "Attendance has ended.";
    fill(codeNode, "Done");
    codeNode.removeAttribute("aria-label");
    timeNode.textContent = "";
    qrHost.closest(".qr-frame").remove();
    board.querySelectorAll(".board-top .btn").forEach((button) => {
      if (button.textContent !== "Close board") button.remove();
    });
    pollRecords();
  }

  async function newCode() {
    const rotation = await api(`/attendance-sessions/${sessionId}/rotate-code`, { method: "POST" });
    fill(codeNode, ...formatCode(rotation.code));
    codeNode.setAttribute("aria-label", `Code ${rotation.code.split("").join(" ")}`);
    drawQr(rotation.qr_token);
    toast("New code is up. The old one no longer works.");
  }

  async function endNow() {
    const ok = await confirmDialog({
      title: "End attendance now?",
      body: "Students who haven't marked themselves present will count as absent for this class.",
      confirmLabel: "End attendance",
    });
    if (!ok) return;
    await api(`/attendance-sessions/${sessionId}/close`, { method: "POST" });
    finish();
  }

  async function cancel() {
    const ok = await confirmDialog({
      title: "Cancel this class?",
      body: "Every student gets a notification, and this session won't count toward anyone's attendance.",
      confirmLabel: "Cancel class",
      danger: true,
    });
    if (!ok) return;
    await api(`/attendance-sessions/${sessionId}/cancel`, { method: "POST" });
    toast("Class cancelled. Students have been notified.");
    close();
  }

  function fullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else board.requestFullscreen?.();
  }

  function close() {
    clearTimers();
    document.removeEventListener("keydown", onKey);
    if (document.fullscreenElement) document.exitFullscreen();
    board.remove();
    renderProfessor();
  }

  function onKey(event) {
    if (event.key === "Escape" && !document.fullscreenElement && !document.querySelector("dialog[open]")) close();
  }

  document.body.append(board);
  document.addEventListener("keydown", onKey);
  drawQr(firstQr);
  tick();
  pollRecords();
  every(1000, tick);
  every(QR_REFRESH_MS, refreshQr);
  every(BOARD_POLL_MS, pollRecords);
}

async function studentsView(section, host, problems = "", leftover = "") {
  const students = await api(`/sections/${section.id}/students`);
  const errorBox = el("p", { class: "error-text", role: "alert" }, problems);
  const input = el("input", { type: "text", inputmode: "email", name: "email", value: leftover, placeholder: "student@university.edu", required: true, "aria-label": "Student emails" });

  async function add(event) {
    event.preventDefault();
    errorBox.textContent = "";
    const emails = input.value.split(/[\s,;]+/).filter(Boolean);
    const failures = [];
    for (const email of emails) {
      try {
        await api(`/sections/${section.id}/enrollments`, { method: "POST", json: { email } });
      } catch (error) {
        failures.push(error.status === 404 ? `${email} has no student account yet` : error.status === 409 ? `${email} is already in this class` : `${email}: ${error.message}`);
      }
    }
    const added = emails.length - failures.length;
    if (added) toast(added === 1 ? "Student added." : `${added} students added.`);
    const problemText = failures.length ? `${failures.join(". ")}.` : "";
    const leftoverEmails = emails.filter((email) => failures.some((failure) => failure.startsWith(email))).join(", ");
    // Refresh the sidebar counts and header, then keep the failed emails in the box so they can be fixed.
    state.sections = await api("/sections");
    await renderSection(document.querySelector(".main"));
    if (failures.length) {
      await studentsView(state.sections.find((item) => item.id === section.id), document.getElementById("section-body"), problemText, leftoverEmails);
    }
  }

  fill(host,
    el(
      "form",
      { class: "panel stack", onsubmit: add },
      el("label", { class: "field" }, el("span", {}, "Add students by email ", el("span", { class: "hint" }, "separate several with commas")), el("div", { class: "row", style: "flex-wrap:nowrap" }, input, el("button", { class: "btn", type: "submit" }, "Add"))),
      el("p", { class: "small muted" }, "Students need a Roll Call account first. They create one with the same email."),
      errorBox,
    ),
    el(
      "div",
      { class: "section-block" },
      el("h2", {}, "In this class"),
      students.length
        ? el(
            "ul",
            { class: "list" },
            students.map((student) =>
              el(
                "li",
                { class: "student-row" },
                el("span", { class: "roster-name" }, el("strong", {}, fullName(student)), el("span", { class: "muted small" }, student.email)),
                el("button", { class: "btn secondary small", onclick: () => remove(student) }, "Remove"),
              ),
            ),
          )
        : el("p", { class: "muted" }, "No students yet."),
    ),
  );

  async function remove(student) {
    const ok = await confirmDialog({
      title: `Remove ${fullName(student)} from ${section.name}?`,
      body: "Their past attendance is kept. Classes from now on won't count for them, and they won't see this class. You can add them again later.",
      confirmLabel: "Remove student",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/sections/${section.id}/enrollments/${student.id}`, { method: "DELETE" });
      toast(`${fullName(student)} removed from ${section.name}.`);
      state.sections = await api("/sections");
      await renderSection(document.querySelector(".main"));
    } catch (error) {
      toast(error.message);
    }
  }
}

function absencesView(section, host, requests) {
  if (!requests.length) {
    fill(host, el("div", { class: "empty" }, el("strong", {}, "No absence requests"), "When a student asks to be excused from this class, it shows up here for you to approve or reject."));
    return;
  }
  fill(host,
    el(
      "ul",
      { class: "list" },
      requests.map((request) =>
        el(
          "li",
          { class: "stack" },
          el("div", { class: "row spread" }, el("strong", {}, request.student_name), statusBadge(request.status)),
          el("p", { class: "small muted" }, request.session_date ? `For the class on ${formatDate(request.session_date)}. Sent ${formatDate(request.created_at)}.` : `For a class that hadn't happened yet. Sent ${formatDate(request.created_at)}.`),
          el("p", {}, request.reason),
          request.has_attachment ? el("p", {}, attachmentLink(request)) : null,
          request.attachment_url && /^https?:\/\//i.test(request.attachment_url)
            ? el("a", { href: request.attachment_url, target: "_blank", rel: "noopener noreferrer", class: "small" }, "Open linked document")
            : null,
          request.status === "pending"
            ? el(
                "div",
                { class: "stack" },
                el(
                  "p",
                  { class: "small muted" },
                  request.session_date
                    ? "Approving excuses the student from that class, so it doesn't count against their attendance."
                    : "This request isn't linked to a class day. After that class, open its attendance list and set the student to Excused.",
                ),
                el(
                  "div",
                  { class: "row" },
                  el("button", { class: "btn small", onclick: () => review(request, "approved") }, "Approve"),
                  el("button", { class: "btn secondary small", onclick: () => review(request, "rejected") }, "Reject"),
                ),
              )
            : null,
        ),
      ),
    ),
  );

  async function review(request, status) {
    try {
      await api(`/absence-requests/${request.id}`, { method: "PATCH", json: { status } });
    } catch (error) {
      toast(error.message);
      return;
    }
    toast(status === "approved" ? `Approved. ${request.student_name} has been notified.` : `Rejected. ${request.student_name} has been notified.`);
    renderSection(document.querySelector(".main"));
  }
}

async function statsView(section, host) {
  const stats = await api(`/sections/${section.id}/statistics`);
  if (!stats.total_sessions) {
    fill(host, el("div", { class: "empty" }, el("strong", {}, "No statistics yet"), "Start attendance to see how many students come to each class."));
    return;
  }
  fill(host,
    el(
      "div",
      { class: "panel" },
      el(
        "div",
        { class: "hero-stat" },
        el("span", { class: "figure" }, percent(stats.attendance_percentage)),
        el("span", { class: "muted" }, `average attendance across ${stats.total_sessions} ${stats.total_sessions === 1 ? "session" : "sessions"}`),
      ),
      el("div", { style: "margin-top:24px" }, el("h3", {}, "Share of the class present, per session"), sessionBars(stats.by_session)),
    ),
  );
}

start();
