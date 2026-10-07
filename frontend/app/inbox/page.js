"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Clock, FileText, Inbox, Mail, MailX, RefreshCw, Send } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Field, Loading, Notice, PageHeader, timeAgo } from "@/components/ui";

const CATEGORY = {
  offer: { label: "Offer", tone: "success" },
  interview: { label: "Interview", tone: "success" },
  assessment: { label: "Assessment", tone: "violet" },
  received: { label: "Application received", tone: "info" },
  recruiter: { label: "Recruiter", tone: "primary" },
  rejection: { label: "Rejected", tone: "danger" },
  other: { label: "Other", tone: "neutral" },
};

function FollowUp({ item, onSent }) {
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState(item.contact || "");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const draft = async () => {
    setOpen(true);
    setBusy(true);
    try {
      const res = await api.draftEmail(item.application_id, "follow_up");
      setSubject(res.subject);
      setBody(res.body);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    if (!confirm(`Send this follow-up to ${to}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.sendEmail(item.application_id, { to_address: to, subject, body, kind: "follow_up", attach_resume: false });
      onSent();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="subtle-panel">
      <div className="row">
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontWeight: 600 }}>{item.title} · {item.company}</div>
          <div className="small muted">
            No reply for {item.days_since} days{item.contact ? ` · last emailed ${item.contact}` : " · no HR contact yet"}
          </div>
        </div>
        {!open && <Button size="sm" icon={FileText} onClick={draft}>Draft follow-up</Button>}
      </div>
      {open && (
        <div className="stack" style={{ gap: 10, marginTop: 12 }}>
          <Field label="To"><input className="input" placeholder="Find a contact from the review queue" value={to} onChange={e => setTo(e.target.value)} /></Field>
          <Field label="Subject"><input className="input" value={subject} onChange={e => setSubject(e.target.value)} /></Field>
          <Field label="Message"><textarea className="textarea" style={{ minHeight: 140 }} value={body} placeholder={busy ? "Writing..." : ""} onChange={e => setBody(e.target.value)} /></Field>
          <div className="row">
            <Button variant="primary" size="sm" icon={Send} loading={busy} onClick={send} disabled={!to || !subject || !body}>Send follow-up</Button>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
          {error && <Notice tone="danger">{error}</Notice>}
        </div>
      )}
    </div>
  );
}

export default function InboxPage() {
  const [account, setAccount] = useState(null);
  const [messages, setMessages] = useState([]);
  const [followUps, setFollowUps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = () => {
    Promise.all([
      api.getEmailAccount().then(setAccount),
      api.getInbox().then(res => setMessages(res || [])),
      api.getFollowUps().then(res => setFollowUps(res || [])),
    ]).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const sync = async () => {
    setSyncing(true);
    setNotice(null);
    try {
      const res = await api.syncInbox();
      setNotice({ tone: "success", text: `Checked ${res.scanned} emails: ${res.stored} job-related, ${res.status_updates} status update(s).` });
      load();
    } catch (e) {
      setNotice({ tone: "danger", text: e.message });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Inbox & follow-ups"
        description="Recruiter replies are read from your mailbox every hour, classified, and move your applications forward."
        actions={account?.connected && <Button variant="primary" icon={RefreshCw} loading={syncing} onClick={sync}>Check inbox now</Button>}
      />
      {notice && <Notice tone={notice.tone} style={{ marginBottom: 16 }}>{notice.text}</Notice>}

      {loading ? (
        <Loading />
      ) : !account?.connected ? (
        <Card>
          <EmptyState icon={MailX} title="Connect your email to track replies"
            description="Takes two minutes with a Gmail App Password. Only job-related emails are read and stored."
            action={<Link href="/settings" className="btn btn-primary">Open settings</Link>} />
        </Card>
      ) : (
        <div className="stack" style={{ gap: 20 }}>
          <Card title={`Follow-ups due (${followUps.length})`} icon={Clock}>
            {followUps.length === 0
              ? <p className="small muted">Nothing due. Applications with no reply after 7 days show up here.</p>
              : <div className="stack" style={{ gap: 10 }}>{followUps.map(item => <FollowUp key={item.application_id} item={item} onSent={load} />)}</div>}
          </Card>

          <Card title="Job-related replies" icon={Inbox}
            actions={<span className="tiny muted">Last checked {account.last_sync_at ? timeAgo(account.last_sync_at) : "never"}</span>}>
            {messages.length === 0 ? (
              <EmptyState icon={Mail} title="No replies yet" description="Replies to your applications will appear here." />
            ) : (
              messages.map(m => {
                const cat = CATEGORY[m.category] || CATEGORY.other;
                return (
                  <div key={m.id} className="list-row" style={{ alignItems: "flex-start" }}>
                    <Badge tone={cat.tone}>{cat.label}</Badge>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600 }}>{m.subject}</div>
                      <div className="small muted">
                        {m.from_name || m.from_address} · {m.received_at ? timeAgo(m.received_at) : ""}
                        {m.application && <> · {m.application.title} at {m.application.company}</>}
                      </div>
                      <div className="small secondary" style={{ marginTop: 4 }}>{(m.snippet || "").slice(0, 220)}</div>
                    </div>
                  </div>
                );
              })
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
