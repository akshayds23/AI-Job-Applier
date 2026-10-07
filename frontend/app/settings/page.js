"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2, Clock, FileText, KeyRound, Mail, Plus, Power, RefreshCw, Search, Trash2, XCircle,
} from "lucide-react";
import { api, pumpJobs } from "@/lib/api";
import { Badge, Button, Card, Field, Notice, PageHeader } from "@/components/ui";
import TitleSuggestions from "@/components/TitleSuggestions";

const splitList = (text) => text.split(/[,\n]/).map(s => s.trim()).filter(Boolean);

const PROVIDER_INFO = {
  groq: { name: "Groq", hint: "Free tier available - console.groq.com/keys" },
  gemini: { name: "Google Gemini", hint: "Free tier on projects without billing - aistudio.google.com/apikey" },
  openai: { name: "OpenAI", hint: "Paid - platform.openai.com/api-keys" },
  anthropic: { name: "Anthropic Claude", hint: "Paid - console.anthropic.com" },
};

/* ------------------------------------------------------------------ AI keys */

function KeyStatus({ k }) {
  const live = k.live;
  if (!k.is_enabled) return <Badge>Disabled</Badge>;
  if (k.status === "invalid" || live?.state === "invalid") return <Badge tone="danger" icon={XCircle}>Rejected</Badge>;
  if (live?.state === "cooling") return <Badge tone="warning" icon={Clock}>Cooling down {live.cooldown_seconds}s</Badge>;
  return <Badge tone="success" icon={CheckCircle2}>Ready</Badge>;
}

function AiKeysCard() {
  const [data, setData] = useState(null);
  const [provider, setProvider] = useState("groq");
  const [secret, setSecret] = useState("");
  const [label, setLabel] = useState("");
  const [model, setModel] = useState("");
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(() => api.getAiKeys().then(setData).catch(e => setMessage({ tone: "danger", text: e.message })), []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 10000); // keep cooldown countdowns fresh
    return () => clearInterval(timer);
  }, [load]);

  const add = async (e) => {
    e.preventDefault();
    setAdding(true);
    setMessage(null);
    try {
      await api.addAiKey({ provider, api_key: secret, label, model });
      setSecret(""); setLabel(""); setModel("");
      setMessage({ tone: "success", text: "Key tested and saved." });
      load();
    } catch (err) {
      setMessage({ tone: "danger", text: err.message });
    } finally {
      setAdding(false);
    }
  };

  const act = async (id, fn, okText) => {
    setBusyId(id);
    setMessage(null);
    try {
      const res = await fn();
      if (res && res.ok === false) setMessage({ tone: "danger", text: res.error });
      else if (okText) setMessage({ tone: "success", text: typeof okText === "function" ? okText(res) : okText });
      load();
    } catch (err) {
      setMessage({ tone: "danger", text: err.message });
    } finally {
      setBusyId(null);
    }
  };

  const saveSettings = (patch) => api.updateAiSettings(patch).then(load).catch(e => setMessage({ tone: "danger", text: e.message }));

  const keys = data?.keys || [];
  const defaultModel = data?.providers?.find(p => p.id === provider)?.default_model;

  return (
    <Card title="AI keys" icon={KeyRound} id="ai-keys" style={{ marginBottom: 20, scrollMarginTop: 80 }}>
      <p className="small secondary" style={{ marginBottom: 14 }}>
        AutoApplier uses your own API keys - nothing is provided by us. Add several keys and they are used in rotation
        (more keys means faster searches). When a key hits its rate limit it rests until the provider says it is available again;
        if every key is resting, work waits instead of failing.
      </p>

      {keys.length === 0 ? (
        <Notice tone="warning" style={{ marginBottom: 14 }}>No keys yet - job scoring and resume tailoring need at least one. Groq and Gemini offer free tiers.</Notice>
      ) : (
        <div style={{ marginBottom: 14 }}>
          {keys.map(k => (
            <div key={k.id} className="list-row" style={{ opacity: k.is_enabled ? 1 : 0.6 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row">
                  <strong>{k.label}</strong>
                  <Badge>{PROVIDER_INFO[k.provider]?.name || k.provider}</Badge>
                  <KeyStatus k={k} />
                </div>
                <div className="tiny muted" style={{ marginTop: 2 }}>
                  <span className="kbd">{k.masked}</span> · model {k.model}
                  {k.live && ` · ${k.live.calls} calls, ~${k.live.tokens.toLocaleString()} tokens since restart`}
                </div>
                {k.last_error && k.status === "invalid" && <div className="tiny" style={{ color: "var(--danger-text)", marginTop: 2 }}>{k.last_error}</div>}
              </div>
              <Button size="sm" icon={RefreshCw} loading={busyId === k.id} onClick={() => act(k.id, () => api.testAiKey(k.id), r => r.rate_limited ? "Key works but is rate-limited right now." : "Key works.")}>Test</Button>
              <Button size="sm" variant="ghost" icon={Power} title={k.is_enabled ? "Disable" : "Enable"} onClick={() => act(k.id, () => api.updateAiKey(k.id, { is_enabled: !k.is_enabled }))} />
              <Button size="sm" variant="ghost" icon={Trash2} title="Delete" onClick={() => confirm(`Delete "${k.label}"?`) && act(k.id, () => api.deleteAiKey(k.id), "Key deleted.")} />
            </div>
          ))}
        </div>
      )}

      <form onSubmit={add} className="subtle-panel stack" style={{ gap: 12 }}>
        <div className="field-label">Add a key</div>
        <div className="grid" style={{ gridTemplateColumns: "180px 1fr", gap: 12 }}>
          <Field label="Provider">
            <select className="select" value={provider} onChange={e => setProvider(e.target.value)}>
              {Object.entries(PROVIDER_INFO).map(([id, info]) => <option key={id} value={id}>{info.name}</option>)}
            </select>
          </Field>
          <Field label="API key" hint={PROVIDER_INFO[provider].hint}>
            <input className="input" type="password" autoComplete="off" value={secret} onChange={e => setSecret(e.target.value)} placeholder="Paste the key" />
          </Field>
          <Field label="Label (optional)">
            <input className="input" value={label} onChange={e => setLabel(e.target.value)} placeholder="e.g. Groq personal" />
          </Field>
          <Field label="Model (optional)" hint={defaultModel ? `Default: ${defaultModel}` : undefined}>
            <input className="input" value={model} onChange={e => setModel(e.target.value)} placeholder={defaultModel} />
          </Field>
        </div>
        <div><Button variant="primary" type="submit" icon={Plus} loading={adding} disabled={secret.trim().length < 12}>Test and add key</Button></div>
      </form>

      {data && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 14 }}>
          <Field label="Preferred provider" hint="Used first when it has a ready key.">
            <select className="select" value={data.settings.preferred_provider || ""} onChange={e => saveSettings({ preferred_provider: e.target.value })}>
              <option value="">No preference</option>
              {Object.entries(PROVIDER_INFO).map(([id, info]) => <option key={id} value={id}>{info.name}</option>)}
            </select>
          </Field>
          <Field label="When every key is rate-limited, wait up to" hint="After this, the plain (non-AI) version is used as a last resort.">
            <select className="select" value={data.settings.max_wait_minutes} onChange={e => saveSettings({ max_wait_minutes: Number(e.target.value) })}>
              {[2, 5, 15, 30, 60].map(m => <option key={m} value={m}>{m} minutes</option>)}
            </select>
          </Field>
        </div>
      )}

      {message && <Notice tone={message.tone} style={{ marginTop: 12 }}>{message.text}</Notice>}
    </Card>
  );
}

/* ------------------------------------------------------------- Job search */

function SearchCard() {
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);
  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  useEffect(() => {
    api.getProfile().then(p => setForm({
      roles: (p.target_roles || []).join(", "),
      locations: (p.target_locations || []).join(", "),
      remote: p.remote_preference || "any",
      maxAge: p.max_job_age_days || 30,
      frequency: p.scrape_frequency_hours || 24,
      minScore: p.auto_apply_min_score ?? 55,
      excludedCompanies: (p.excluded_companies || []).join(", "),
      excludedWords: (p.keywords_exclude || []).join(", "),
    })).catch(() => {});
  }, []);

  const save = async () => {
    setSaved(null);
    try {
      await api.updateProfile({
        target_roles: splitList(form.roles),
        target_locations: splitList(form.locations),
        remote_preference: form.remote,
        max_job_age_days: Number(form.maxAge),
        scrape_frequency_hours: Number(form.frequency),
        auto_apply_min_score: Number(form.minScore),
        excluded_companies: splitList(form.excludedCompanies),
        keywords_exclude: splitList(form.excludedWords),
        is_onboarded: true,
      });
      setSaved({ tone: "success", text: "Saved. The next search uses these settings." });
    } catch (e) {
      setSaved({ tone: "danger", text: e.message });
    }
  };

  if (!form) return null;

  return (
    <Card title="Job search" icon={Search} style={{ marginBottom: 20 }}>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div className="stack" style={{ gap: 8, gridColumn: "1 / -1" }}>
          <Field label="Target roles" hint="Comma separated, up to 4 are searched. Company career pages match titles containing every word of a role.">
            <input className="input" value={form.roles} onChange={e => set("roles", e.target.value)} placeholder="Technical Program Manager, Product Manager" />
          </Field>
          <TitleSuggestions value={form.roles} onChange={v => set("roles", v)} />
        </div>
        <Field label="Locations" hint="Up to 3 places are searched, e.g. Bengaluru, Hyderabad, Remote.">
          <input className="input" value={form.locations} onChange={e => set("locations", e.target.value)} placeholder="Bengaluru, Remote" />
        </Field>
        <Field label="Work arrangement">
          <select className="select" value={form.remote} onChange={e => set("remote", e.target.value)}>
            <option value="any">On-site, hybrid or remote</option>
            <option value="remote">Remote only</option>
            <option value="onsite">On-site or hybrid in my locations</option>
          </select>
        </Field>
        <Field label="Ignore postings older than">
          <select className="select" value={form.maxAge} onChange={e => set("maxAge", e.target.value)}>
            {[7, 14, 30, 60, 90].map(d => <option key={d} value={d}>{d} days</option>)}
          </select>
        </Field>
        <Field label="Search automatically every" hint="Each search uses your API tokens.">
          <select className="select" value={form.frequency} onChange={e => set("frequency", e.target.value)}>
            {[6, 12, 24, 48, 168].map(h => <option key={h} value={h}>{h === 168 ? "week" : `${h} hours`}</option>)}
          </select>
        </Field>
        <Field label={`Add to review queue when match is at least ${form.minScore}%`}>
          <input type="range" min={30} max={90} step={5} value={form.minScore} onChange={e => set("minScore", e.target.value)} />
        </Field>
        <Field label="Never show these companies">
          <input className="input" value={form.excludedCompanies} onChange={e => set("excludedCompanies", e.target.value)} placeholder="Company A, Company B" />
        </Field>
        <Field label="Skip jobs mentioning">
          <input className="input" value={form.excludedWords} onChange={e => set("excludedWords", e.target.value)} placeholder="unpaid, commission only" />
        </Field>
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        <Button variant="primary" onClick={save}>Save search settings</Button>
        {saved && <Notice tone={saved.tone}>{saved.text}</Notice>}
      </div>
    </Card>
  );
}

/* ---------------------------------------------------------- Resume details */

function ResumeImportNotice() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(() => api.getResumeStatus().then(setStatus).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!status?.importing) return;
    const timer = setInterval(() => { pumpJobs(); refresh(); }, 4000);
    return () => clearInterval(timer);
  }, [status?.importing, refresh]);

  const reimport = async () => {
    setError(null);
    try {
      await api.reimportResume();
      setStatus(s => ({ ...s, importing: true, message: "Reading your resume with AI" }));
    } catch (e) {
      setError(e.message);
    }
  };

  if (!status?.has_resume) return null;
  if (status.importing) {
    return <Notice style={{ marginBottom: 14 }}>{status.message || "Reading your resume with AI"}...</Notice>;
  }
  if (!status.parsed_with_ai) {
    return (
      <Notice tone="warning" style={{ marginBottom: 14 }}>
        <div>Your resume ({status.file_name}) was read without AI, so your work history, projects and education
          were not imported and generated resumes will be missing them.</div>
        <div className="row" style={{ marginTop: 10 }}>
          <Button variant="primary" icon={RefreshCw} onClick={reimport}>Read resume with AI</Button>
          {error && <span className="small" style={{ color: "var(--danger)" }}>{error}</span>}
        </div>
      </Notice>
    );
  }
  return null;
}

function ResumeCard() {
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);

  useEffect(() => {
    api.getProfile().then(p => setForm({
      headline: p.headline || "",
      contactEmail: p.contact_email || p.email || "",
      pdfEngine: p.pdf_engine || "latex",
      achievements: (p.achievements || []).join("\n"),
    })).catch(() => {});
  }, []);

  const save = async () => {
    setSaved(null);
    try {
      await api.updateProfile({
        headline: form.headline.trim(),
        contact_email: form.contactEmail.trim(),
        pdf_engine: form.pdfEngine,
        achievements: form.achievements.split("\n").map(s => s.trim()).filter(Boolean),
      });
      setSaved({ tone: "success", text: "Saved. Use Re-tailor in the review queue to rebuild existing resumes." });
    } catch (e) {
      setSaved({ tone: "danger", text: e.message });
    }
  };

  if (!form) return null;

  return (
    <Card title="Resume details" icon={FileText} style={{ marginBottom: 20 }}>
      <ResumeImportNotice />
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <Field label="Headline under your name" hint="Each job gets a tailored version.">
          <input className="input" value={form.headline} onChange={e => setForm({ ...form, headline: e.target.value })} placeholder="AI & Robotics | Program Leadership" />
        </Field>
        <Field label="Email shown on resumes and forms">
          <input className="input" type="email" value={form.contactEmail} onChange={e => setForm({ ...form, contactEmail: e.target.value })} />
        </Field>
        <Field label="PDF style for downloads and emails" hint="Application forms always receive the ATS-safe PDF.">
          <select className="select" value={form.pdfEngine} onChange={e => setForm({ ...form, pdfEngine: e.target.value })}>
            <option value="latex">LaTeX (your template)</option>
            <option value="builtin">ATS-safe (plain text-clean PDF)</option>
          </select>
        </Field>
        <Field label="Achievements" hint="One per line, keep the real numbers. Only these and your CV can appear on resumes.">
          <textarea className="textarea" value={form.achievements} onChange={e => setForm({ ...form, achievements: e.target.value })} />
        </Field>
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        <Button variant="primary" onClick={save}>Save resume details</Button>
        {saved && <Notice tone={saved.tone}>{saved.text}</Notice>}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ Email */

function EmailCard() {
  const [account, setAccount] = useState(null);
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [smtpHost, setSmtpHost] = useState("smtp.gmail.com");
  const [imapHost, setImapHost] = useState("imap.gmail.com");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  const load = () => api.getEmailAccount().then(setAccount).catch(() => {});
  useEffect(() => { load(); }, []);

  const connect = async (e) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await api.connectEmail({ email_address: address, app_password: password, smtp_host: smtpHost, imap_host: imapHost });
      setPassword("");
      setMessage({ tone: "success", text: "Connected. Sending and reply tracking are on." });
      load();
    } catch (err) {
      setMessage({ tone: "danger", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!confirm("Disconnect this mailbox? The stored credentials are deleted.")) return;
    await api.disconnectEmail().catch(err => setMessage({ tone: "danger", text: err.message }));
    load();
  };

  return (
    <Card title="Email account" icon={Mail} style={{ marginBottom: 20 }}>
      <p className="small secondary" style={{ marginBottom: 14 }}>Used to email hiring teams from your own address and to track their replies.</p>
      {account?.connected ? (
        <div className="row">
          <div style={{ flex: 1 }}>
            <div>Connected as <strong>{account.email_address}</strong></div>
            <div className="tiny muted">
              Sent in the last 24 hours: {account.sent_today}/{account.daily_limit} · last inbox check {account.last_sync_at ? new Date(account.last_sync_at + "Z").toLocaleString() : "never"}
            </div>
            {account.last_error && <Notice tone="danger" style={{ marginTop: 8 }}>{account.last_error}</Notice>}
          </div>
          <Button variant="danger" onClick={disconnect}>Disconnect</Button>
        </div>
      ) : (
        <form onSubmit={connect} className="stack" style={{ gap: 12 }}>
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="Email address"><input className="input" type="email" required value={address} onChange={e => setAddress(e.target.value)} placeholder="you@gmail.com" /></Field>
            <Field label="App password" hint="Gmail: enable 2-Step Verification, then create one at myaccount.google.com/apppasswords.">
              <input className="input" type="password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="16-character app password" />
            </Field>
          </div>
          <label className="check"><input type="checkbox" checked={advanced} onChange={e => setAdvanced(e.target.checked)} /> Not Gmail (custom SMTP/IMAP servers)</label>
          {advanced && (
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Field label="SMTP host (SSL, port 465)"><input className="input" value={smtpHost} onChange={e => setSmtpHost(e.target.value)} /></Field>
              <Field label="IMAP host (SSL, port 993)"><input className="input" value={imapHost} onChange={e => setImapHost(e.target.value)} /></Field>
            </div>
          )}
          <div><Button variant="primary" type="submit" loading={busy}>Connect</Button></div>
        </form>
      )}
      {message && <Notice tone={message.tone} style={{ marginTop: 12 }}>{message.text}</Notice>}
    </Card>
  );
}

export default function SettingsPage() {
  useEffect(() => {
    if (window.location.hash === "#ai-keys") {
      setTimeout(() => document.getElementById("ai-keys")?.scrollIntoView({ behavior: "smooth" }), 300);
    }
  }, []);

  return (
    <div style={{ maxWidth: 960 }}>
      <PageHeader title="Settings" description="Your AI keys, job search preferences, resume details and email." />
      <AiKeysCard />
      <SearchCard />
      <ResumeCard />
      <EmailCard />
    </div>
  );
}
