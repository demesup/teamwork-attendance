// "Scan sheet" tab: a photo of the paper list becomes a digital attendance list the professor checks and approves.
// Uses the helpers from app.js (el, api, toast, fill, renderProfessor).

const SCAN_ACCEPT = "image/png,image/jpeg,.png,.jpg,.jpeg";
const SCAN_MAX_BYTES = 5 * 1024 * 1024;

async function scanView(section, host) {
  const students = await api(`/sections/${section.id}/students`);
  const status = el("p", { class: "muted", role: "status" }, "Take a straight, well-lit photo of the list. Nothing is saved until you approve the list.");
  const dateInput = el("input", { type: "date", "aria-label": "Date of the sheet" });
  const fileInput = el("input", { type: "file", accept: SCAN_ACCEPT, required: true, "aria-label": "Photo of the attendance sheet" });
  const readButton = el("button", { class: "btn", type: "submit" }, "Read sheet");
  const result = el("div", { id: "scan-result" });

  async function read(event) {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) return toast("Choose a photo first");
    if (file.size > SCAN_MAX_BYTES) return toast("The photo is larger than 5 MB. Retake it smaller.");
    const body = new FormData();
    body.append("file", file);
    if (dateInput.value) body.append("session_date", dateInput.value);
    body.append("annotate_sheet", "true");
    readButton.disabled = true;
    status.textContent = "Reading the sheet. The first scan takes a little longer while the reader loads.";
    result.replaceChildren();
    try {
      const preview = await api(`/sections/${section.id}/attendance-scans`, { method: "POST", files: body });
      if (preview.session_date && !dateInput.value) dateInput.value = preview.session_date;
      status.textContent = "Check every row below, then approve the list.";
      result.replaceChildren(scanReview(section, students, preview, dateInput));
    } catch (error) {
      status.textContent = error.message;
    } finally {
      readButton.disabled = false;
    }
  }

  fill(host,
    el(
      "form",
      { class: "section-block", style: "margin-top:0", onsubmit: read },
      el("h2", {}, "Scan an attendance sheet"),
      status,
      el(
        "div",
        { class: "scan-controls" },
        el("label", {}, el("span", {}, "Photo"), fileInput),
        el("label", {}, el("span", {}, "Date of the sheet"), dateInput),
        readButton,
      ),
    ),
    result,
  );
}

function scanReview(section, students, preview, dateInput) {
  const sessions = preview.sessions.map((part) => scanSession(section, students, part));
  const approve = el("button", { class: "btn", type: "button" }, "Approve and save");
  const note = el("p", { class: "muted" }, "Saved sessions are recorded as attendance taken from the sheet.");

  async function save() {
    const date = dateInput.value;
    if (!date) return toast("Choose the date of the sheet first");
    const payload = {
      session_date: date,
      sessions: sessions.map((session) => ({ label: session.label, student_ids: session.checkedIds() })),
    };
    approve.disabled = true;
    try {
      await api(`/sections/${section.id}/attendance-scans/confirm`, { method: "POST", json: payload });
      toast(`Saved ${sessions.length} session${sessions.length === 1 ? "" : "s"} for ${date}.`);
      state.tab = "sessions";
      renderProfessor();
    } catch (error) {
      toast(error.message);
      approve.disabled = false;
    }
  }
  approve.onclick = save;

  return el(
    "div",
    { class: "scan-result section-block", style: "margin-top:0" },
    preview.annotated_image
      ? el(
          "figure",
          { class: "scan-photo" },
          el("img", { src: preview.annotated_image, alt: "The sheet with rows the app could not read marked in red" }),
          el("figcaption", { class: "muted" }, "Red boxes mark rows the app could not read. Check them against the photo."),
        )
      : null,
    sessions.map((session) => session.node),
    el("div", { class: "scan-approve" }, approve, note),
  );
}

function scanSession(section, students, part) {
  const title = part.label ? `Session ${part.label}` : "Sheet";
  const boxes = new Map();
  const picks = [];
  const everyone = students.map((student) => ({ id: student.id, name: `${student.last_name} ${student.first_name}`.trim() }));

  function checkbox(id, checked) {
    const input = el("input", { type: "checkbox" });
    input.checked = checked;
    boxes.set(id, input);
    return input;
  }

  function pickRow(label, options, preselect) {
    const select = el(
      "select",
      { "aria-label": label },
      el("option", { value: "" }, "Nobody"),
      options.map((option) => el("option", { value: option.id }, option.name)),
    );
    select.value = preselect || "";
    picks.push(select);
    return el("label", { class: "scan-pick" }, el("span", {}, label), select);
  }

  // Rows the app could not place: each gets a dropdown that ticks the student the professor picks.
  const needsLook = [];
  for (const entry of part.review) {
    const candidates = entry.candidates || [{ student_id: entry.student_id, name: entry.name }];
    const options = candidates.map((candidate) => ({ id: candidate.student_id, name: candidate.name }));
    const top = options[0]?.id || "";
    needsLook.push(
      el("li", {}, el("p", { class: "scan-line" }, `"${entry.line}"`), pickRow("Who is this?", options.length ? options : everyone, top)),
    );
  }
  for (const entry of part.unnamed) {
    needsLook.push(
      el("li", {}, el("p", { class: "scan-line" }, `Row ${entry.row} has no readable name`), pickRow("Who signed row " + entry.row + "?", everyone, "")),
    );
  }
  for (const line of part.unmatched) {
    needsLook.push(el("li", {}, el("p", { class: "scan-line muted" }, `Not matched: "${line}"`)));
  }
  if (part.missing_rows.length) {
    needsLook.push(el("li", {}, el("p", { class: "scan-line muted" }, `Row numbers not seen on the photo: ${part.missing_rows.join(", ")}`)));
  }

  const readIds = new Set(part.matched.map((entry) => entry.student_id));
  const rows = everyone.map((person) => {
    const input = checkbox(person.id, readIds.has(person.id));
    return el(
      "li",
      {},
      el(
        "label",
        { class: "scan-check" },
        input,
        el("span", {}, person.name),
        readIds.has(person.id) ? el("span", { class: "muted" }, "read from sheet") : null,
      ),
    );
  });

  // The dropdowns drive the checkboxes: picking a student ticks them.
  for (const select of picks) {
    select.addEventListener("change", () => {
      const input = boxes.get(select.value);
      if (input) input.checked = true;
    });
    if (select.value && boxes.has(select.value)) boxes.get(select.value).checked = true;
  }

  return {
    label: part.label,
    checkedIds: () => [...boxes].filter(([, input]) => input.checked).map(([id]) => id),
    node: el(
      "section",
      { class: "scan-session" },
      el("h3", {}, title),
      needsLook.length ? el("ul", { class: "list scan-look" }, needsLook) : el("p", { class: "muted" }, "Every row was read."),
      el("h4", {}, "Attendance list"),
      el("ul", { class: "list scan-list" }, rows),
    ),
  };
}
