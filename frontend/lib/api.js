// Same-origin "/api" when deployed (Vercel serves the backend under /api); the local
// FastAPI server when developing on this machine.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined" && !["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "/api"
    : "http://localhost:8000/api");

export async function fetchApi(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const token = typeof window !== "undefined" ? sessionStorage.getItem("app_auth_token") : null;
  
  const defaultHeaders = {
    "Content-Type": "application/json",
    ...(token ? { "Authorization": `Bearer ${token}` } : {})
  };

  const response = await fetch(url, {
    ...options,
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  });

  if (response.status === 401 && typeof window !== "undefined" && !endpoint.startsWith("/auth/") && window.location.pathname !== "/login") {
    sessionStorage.removeItem("app_auth_token");
    sessionStorage.removeItem("app_user_data");
    window.location.href = "/login";
  }

  if (!response.ok) {
    const errorText = await response.text();
    let message = errorText;
    try {
      const detail = JSON.parse(errorText).detail;
      if (detail) message = typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch {}
    throw new Error(message || `API Error: ${response.status}`);
  }

  if (response.status === 204) return null;
  return response.json();
}

// Discovery runs in the background on the server; start one and poll until it finishes.
// File downloads need the auth header, so a plain <a href> can't be used.
async function downloadFile(endpoint) {
  const token = typeof window !== "undefined" ? sessionStorage.getItem("app_auth_token") : null;
  const response = await fetch(`${API_BASE}${endpoint}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) {
    let message = `Download failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? match[1] : "document";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// Discovery runs in the background on the server. startDiscovery() kicks one off and
// tells the TopBar (which tracks progress); pages listen for "discovery-finished".
export async function startDiscovery() {
  const res = await fetchApi("/jobs/scrape", { method: "POST" });
  if (typeof window !== "undefined") window.dispatchEvent(new Event("discovery-started"));
  return res;
}

// Advance background work (search, document preparation). Fire-and-forget, one at a time:
// on serverless hosts this request is what runs the next step.
let pumping = false;
export function pumpJobs() {
  if (pumping || typeof window === "undefined") return;
  pumping = true;
  fetchApi("/jobs/work", { method: "POST" })
    .catch(() => {})
    .finally(() => { pumping = false; });
}

export function describeRun(run) {
  if (!run) return { tone: "info", text: "Discovery is still running in the background." };
  if (run.status === "failed") return { tone: "danger", text: `Discovery failed: ${run.error_message || "unknown error"}` };
  return {
    tone: "success",
    text: `Found ${run.jobs_found} jobs (${run.jobs_new} new), ${run.jobs_verified || 0} verified on company sites. ` +
      `${run.matches_created} analysed, ${run.applications_drafted} added to your review queue.`,
  };
}

// Server feature flags (cached for the session).
let configPromise = null;
export function getConfig() {
  if (!configPromise) {
    configPromise = fetch(API_BASE.replace(/\/api$/, "") + "/api/config")
      .then(r => r.json())
      .catch(() => ({ assisted_apply: true, latex_pdf: true }));
  }
  return configPromise;
}

export const api = {
  login: (email, password) => fetchApi("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  register: (name, email, password) => fetchApi("/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) }),
  getCurrentUser: () => fetchApi("/auth/me"),
  getProfile: () => fetchApi("/profile/"),
  updateProfile: (data) => fetchApi("/profile/", { method: "PUT", body: JSON.stringify(data) }),
  uploadResume: (file) => {
    const formData = new FormData();
    formData.append("file", file);
    const token = typeof window !== "undefined" ? sessionStorage.getItem("app_auth_token") : null;
    return fetch(`${API_BASE}/profile/upload-resume`, {
      method: "POST",
      headers: {
        ...(token ? { "Authorization": `Bearer ${token}` } : {})
      },
      body: formData
    }).then(res => res.json());
  },
  autoImportLinks: (data) => fetchApi("/profile/auto-import", { method: "POST", body: JSON.stringify(data) }),
  getMatchedJobs: (minScore = 0, includeSkipped = false) => fetchApi(`/jobs/matched?min_score=${minScore}&include_skipped=${includeSkipped}`),
  triggerLiveScrape: () => fetchApi("/jobs/scrape", { method: "POST" }),
  getScrapeRuns: (limit = 5) => fetchApi(`/jobs/runs?limit=${limit}`),
  getCompanies: () => fetchApi("/companies/"),
  addCompany: (name, careersUrl) => fetchApi("/companies/", { method: "POST", body: JSON.stringify({ name, careers_url: careersUrl }) }),
  checkCompany: (id) => fetchApi(`/companies/${id}/check`, { method: "POST" }),
  setCompanyActive: (id, isActive) => fetchApi(`/companies/${id}?is_active=${isActive}`, { method: "PATCH" }),
  removeCompany: (id) => fetchApi(`/companies/${id}`, { method: "DELETE" }),
  getApplications: (status) => fetchApi(`/applications/${status ? `?status=${status}` : ""}`),
  updateApplicationStatus: (id, status) => fetchApi(`/applications/${id}/status`, { method: "PUT", body: JSON.stringify({ status }) }),
  getDownloadResumeUrl: (id) => `${API_BASE}/applications/${id}/download-resume`,
  downloadDocument: (id, format = "pdf") => downloadFile(`/applications/${id}/download-resume?format=${format}`),
  editApplication: (id, data) => fetchApi(`/applications/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  startAssistedApply: (id) => fetchApi(`/applications/${id}/assist-apply`, { method: "POST" }),
  getApplyStatus: (id) => fetchApi(`/applications/${id}/apply-status`),
  getApplicationLogs: (id) => fetchApi(`/applications/${id}/logs`),
  getEmailAccount: () => fetchApi("/email/account"),
  connectEmail: (data) => fetchApi("/email/account", { method: "PUT", body: JSON.stringify(data) }),
  disconnectEmail: () => fetchApi("/email/account", { method: "DELETE" }),
  syncInbox: () => fetchApi("/email/sync", { method: "POST" }),
  getInbox: () => fetchApi("/email/inbox"),
  getFollowUps: () => fetchApi("/email/follow-ups"),
  findContacts: (id) => fetchApi(`/email/applications/${id}/contacts`),
  draftEmail: (id, kind = "initial") => fetchApi(`/email/applications/${id}/draft`, { method: "POST", body: JSON.stringify({ kind }) }),
  sendEmail: (id, data) => fetchApi(`/email/applications/${id}/send`, { method: "POST", body: JSON.stringify(data) }),
  getEmailHistory: (id) => fetchApi(`/email/applications/${id}/history`),
  getTitleSuggestions: (refresh = false) => fetchApi(`/profile/title-suggestions?refresh=${refresh}`),
  getAiKeys: () => fetchApi("/ai-keys/"),
  addAiKey: (data) => fetchApi("/ai-keys/", { method: "POST", body: JSON.stringify(data) }),
  updateAiKey: (id, data) => fetchApi(`/ai-keys/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  testAiKey: (id) => fetchApi(`/ai-keys/${id}/test`, { method: "POST" }),
  deleteAiKey: (id) => fetchApi(`/ai-keys/${id}`, { method: "DELETE" }),
  updateAiSettings: (data) => fetchApi("/ai-keys/settings", { method: "PUT", body: JSON.stringify(data) }),
  getAiStatus: () => fetchApi("/ai-keys/status"),
  prepareApplication: (id) => fetchApi(`/applications/${id}/prepare`, { method: "POST" }),
  getSessions: () => fetchApi("/sessions/"),
  getAnalytics: () => fetchApi("/analytics/overview"),
  getTemplates: () => fetchApi("/templates/"),
};
