const config = window.APP_CONFIG || {};
const $ = (id) => document.getElementById(id);
const configured = config.apiUrl && !config.apiUrl.includes("YOUR_") && config.cognitoClientId && !config.cognitoClientId.includes("YOUR_");
const previewMode = new URLSearchParams(location.search).get("preview") === "1";
let resumeText = "";
let conversationId = localStorage.getItem("conversation_id") || crypto.randomUUID();
localStorage.setItem("conversation_id", conversationId);

if (!configured && !previewMode) $("setup-warning").classList.remove("hidden");
if (previewMode) $("preview-badge").classList.remove("hidden");

function base64Url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sha256(value) {
  return base64Url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

function randomVerifier() {
  const bytes = new Uint8Array(48);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

function token() {
  if (previewMode) return "prototype-preview-token";
  const value = sessionStorage.getItem("id_token");
  const expires = Number(sessionStorage.getItem("token_expires") || 0);
  if (!value || Date.now() >= expires) return null;
  return value;
}

function updateAuthUi() {
  if (previewMode) {
    $("auth-status").textContent = "Signed in";
    $("login-button").classList.add("hidden");
    $("logout-button").classList.add("hidden");
    $("signup-button").classList.remove("hidden");
    return;
  }
  const signedIn = Boolean(token());
  $("auth-status").textContent = signedIn ? "Signed in securely" : "Not signed in";
  $("login-button").classList.toggle("hidden", signedIn);
  $("signup-button").classList.toggle("hidden", signedIn);
  $("logout-button").classList.toggle("hidden", !signedIn);
}

const previewStatuses = [
  {company: "Jane Street", role: "Software Engineer Intern", status: "INTERVIEW", status_at: "2026-09-06T09:30:00Z"},
  {company: "Stripe", role: "Data Science Intern", status: "ASSESSMENT", status_at: "2026-09-05T04:10:00Z"},
  {company: "Canva", role: "Backend Engineering Intern", status: "APPLIED", status_at: "2026-09-03T12:00:00Z"},
  {company: "Airbnb", role: "Software Engineer Intern", status: "REJECTED", status_at: "2026-09-02T08:15:00Z"},
  {company: "Datadog", role: "Product Analytics Intern", status: "OFFER", status_at: "2026-09-01T06:45:00Z"}
];

const previewJobs = [
  {company: "Cloudflare", role: "Software Engineer Intern", location: "Singapore · Skills 28/30 · Experience 22/25 · Projects 14/15", priority_score: 91, eligibility: "ELIGIBLE"},
  {company: "TikTok", role: "Backend Software Engineer Intern", location: "Singapore · Skills 26/30 · Experience 21/25 · Projects 13/15", priority_score: 87, eligibility: "ELIGIBLE"},
  {company: "Grab", role: "Data Platform Intern", location: "Singapore · Skills 25/30 · Experience 20/25 · Projects 13/15", priority_score: 84, eligibility: "ELIGIBLE"},
  {company: "Wise", role: "Software Engineering Intern", location: "Singapore · Skills 24/30 · Experience 20/25 · Projects 12/15", priority_score: 81, eligibility: "ELIGIBLE"},
  {company: "Shopee", role: "Machine Learning Intern", location: "Singapore · Skills 23/30 · Experience 18/25 · Projects 13/15", priority_score: 78, eligibility: "ELIGIBLE"}
];

function previewResponse(task) {
  const result = {
    email_tracker: {connected: true, application_statuses: []},
    fit_agent: {candidate_ready: false, ranked_jobs: []},
    excel_report: {generated: false}
  };
  if (task === "CONNECT_GMAIL") return {message: "Gmail connection flow completed for the prototype preview.", result};
  if (task === "EMAIL_SYNC") {
    result.email_tracker.application_statuses = previewStatuses;
    return {message: "Processed 20 messages and identified 5 application updates.", result};
  }
  if (task === "EMAIL_STATUS") {
    result.email_tracker.application_statuses = previewStatuses;
    return {message: "Loaded 5 application statuses from the tracker.", result};
  }
  if (task === "EXPORT_DAILY") {
    result.email_tracker.application_statuses = previewStatuses;
    result.excel_report.generated = true;
    return {message: "Prepared the application tracker workbook for download.", result};
  }
  result.fit_agent = {candidate_ready: true, ranked_jobs: previewJobs};
  return {message: "Compared the candidate profile with five internship opportunities and ranked them by priority.", result};
}

async function beginCognito(path = "/oauth2/authorize") {
  if (!configured) return alert("Configure the deployed API and Cognito values first.");
  const verifier = randomVerifier();
  sessionStorage.setItem("pkce_verifier", verifier);
  const challenge = await sha256(verifier);
  const params = new URLSearchParams({
    client_id: config.cognitoClientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: config.redirectUri,
    code_challenge_method: "S256",
    code_challenge: challenge
  });
  location.assign(`${config.cognitoDomain}${path}?${params}`);
}

async function login() { return beginCognito("/oauth2/authorize"); }
async function signup() { return beginCognito("/signup"); }

function openOnboarding() {
  const accountReady = previewMode || Boolean(token());
  $("account-preview-note").classList.toggle("hidden", !previewMode);
  $("gmail-preview-note").classList.toggle("hidden", !previewMode);
  $("account-help").textContent = accountReady
    ? (previewMode ? "Preview the account creation experience." : "Your account is signed in securely.")
    : "Account creation is securely handled by Amazon Cognito.";
  $("create-account-button").textContent = previewMode ? "Preview sign-up" : (accountReady ? "Account ready" : "Create secure account");
  $("create-account-button").disabled = accountReady && !previewMode;
  if (accountReady) {
    $("account-step-number").textContent = "✓";
    $("account-step-number").classList.add("complete");
  }
  $("gmail-onboarding-step").classList.toggle("disabled", !accountReady);
  $("modal-connect-gmail").disabled = !accountReady;
  $("modal-connect-gmail").textContent = "Continue to Gmail";
  $("onboarding-dialog").showModal();
}

async function finishLogin() {
  const params = new URLSearchParams(location.search);
  const code = params.get("code");
  if (!code) return;
  const verifier = sessionStorage.getItem("pkce_verifier");
  if (!verifier) throw new Error("The sign-in verifier is missing. Please sign in again.");
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.cognitoClientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier
  });
  const response = await fetch(`${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: {"content-type": "application/x-www-form-urlencoded"},
    body
  });
  if (!response.ok) throw new Error("Sign-in could not be completed.");
  const data = await response.json();
  sessionStorage.setItem("id_token", data.id_token);
  sessionStorage.setItem("token_expires", String(Date.now() + data.expires_in * 1000 - 30000));
  sessionStorage.removeItem("pkce_verifier");
  history.replaceState({}, document.title, location.pathname);
}

function logout() {
  sessionStorage.clear();
  const params = new URLSearchParams({client_id: config.cognitoClientId, logout_uri: config.logoutUri});
  location.assign(`${config.cognitoDomain}/logout?${params}`);
}

async function extractPdf(file) {
  if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) throw new Error("Please choose a PDF file.");
  if (file.size > 8 * 1024 * 1024) throw new Error("Please use a PDF smaller than 8 MB.");
  const pdfjs = await import("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.8.69/pdf.min.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.8.69/pdf.worker.min.mjs";
  const pdf = await pdfjs.getDocument({data: new Uint8Array(await file.arrayBuffer())}).promise;
  const pages = [];
  for (let i = 1; i <= Math.min(pdf.numPages, 8); i += 1) {
    const page = await pdf.getPage(i);
    const content = await page.getTextContent();
    pages.push(content.items.map((item) => item.str).join(" "));
  }
  const text = pages.join("\n").replace(/\s+/g, " ").trim();
  if (text.length < 100) throw new Error("This PDF has little selectable text. Export your resume as a text-based PDF and retry.");
  return text.slice(0, 60000);
}

function addMessage(kind, text, actionUrl = null) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${kind === "user" ? "user-message" : "assistant-message"}`;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = kind === "user" ? "You" : "Compass";
  const pre = document.createElement("pre");
  pre.textContent = text;
  wrapper.append(label, pre);
  if (actionUrl) {
    const link = document.createElement("a");
    link.href = actionUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = actionUrl.includes("accounts.google.com") ? "Authorize Gmail" : "Open download";
    wrapper.append(link);
  }
  $("conversation").append(wrapper);
  $("conversation").scrollTop = $("conversation").scrollHeight;
  return wrapper;
}

function addTyping() {
  const node = addMessage("assistant", "");
  node.querySelector("pre").innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  return node;
}

function extractResult(text) {
  const marker = text.lastIndexOf("ORCHESTRATOR_RESULT_V1");
  const source = marker >= 0 ? text.slice(marker) : text;
  const first = source.indexOf("{");
  if (first < 0) return null;
  let depth = 0, inString = false, escaped = false;
  for (let i = first; i < source.length; i += 1) {
    const ch = source[i];
    if (escaped) { escaped = false; continue; }
    if (ch === "\\") { escaped = true; continue; }
    if (ch === '"') { inString = !inString; continue; }
    if (inString) continue;
    if (ch === "{") depth += 1;
    if (ch === "}" && --depth === 0) {
      try { return JSON.parse(source.slice(first, i + 1)); } catch { return null; }
    }
  }
  return null;
}

function renderResult(result) {
  if (!result) return;
  const jobs = result.fit_agent?.ranked_jobs || [];
  const statuses = result.email_tracker?.application_statuses || [];
  if (!jobs.length && !statuses.length) return;
  $("results-section").classList.remove("hidden");
  const counts = {
    tracked: statuses.length,
    interviews: statuses.filter(x => x.status === "INTERVIEW").length,
    offers: statuses.filter(x => x.status === "OFFER").length,
    ranked: jobs.length
  };
  $("summary-cards").innerHTML = "";
  for (const [label, value] of Object.entries(counts)) {
    const card = document.createElement("div");
    card.className = "summary-card";
    const strong = document.createElement("strong"); strong.textContent = value;
    const span = document.createElement("span"); span.textContent = label[0].toUpperCase() + label.slice(1);
    card.append(strong, span); $("summary-cards").append(card);
  }
  $("pipeline").innerHTML = "";
  statuses.forEach((application) => {
    const row = document.createElement("div"); row.className = "pipeline-row";
    const company = document.createElement("strong"); company.textContent = application.company || "Unknown company";
    const role = document.createElement("span"); role.className = "pipeline-role"; role.textContent = application.role || "Role not identified";
    const status = document.createElement("span"); status.className = "status-pill"; status.textContent = application.status || "NEEDS_REVIEW";
    const date = document.createElement("span"); date.className = "pipeline-date";
    date.textContent = application.status_at ? new Date(application.status_at).toLocaleDateString() : "Date unavailable";
    row.append(company, role, status, date); $("pipeline").append(row);
  });
  $("rankings").innerHTML = "";
  jobs.forEach((job, index) => {
    const card = document.createElement("div"); card.className = "ranking-card";
    const rank = document.createElement("div"); rank.className = "rank"; rank.textContent = `#${index + 1}`;
    const info = document.createElement("div");
    const title = document.createElement("h3"); title.textContent = `${job.company || "Company"} · ${job.role || "Internship"}`;
    const detail = document.createElement("p"); detail.textContent = job.location || job.next_action || "Official role verified";
    info.append(title, detail);
    const score = document.createElement("div"); score.className = "score"; score.textContent = `${job.priority_score ?? job.fit_score ?? "—"}/100`;
    const status = document.createElement("span"); status.className = "status-pill"; status.textContent = job.eligibility || job.status || "ELIGIBLE";
    card.append(rank, info, score, status); $("rankings").append(card);
  });
  $("results-section").scrollIntoView({behavior: "smooth", block: "start"});
}

async function sendPrompt(message, includeResume = false, task = "AUTO") {
  if (!token()) { await login(); return; }
  addMessage("user", message);
  const typing = addTyping();
  $("request-status").textContent = "The orchestrator is working. Verified job searches can take up to a few minutes.";
  document.querySelectorAll("button").forEach((b) => b.disabled = true);
  try {
    if (previewMode) {
      await new Promise((resolve) => setTimeout(resolve, 650));
      const preview = previewResponse(task === "AUTO" ? "DISCOVER_AND_RANK" : task);
      typing.remove();
      addMessage("assistant", preview.message);
      renderResult(preview.result);
      return;
    }
    const response = await fetch(`${config.apiUrl}/chat`, {
      method: "POST",
      headers: {"content-type": "application/json", authorization: `Bearer ${token()}`},
      body: JSON.stringify({message, conversationId, resumeText: includeResume ? resumeText : "", task})
    });
    const data = await response.json();
    if (response.status === 401) { sessionStorage.clear(); updateAuthUi(); throw new Error("Your session expired. Please sign in again."); }
    if (!response.ok) throw new Error(data.error || "The request failed.");
    let job = data;
    for (let attempt = 0; attempt < 170 && !["COMPLETED", "FAILED"].includes(job.status); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const poll = await fetch(`${config.apiUrl}/requests/${encodeURIComponent(data.requestId)}`, {
        headers: {authorization: `Bearer ${token()}`}
      });
      job = await poll.json();
      if (poll.status === 401) { sessionStorage.clear(); updateAuthUi(); throw new Error("Your session expired. Please sign in again."); }
      if (!poll.ok) throw new Error(job.error || "Could not read the request result.");
      $("request-status").textContent = job.status === "RUNNING" ? "The orchestrator is verifying sources and calculating rankings…" : "Your request is queued…";
    }
    if (job.status === "FAILED") throw new Error(job.error || "The agent request failed.");
    if (job.status !== "COMPLETED") throw new Error("The request is still running. Please retry in a moment.");
    const result = job.result || {};
    typing.remove();
    const url = result.message.match(/https:\/\/accounts\.google\.com\/[^\s<>)\]]+/)?.[0]
      || result.message.match(/https:\/\/[^\s<>)\]]+\.xlsx[^\s<>)\]]*/)?.[0]
      || null;
    addMessage("assistant", result.message, url);
    renderResult(extractResult(result.message));
  } catch (error) {
    typing.remove();
    addMessage("assistant", `Sorry, ${error.message}`);
  } finally {
    document.querySelectorAll("button").forEach((b) => b.disabled = false);
    $("request-status").textContent = "";
  }
}

$("login-button").addEventListener("click", login);
$("signup-button").addEventListener("click", openOnboarding);
$("logout-button").addEventListener("click", logout);
$("onboarding-close").addEventListener("click", () => $("onboarding-dialog").close());
$("onboarding-dialog").addEventListener("click", (event) => {
  if (event.target === $("onboarding-dialog")) $("onboarding-dialog").close();
});
$("create-account-button").addEventListener("click", () => {
  if (previewMode) {
    addMessage("assistant", "Account creation is ready to continue in the prototype preview.");
    $("onboarding-dialog").close();
    return;
  }
  signup();
});
$("modal-connect-gmail").addEventListener("click", () => {
  $("onboarding-dialog").close();
  sendPrompt("Connect my Gmail account with read-only access.", false, "CONNECT_GMAIL");
});
$("resume-input").addEventListener("change", async (event) => {
  const file = event.target.files?.[0]; if (!file) return;
  $("resume-name").textContent = "Reading PDF…";
  try {
    resumeText = await extractPdf(file);
    $("resume-name").textContent = `${file.name} · ${resumeText.length.toLocaleString()} characters ready`;
    $("resume-badge").textContent = "Resume ready";
    $("resume-badge").classList.add("ready");
  } catch (error) {
    resumeText = ""; $("resume-name").textContent = error.message;
  }
});
const drop = $("resume-drop");
["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.add("dragover"); }));
["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.remove("dragover"); }));
drop.addEventListener("drop", (event) => { const file = event.dataTransfer.files?.[0]; if (file) { const transfer = new DataTransfer(); transfer.items.add(file); $("resume-input").files = transfer.files; $("resume-input").dispatchEvent(new Event("change")); } });

$("rank-button").addEventListener("click", () => {
  if (!resumeText && !previewMode) return alert("Upload a text-based PDF resume first.");
  const prompt = `Find and rank at least five currently open ${$("direction").value} internships in ${$("location").value}. My graduation date is ${$("graduation").value} and my availability is ${$("availability").value}. Verify official job descriptions, apply hard eligibility gates, show the score breakdown, and rank by priority. Do not scan email.`;
  sendPrompt(prompt, true, "DISCOVER_AND_RANK");
});
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.task === "CONNECT_GMAIL") { openOnboarding(); return; }
  sendPrompt(button.dataset.prompt, false, button.dataset.task || "AUTO");
}));
$("chat-form").addEventListener("submit", (event) => { event.preventDefault(); const value = $("chat-input").value.trim(); if (value) { $("chat-input").value = ""; sendPrompt(value, Boolean(resumeText)); } });

try { await finishLogin(); } catch (error) { addMessage("assistant", error.message); }
updateAuthUi();
