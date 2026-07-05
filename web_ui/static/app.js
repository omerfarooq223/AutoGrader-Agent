const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");

const railLabels = {
  waiting: "Waiting",
  active: "In progress",
  complete: "Complete",
  ready: "Ready",
  error: "Needs attention",
};

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    tabs.forEach((item) => item.classList.remove("active"));
    panels.forEach((panel) => panel.classList.remove("active-panel"));
    tab.classList.add("active");
    document.getElementById(target).classList.add("active-panel");
    document.body.dataset.mode = target;
  });
});

function setRailStep(id, state, label = railLabels[state] || state) {
  const step = document.querySelector(`[data-rail="${id}"]`);
  if (!step) return;
  step.classList.remove("is-waiting", "is-active", "is-complete", "is-error");
  const className = state === "ready" ? "is-active" : `is-${state}`;
  step.classList.add(className);
  const small = step.querySelector("small");
  if (small) small.textContent = label;
}

function setSectionState(id, active) {
  const section = document.querySelector(`[data-step-section="${id}"]`);
  if (section) section.classList.toggle("is-active", active);
}

function setIdleVisibility(prefix, hidden) {
  const panel = document.getElementById(`${prefix}Result`);
  const idle = panel?.parentElement.querySelector(".idle-result");
  if (idle) idle.hidden = hidden;
}

function updateGradingReadiness() {
  const form = document.getElementById("gradingForm");
  const submissions = form.elements.submissions_zip.files.length > 0;
  const brief = form.elements.assignment_brief.files.length > 0;
  const ready = submissions && brief;
  const rubricStatus = document.getElementById("rubricStatus");
  const generateButton = document.getElementById("generateRubricButton");
  const validateButton = document.getElementById("validateRubricButton");
  const preview = document.getElementById("rubricPreview");
  const hasRubric = form.elements.manual_rubric.value.trim().length > 0;

  setRailStep("grading-upload", ready ? "complete" : "active", ready ? "Complete" : "In progress");
  setRailStep("grading-rubric", ready ? "ready" : "waiting", ready ? "Ready" : "Waiting");
  setRailStep("grading-answer", ready ? "ready" : "waiting", ready ? "Optional" : "Waiting");
  setRailStep("grading-grade", ready ? "ready" : "waiting", ready ? "Ready" : "Waiting");
  setRailStep("grading-results", "waiting");

  setSectionState("grading-upload", !ready);
  setSectionState("grading-rubric", ready);

  if (rubricStatus) {
    if (hasRubric && preview && !preview.hidden) {
      rubricStatus.textContent = "Rubric changed. Validate & Preview again to refresh what AutoGrader will use.";
      rubricStatus.classList.remove("status-success");
      rubricStatus.classList.add("status-info");
    } else {
      rubricStatus.textContent = brief
        ? "Rubric step is ready. Generate a rubric from the brief, or paste your own and validate it."
        : "Upload an assignment brief in Step 1 to generate a rubric.";
      rubricStatus.classList.toggle("status-success", ready && hasRubric);
      rubricStatus.classList.toggle("status-info", !ready || !hasRubric);
    }
  }

  if (generateButton) {
    generateButton.disabled = !brief;
  }
  if (validateButton) {
    validateButton.disabled = !hasRubric;
  }
}

function updateVivaReadiness() {
  const form = document.getElementById("vivaForm");
  const hasProject = form.elements.project_document.files.length > 0;

  setRailStep("viva-upload", hasProject ? "complete" : "active", hasProject ? "Complete" : "In progress");
  setRailStep("viva-settings", hasProject ? "ready" : "waiting", hasProject ? "Ready" : "Waiting");
  setRailStep("viva-results", "waiting");
  setSectionState("viva-upload", !hasProject);
  setSectionState("viva-settings", hasProject);
}

document
  .querySelectorAll("#gradingForm input, #gradingForm textarea")
  .forEach((input) => {
    input.addEventListener("input", updateGradingReadiness);
    input.addEventListener("change", updateGradingReadiness);
  });

document
  .querySelectorAll("#vivaForm input, #vivaForm select")
  .forEach((input) => input.addEventListener("change", updateVivaReadiness));

function setProgress(prefix, job) {
  const result = document.getElementById(`${prefix}Result`);
  const message = document.getElementById(`${prefix}Message`);
  const percent = document.getElementById(`${prefix}Percent`);
  const bar = document.getElementById(`${prefix}Bar`);
  result.hidden = false;
  setIdleVisibility(prefix, true);

  const value = Math.max(0, Math.min(job.progress || 0, 100));
  message.textContent = job.message || "Working";
  percent.textContent = `${value}%`;
  bar.style.width = `${value}%`;

  if (prefix === "grading") {
    updateGradingRail(job);
  } else {
    updateVivaRail(job);
  }
}

function updateGradingRail(job) {
  const progress = job.progress || 0;

  setRailStep("grading-upload", progress >= 12 ? "complete" : "active");
  setRailStep("grading-rubric", progress >= 24 ? "complete" : progress >= 18 ? "active" : "waiting");
  setRailStep("grading-answer", progress >= 35 ? "complete" : progress >= 24 ? "active" : "waiting");
  setRailStep("grading-grade", progress >= 92 ? "complete" : progress >= 45 ? "active" : "waiting");
  setRailStep("grading-results", progress >= 92 ? "active" : "waiting", progress >= 92 ? "Writing report" : "Waiting");

  setSectionState("grading-grade", progress >= 35 && progress < 92);
  setSectionState("grading-results", progress >= 92);

  if (job.status === "done") {
    setRailStep("grading-results", "complete");
    setSectionState("grading-results", true);
  }
  if (job.status === "error") {
    setRailStep("grading-results", "error");
    setSectionState("grading-results", true);
  }
}

function updateVivaRail(job) {
  const progress = job.progress || 0;
  setRailStep("viva-upload", progress >= 20 ? "complete" : "active");
  setRailStep("viva-settings", progress >= 55 ? "complete" : progress >= 20 ? "active" : "waiting");
  setRailStep("viva-results", progress >= 55 ? "active" : "waiting");
  setSectionState("viva-results", progress >= 55);

  if (job.status === "done") setRailStep("viva-results", "complete");
  if (job.status === "error") setRailStep("viva-results", "error");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollJob(jobId, prefix, onDone) {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) {
      throw new Error("Could not read job status.");
    }
    const job = await response.json();
    setProgress(prefix, job);

    if (job.status === "done") {
      onDone(job);
      return;
    }
    if (job.status === "error") {
      throw new Error(job.error || "The job failed.");
    }
    await sleep(1200);
  }
}

function setRubricStatus(message, state = "info") {
  const rubricStatus = document.getElementById("rubricStatus");
  if (!rubricStatus) return;
  rubricStatus.textContent = message;
  rubricStatus.classList.toggle("status-success", state === "success");
  rubricStatus.classList.toggle("status-info", state !== "success");
}

function formatScore(value) {
  const number = Number(value || 0);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function renderRubricPreview(payload) {
  const preview = document.getElementById("rubricPreview");
  const summary = document.getElementById("rubricSummary");
  const criteriaRoot = document.getElementById("rubricCriteria");
  const criteria = Array.isArray(payload.criteria) ? payload.criteria : [];
  const total = Number(payload.total || 0);

  if (!preview || !summary || !criteriaRoot) return;

  summary.textContent = `${criteria.length} criteria - ${formatScore(total)} total marks`;
  criteriaRoot.innerHTML = criteria.map((item, index) => `
    <article class="rubric-criterion">
      <div class="rubric-criterion-head">
        <span class="badge">C${index + 1}</span>
        <strong>${escapeHtml(item.name || "Criterion")}</strong>
        <span class="score-pill">${escapeHtml(formatScore(item.max_score))} marks</span>
      </div>
      <p>${escapeHtml(item.description || "No description provided.")}</p>
    </article>
  `).join("");
  preview.hidden = criteria.length === 0;
}

async function validateRubric({ silent = false } = {}) {
  const form = document.getElementById("gradingForm");
  const button = document.getElementById("validateRubricButton");
  const textarea = document.getElementById("manualRubric");

  if (!textarea.value.trim()) {
    setRubricStatus("Paste or generate a rubric before validating it.");
    return null;
  }

  if (button) button.disabled = true;
  if (!silent) setRubricStatus("Validating rubric and building preview...");

  try {
    const response = await fetch("/api/rubric/validate", {
      method: "POST",
      body: new FormData(form),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Rubric validation failed.");
    }

    textarea.value = payload.rubric || textarea.value;
    renderRubricPreview(payload);
    setRailStep("grading-rubric", "complete", "Validated");
    setRubricStatus("Rubric validated. Preview below shows exactly what AutoGrader will use.", "success");
    return payload;
  } catch (error) {
    setRailStep("grading-rubric", "error");
    setRubricStatus(error.message || "Rubric validation failed.");
    return null;
  } finally {
    if (button) button.disabled = !textarea.value.trim();
  }
}

async function pollRubricJob(jobId) {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) {
      throw new Error("Could not read rubric generation status.");
    }
    const job = await response.json();
    setRubricStatus(`${job.message || "Generating rubric"} (${Math.max(0, Math.min(job.progress || 0, 100))}%)`);

    if (job.status === "done") return job;
    if (job.status === "error") throw new Error(job.error || "Rubric generation failed.");
    await sleep(1200);
  }
}

async function generateRubric() {
  const form = document.getElementById("gradingForm");
  const button = document.getElementById("generateRubricButton");
  const textarea = document.getElementById("manualRubric");

  if (!form.elements.assignment_brief.files.length) {
    setRubricStatus("Upload an assignment brief before generating a rubric.");
    return;
  }

  button.disabled = true;
  setRailStep("grading-rubric", "active", "Generating");
  setSectionState("grading-rubric", true);
  setRubricStatus("Uploading brief for rubric generation (5%)");

  try {
    const response = await fetch("/api/rubric", {
      method: "POST",
      body: new FormData(form),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not start rubric generation.");
    }

    const job = await pollRubricJob(payload.job_id);
    textarea.value = job.result?.rubric || "";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await validateRubric({ silent: true });
    setRailStep("grading-rubric", "complete", "Review");
    setRailStep("grading-answer", "ready", "Optional");
    setRailStep("grading-grade", "ready", "Ready");
    setRubricStatus("Rubric generated and previewed. Review or edit it here, then validate again if you make changes.", "success");
  } catch (error) {
    setRailStep("grading-rubric", "error");
    setRubricStatus(error.message || "Rubric generation failed.");
  } finally {
    button.disabled = !form.elements.assignment_brief.files.length;
  }
}

async function submitJob(form, endpoint, prefix, onDone) {
  const button = form.querySelector("button[type='submit']");
  const output = document.getElementById(`${prefix}Output`);
  output.innerHTML = "";
  button.disabled = true;

  try {
    setProgress(prefix, { message: "Uploading files", progress: 3 });
    document.querySelector(`[data-step-section="${prefix}-results"]`)?.scrollIntoView({ block: "nearest" });
    const response = await fetch(endpoint, {
      method: "POST",
      body: new FormData(form),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Upload failed.");
    }
    await pollJob(payload.job_id, prefix, onDone);
  } catch (error) {
    output.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
    if (prefix === "grading") setRailStep("grading-results", "error");
    if (prefix === "viva") setRailStep("viva-results", "error");
  } finally {
    button.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.getElementById("gradingForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitJob(event.currentTarget, "/api/grade", "grading", (job) => {
    const result = job.result || {};
    const penalty = Number(result.plagiarism_penalty || 0);
    const threshold = Number(result.plagiarism_threshold || 65);
    const formatNumber = (value) => Number.isInteger(value) ? String(value) : value.toFixed(1);
    const policyText = penalty > 0
      ? `Threshold ${formatNumber(threshold)}% with ${formatNumber(penalty)} mark(s) deducted per flagged student.`
      : `Threshold ${formatNumber(threshold)}% with reporting only; no marks deducted.`;
    document.getElementById("gradingOutput").innerHTML = `
      <div class="status-msg status-success">Report ready. ${escapeHtml(result.students || 0)} student(s) graded and ${escapeHtml(result.plagiarism_flags || 0)} similarity flag(s) found. ${escapeHtml(policyText)}</div>
      <a class="download" href="${escapeHtml(result.download_url)}">Download Excel report</a>
    `;
  });
});

document.getElementById("generateRubricButton").addEventListener("click", generateRubric);
document.getElementById("validateRubricButton").addEventListener("click", () => validateRubric());

document.getElementById("vivaForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitJob(event.currentTarget, "/api/viva", "viva", (job) => {
    const result = job.result || {};
    const questions = Array.isArray(result.questions) ? result.questions : [];
    const items = questions.map((item, index) => `
      <li class="question-card">
        <div class="question-meta">
          <span class="badge">Q${index + 1}</span>
          <span class="badge">${escapeHtml(item.category || "General")}</span>
          <span class="badge">${escapeHtml(item.difficulty || "Mixed")}</span>
        </div>
        <p>${escapeHtml(item.question || "")}</p>
        ${item.what_to_listen_for ? `<div class="hint"><strong>Listen for:</strong> ${escapeHtml(item.what_to_listen_for)}</div>` : ""}
      </li>
    `).join("");

    document.getElementById("vivaOutput").innerHTML = `
      <h3>${escapeHtml(result.project_name || "Viva Questions")}</h3>
      ${result.notes ? `<p class="hint">${escapeHtml(result.notes)}</p>` : ""}
      ${items ? `<ol class="question-list">${items}</ol>` : "<p>No questions were generated.</p>"}
    `;
  });
});

document.querySelector('[data-reset="grading"]').addEventListener("click", () => {
  document.getElementById("gradingForm").reset();
  document.getElementById("gradingResult").hidden = true;
  document.getElementById("gradingOutput").innerHTML = "";
  document.getElementById("rubricPreview").hidden = true;
  document.getElementById("rubricCriteria").innerHTML = "";
  setIdleVisibility("grading", false);
  updateGradingReadiness();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

updateGradingReadiness();
updateVivaReadiness();
