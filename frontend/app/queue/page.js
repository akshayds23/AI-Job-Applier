"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Building2, Check, CheckCircle2, Download, ExternalLink, FileText, ListChecks, Mail, MapPin, MousePointerClick,
  RefreshCw, Save, Search, Send, ShieldCheck, ThumbsUp, X,
} from "lucide-react";
import { api, getConfig, pumpJobs } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Field, Loading, Notice, PageHeader } from "@/components/ui";
import { STATUS_META } from "@/lib/status";

const QUEUE_STATUSES = "pending,approved,applying,awaiting_confirmation";
const LIVE_APPLY = ["preparing", "filling", "waiting_for_user"];

function PreparePanel({ item, onDone }) {
  const [state, setState] = useState(item.prepare);
  const [wait, setWait] = useState(null);
  const timer = useRef(null);

  const poll = useCallback(async () => {
    try {
      const res = await api.getApplyStatus(item.id);
      setState(res.prepare);
      setWait(res.llm_wait);
      if (res.prepare?.state === "preparing") {
        pumpJobs();
        timer.current = setTimeout(poll, 3000);
      }
      else if (res.prepare?.state === "done") onDone();
    } catch {}
  }, [item.id, onDone]);

  useEffect(() => {
    setState(item.prepare);
    if (item.prepare?.state === "preparing") poll();
    return () => clearTimeout(timer.current);
  }, [item.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    try {
      setState(await api.prepareApplication(item.id));
      pumpJobs();
      clearTimeout(timer.current);
      timer.current = setTimeout(poll, 2500);
    } catch (e) {
      setState({ state: "failed", message: e.message });
    }
  };

  const busy = state?.state === "preparing";
  return (
    <Card title="Tailored resume and cover letter" icon={FileText} style={{ marginBottom: 16 }}>
      <p className="small secondary" style={{ marginBottom: 12 }}>
        {item.prepared
          ? "Prepared for this job. Download them above, or re-tailor after editing your profile."
          : "Not prepared yet. To save your API tokens, tailoring happens only for jobs you choose."}
      </p>
      <div className="row">
        <Button variant={item.prepared ? "secondary" : "primary"} icon={RefreshCw} loading={busy} onClick={start}>
          {item.prepared ? "Re-tailor" : "Prepare documents"}
        </Button>
        {busy && wait?.seconds > 0 && (
          <Badge tone="warning">Waiting {wait.seconds}s for your API rate limit</Badge>
        )}
        {state?.message && (
          <span className="small" style={{ color: state.state === "failed" ? "var(--danger-text)" : state.state === "done" ? "var(--success-text)" : "var(--text-2)" }}>
            {state.message}
          </span>
        )}
      </div>
    </Card>
  );
}

function ApplyPanel({ item, onChanged }) {
  const [assisted, setAssisted] = useState(true);
  useEffect(() => { getConfig().then(c => setAssisted(c.assisted_apply !== false)); }, []);
  const [session, setSession] = useState(item.apply_session);
  const [error, setError] = useState(item.error_message);
  const timer = useRef(null);

  const poll = useCallback(async () => {
    try {
      const res = await api.getApplyStatus(item.id);
      setSession(res.session);
      setError(res.error_message);
      if (res.session && LIVE_APPLY.includes(res.session.state)) timer.current = setTimeout(poll, 3000);
      else if (res.status !== item.status) onChanged();
    } catch {}
  }, [item.id, item.status, onChanged]);

  useEffect(() => {
    setSession(item.apply_session);
    setError(item.error_message);
    if (item.apply_session && LIVE_APPLY.includes(item.apply_session.state)) poll();
    return () => clearTimeout(timer.current);
  }, [item.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null);
    try {
      setSession(await api.startAssistedApply(item.id));
      clearTimeout(timer.current);
      timer.current = setTimeout(poll, 2000);
    } catch (e) {
      setError(e.message);
    }
  };

  const live = session && LIVE_APPLY.includes(session.state);
  const tone = session?.state === "failed" ? "danger" : session?.state === "submitted" ? "success" : "info";

  return (
    <Card title="Apply on the company site" icon={MousePointerClick} style={{ marginBottom: 16 }}>
      <p className="small secondary" style={{ marginBottom: 12 }}>
        {assisted
          ? "Opens the real application form in a browser window on your computer, fills in your details and attaches the ATS-safe resume. You check everything and click Submit yourself; the confirmation page is detected automatically."
          : "Open the employer's application form, attach the ATS-safe resume from Documents above, then mark it submitted here."}
      </p>
      <div className="row">
        {assisted && <Button variant="primary" icon={MousePointerClick} loading={live} onClick={start}>{live ? "Browser open" : "Auto-fill application"}</Button>}
        <a href={item.job.apply_url} target="_blank" rel="noopener noreferrer" className="btn btn-secondary"><ExternalLink size={16} /> Open application page</a>
      </div>
      {session?.message && <Notice tone={tone} style={{ marginTop: 12 }}>{session.message}</Notice>}
      {error && !session && <Notice tone="danger" style={{ marginTop: 12 }}>{error}</Notice>}
    </Card>
  );
}

function EmailPanel({ item }) {
  const [contacts, setContacts] = useState(null);
  const [finding, setFinding] = useState(false);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [sending, setSending] = useState(false);
  const [attachResume, setAttachResume] = useState(true);
  const [attachLetter, setAttachLetter] = useState(false);
  const [history, setHistory] = useState(null);
  const [message, setMessage] = useState(null);

  const loadHistory = useCallback(() => {
    api.getEmailHistory(item.id).then(setHistory).catch(() => {});
  }, [item.id]);

  useEffect(() => {
    setContacts(null); setTo(""); setSubject(""); setBody(""); setMessage(null);
    loadHistory();
  }, [item.id, loadHistory]);

  const find = async () => {
    setFinding(true);
    setMessage(null);
    try {
      const res = await api.findContacts(item.id);
      setContacts(res);
      if (res.contacts[0] && !to) setTo(res.contacts[0].email);
    } catch (e) {
      setMessage({ tone: "danger", text: e.message });
    } finally {
      setFinding(false);
    }
  };

  const draft = async () => {
    setDrafting(true);
    try {
      const res = await api.draftEmail(item.id, "initial");
      setSubject(res.subject);
      setBody(res.body);
    } catch (e) {
      setMessage({ tone: "danger", text: e.message });
    } finally {
      setDrafting(false);
    }
  };

  const send = async () => {
    if (!confirm(`Send this email to ${to}?`)) return;
    setSending(true);
    setMessage(null);
    try {
      await api.sendEmail(item.id, { to_address: to, subject, body, kind: "initial", attach_resume: attachResume, attach_cover_letter: attachLetter });
      setMessage({ tone: "success", text: `Sent to ${to}. Replies are tracked automatically.` });
      loadHistory();
    } catch (e) {
      setMessage({ tone: "danger", text: e.message });
    } finally {
      setSending(false);
    }
  };

  return (
    <Card title="Email the hiring team" icon={Mail} style={{ marginBottom: 16 }}>
      <p className="small secondary" style={{ marginBottom: 12 }}>Sent from your own mailbox (connect it in Settings). Use alongside the application, not instead of it.</p>
      <Button icon={Search} loading={finding} onClick={find} style={{ marginBottom: 12 }}>Find HR / careers contacts</Button>

      {contacts && (
        <div className="stack small" style={{ gap: 4, marginBottom: 12 }}>
          {contacts.contacts.length === 0 && <span className="muted">No published address found{contacts.domain ? ` on ${contacts.domain}` : ""}.</span>}
          {[...contacts.contacts, ...contacts.guesses].map(c => (
            <label key={c.email} className="check">
              <input type="radio" name="contact" checked={to === c.email} onChange={() => setTo(c.email)} />
              <span style={{ color: "var(--text)" }}>{c.email}</span>
              <Badge tone={c.confidence === "guess" ? "warning" : "neutral"}>{c.source}</Badge>
            </label>
          ))}
        </div>
      )}

      <div className="stack" style={{ gap: 10 }}>
        <Field label="To"><input className="input" placeholder="careers@company.com" value={to} onChange={e => setTo(e.target.value)} /></Field>
        <div className="row" style={{ alignItems: "flex-end", flexWrap: "nowrap" }}>
          <Field label="Subject" style={{ flex: 1 }}><input className="input" value={subject} onChange={e => setSubject(e.target.value)} /></Field>
          <Button icon={FileText} loading={drafting} onClick={draft}>Draft with AI</Button>
        </div>
        <Field label="Message"><textarea className="textarea" style={{ minHeight: 180 }} value={body} onChange={e => setBody(e.target.value)} /></Field>
        <div className="row">
          <label className="check"><input type="checkbox" checked={attachResume} onChange={e => setAttachResume(e.target.checked)} /> Attach resume (PDF)</label>
          <label className="check"><input type="checkbox" checked={attachLetter} onChange={e => setAttachLetter(e.target.checked)} /> Attach cover letter</label>
          <span className="spacer" />
          <Button variant="primary" icon={Send} loading={sending} onClick={send} disabled={!to || !subject || !body}>Send email</Button>
        </div>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
      </div>

      {history && (history.sent.length > 0 || history.received.length > 0) && (
        <div className="small" style={{ marginTop: 16 }}>
          <div className="field-label" style={{ marginBottom: 6 }}>Email history</div>
          {history.sent.map((e, i) => (
            <div key={`s${i}`} className="list-row" style={{ padding: "6px 0" }}>
              <Send size={14} className="muted" />
              <span style={{ flex: 1 }}>{e.kind === "follow_up" ? "Follow-up" : "Email"} to {e.to}</span>
              <span className="muted">{e.status === "sent" ? new Date(e.sent_at + "Z").toLocaleString() : `failed: ${e.error}`}</span>
            </div>
          ))}
          {history.received.map((m, i) => (
            <div key={`r${i}`} className="list-row" style={{ padding: "6px 0" }}>
              <Mail size={14} className="muted" />
              <span style={{ flex: 1 }}>{m.subject}</span>
              <Badge>{m.category}</Badge>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function QueuePage() {
  const [queue, setQueue] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState("");
  const [letter, setLetter] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(() => {
    return api.getApplications(QUEUE_STATUSES)
      .then(res => {
        const items = res || [];
        setQueue(items);
        setActiveId(prev => (items.some(i => i.id === prev) ? prev : items[0]?.id ?? null));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("discovery-finished", load);
    return () => window.removeEventListener("discovery-finished", load);
  }, [load]);

  const activeItem = queue.find(i => i.id === activeId) || null;

  useEffect(() => {
    setSummary(activeItem?.tailored_summary || "");
    setLetter(activeItem?.cover_letter || "");
    setNotice(null);
  }, [activeItem?.id, activeItem?.tailored_summary, activeItem?.cover_letter]); // eslint-disable-line react-hooks/exhaustive-deps

  const setStatus = async (status) => {
    try {
      await api.updateApplicationStatus(activeItem.id, status);
      // Approving is the moment to spend tokens on tailoring.
      if (status === "approved" && !activeItem.prepared) await api.prepareApplication(activeItem.id);
      await load();
    } catch (e) {
      setNotice({ tone: "danger", text: e.message });
    }
  };

  const saveDrafts = async () => {
    setSaving(true);
    try {
      await api.editApplication(activeItem.id, { tailored_summary: summary, cover_letter: letter });
      await load();
      setNotice({ tone: "success", text: "Edits saved." });
    } catch (e) {
      setNotice({ tone: "danger", text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const download = (format) => api.downloadDocument(activeItem.id, format).catch(e => setNotice({ tone: "danger", text: e.message }));
  const dirty = activeItem && (summary !== (activeItem.tailored_summary || "") || letter !== (activeItem.cover_letter || ""));

  return (
    <div>
      <PageHeader title="Review queue" description="Approve the jobs you want, then apply on the company site and email the hiring team." />

      {loading ? (
        <Loading label="Loading applications..." />
      ) : queue.length === 0 ? (
        <Card>
          <EmptyState icon={ListChecks} title="Your queue is empty"
            description="Jobs that score above your minimum match appear here after a search. Adjust the minimum in Settings if nothing comes through." />
        </Card>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "300px minmax(0, 1fr)", alignItems: "start" }}>
          <div className="stack" style={{ gap: 8 }}>
            {queue.map(item => {
              const meta = STATUS_META[item.status] || { label: item.status, tone: "neutral" };
              return (
                <div key={item.id} onClick={() => setActiveId(item.id)} className={`card interactive${activeId === item.id ? " selected" : ""}`} style={{ padding: 14 }}>
                  <div style={{ fontWeight: 600, fontSize: "0.92rem" }}>{item.job.title}</div>
                  <div className="small muted" style={{ margin: "2px 0 8px" }}>{item.job.company}</div>
                  <div className="row" style={{ gap: 6 }}>
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                    {item.match_score != null && <Badge tone="primary">{Math.round(item.match_score)}%</Badge>}
                    {item.job.trust_label === "verified" && <Badge tone="success" icon={ShieldCheck}>Verified</Badge>}
                    {item.prepared && <Badge tone="accent" icon={Check}>Docs ready</Badge>}
                  </div>
                </div>
              );
            })}
          </div>

          {activeItem && (
            <div>
              <Card style={{ marginBottom: 16 }}>
                <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
                  <div style={{ flex: 1, minWidth: 240 }}>
                    <h2 style={{ fontSize: "1.25rem" }}>{activeItem.job.title}</h2>
                    <div className="row small secondary" style={{ marginTop: 4, gap: 14 }}>
                      <span className="row" style={{ gap: 4 }}><Building2 size={14} />{activeItem.job.company}</span>
                      {activeItem.job.location && <span className="row" style={{ gap: 4 }}><MapPin size={14} />{activeItem.job.location}</span>}
                      <a href={activeItem.job.url} target="_blank" rel="noopener noreferrer" className="row" style={{ gap: 4 }}><ExternalLink size={14} />Job posting</a>
                    </div>
                  </div>
                  <div className="row">
                    {activeItem.status === "pending" && <Button variant="primary" icon={ThumbsUp} onClick={() => setStatus("approved")}>Approve</Button>}
                    <Button icon={CheckCircle2} onClick={() => setStatus("submitted")}>I submitted it</Button>
                    <Button variant="ghost" icon={X} onClick={() => setStatus("skipped")}>Skip</Button>
                  </div>
                </div>
                {activeItem.status === "awaiting_confirmation" && (
                  <Notice tone="warning" style={{ marginTop: 12 }}>The browser closed before a confirmation page appeared. If you submitted, click “I submitted it”.</Notice>
                )}
                <div className="divider" />
                <div className="row">
                  <span className="field-label" style={{ marginRight: 4 }}>Documents</span>
                  <Button size="sm" icon={Download} onClick={() => download("pdf")}>Resume PDF</Button>
                  <Button size="sm" icon={Download} onClick={() => download("ats_pdf")} title="Plain PDF that applicant-tracking systems parse cleanly">ATS PDF</Button>
                  <Button size="sm" icon={Download} onClick={() => download("docx")}>DOCX</Button>
                  <Button size="sm" icon={Download} onClick={() => download("tex")} title="LaTeX source for Overleaf">LaTeX</Button>
                  {activeItem.cover_letter && <Button size="sm" icon={Download} onClick={() => download("cover_letter")}>Cover letter</Button>}
                </div>
                {notice && <Notice tone={notice.tone} style={{ marginTop: 12 }}>{notice.text}</Notice>}
              </Card>

              <PreparePanel item={activeItem} onDone={load} />
              {activeItem.status !== "pending" && <ApplyPanel item={activeItem} onChanged={load} />}
              {activeItem.status !== "pending" && <EmailPanel item={activeItem} />}

              {activeItem.prepared && (
                <Card title="Review the drafts" icon={FileText}>
                  {activeItem.tailored_headline && (
                    <p className="small secondary" style={{ marginBottom: 12 }}>Headline: <strong style={{ color: "var(--text)" }}>{activeItem.tailored_headline}</strong></p>
                  )}
                  <div className="stack" style={{ gap: 14 }}>
                    <Field label="Professional summary"><textarea className="textarea" style={{ minHeight: 110 }} value={summary} onChange={e => setSummary(e.target.value)} /></Field>
                    <Field label="Cover letter"><textarea className="textarea" style={{ minHeight: 260 }} value={letter} onChange={e => setLetter(e.target.value)} /></Field>
                    {dirty && <div><Button variant="primary" icon={Save} loading={saving} onClick={saveDrafts}>Save edits</Button></div>}
                  </div>
                </Card>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
