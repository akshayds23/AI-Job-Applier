"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, FileUp, FolderGit2, Globe, Layers, Plus, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Field, Loading, Notice, PageHeader } from "@/components/ui";

const KIND_LABELS = { project: "Project", highlight: "Work highlight", skill: "Skill" };
const SOURCE_LABELS = { github: "GitHub", portfolio: "Portfolio", document: "Document", manual: "Added by you" };
const splitList = (text) => text.split(",").map(s => s.trim()).filter(Boolean);

function ImportCard({ roles, onImported }) {
  const [github, setGithub] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState(null);
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => { if (!role && roles[0]) setRole(roles[0].company); }, [roles, role]);

  const run = async (name, fn) => {
    setBusy(name);
    setNotice(null);
    try {
      const res = await fn();
      setNotice({ tone: "success", text: res.added ? `Found ${res.added} new item(s). Review them below - nothing is used until you approve it.` : "Nothing new found there." });
      onImported();
    } catch (e) {
      setNotice({ tone: "danger", text: e.message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Import your work" icon={FileUp} style={{ marginBottom: 20 }}>
      <p className="small secondary" style={{ marginBottom: 14 }}>
        AI reads the source and suggests projects, work highlights and skills. Each import uses a few AI calls.
      </p>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
        <Field label="GitHub username" hint="Reads your public repositories and their READMEs.">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input className="input" value={github} onChange={e => setGithub(e.target.value)} placeholder="your-github-username" />
            <Button icon={FolderGit2} loading={busy === "github"} disabled={!github.trim() || !!busy}
              onClick={() => run("github", () => api.careerImportGithub(github))}>Import</Button>
          </div>
        </Field>
        <Field label="Portfolio or any page" hint="Your portfolio, a project page, a blog post.">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input className="input" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-portfolio.com" />
            <Button icon={Globe} loading={busy === "url"} disabled={!url.trim() || !!busy}
              onClick={() => run("url", () => api.careerImportUrl(url))}>Import</Button>
          </div>
        </Field>
        <Field label="Document about your work" hint="HTML, Markdown, text, PDF or Word: design docs, reports, runbooks.">
          <input className="input" type="file" accept=".html,.htm,.md,.txt,.pdf,.docx" onChange={e => setFile(e.target.files[0] || null)} />
          {roles.length > 0 && (
            <select className="select" style={{ marginTop: 8 }} value={role} onChange={e => setRole(e.target.value)}>
              {roles.map(r => <option key={r.company} value={r.company}>Work done at {r.company}</option>)}
            </select>
          )}
          <Button style={{ marginTop: 8 }} icon={FileUp} loading={busy === "doc"} disabled={!file || !!busy}
            onClick={() => run("doc", () => api.careerImportDocument(file, role))}>Import document</Button>
        </Field>
      </div>
      {notice && <Notice tone={notice.tone} style={{ marginTop: 14 }}>{notice.text}</Notice>}
    </Card>
  );
}

function AddManually({ roles, onAdded }) {
  const [kind, setKind] = useState("highlight");
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [skills, setSkills] = useState("");
  const [role, setRole] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { if (!role && roles[0]) setRole(roles[0].company); }, [roles, role]);

  const add = async () => {
    setError("");
    try {
      await api.careerAddFact({
        kind, title: kind === "highlight" ? text : (kind === "skill" ? text : title), text: kind === "skill" ? "" : text,
        skills: splitList(skills), role_company: kind === "highlight" ? role : null,
      });
      setText(""); setTitle(""); setSkills("");
      onAdded();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <Card title="Add something yourself" icon={Plus} style={{ marginBottom: 20 }}>
      <div className="grid" style={{ gridTemplateColumns: "200px 1fr", gap: 14 }}>
        <Field label="Type">
          <select className="select" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="highlight">Work highlight</option>
            <option value="project">Project</option>
            <option value="skill">Skill</option>
          </select>
        </Field>
        <div>
          {kind === "project" && (
            <Field label="Project name"><input className="input" value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Expense Tracker" /></Field>
          )}
          <Field label={kind === "skill" ? "Skill" : kind === "project" ? "What it does and how" : "What you did"}>
            {kind === "skill"
              ? <input className="input" value={text} onChange={e => setText(e.target.value)} placeholder="e.g. Docker" />
              : <textarea className="textarea" rows={3} value={text} onChange={e => setText(e.target.value)}
                  placeholder={kind === "project" ? "What it does, how it is built." : "e.g. Deployed the app on AWS EC2, then moved it to Vercel for a stable custom domain."} />}
          </Field>
          {kind !== "skill" && (
            <Field label="Technologies (comma separated)"><input className="input" value={skills} onChange={e => setSkills(e.target.value)} placeholder="e.g. Python, PostgreSQL, Docker" /></Field>
          )}
          {kind === "highlight" && roles.length > 0 && (
            <Field label="Job">
              <select className="select" value={role} onChange={e => setRole(e.target.value)}>
                {roles.map(r => <option key={r.company} value={r.company}>{r.title} at {r.company}</option>)}
              </select>
            </Field>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <Button variant="primary" icon={Plus} disabled={!text.trim() || (kind === "project" && !title.trim())} onClick={add}>Add</Button>
            {error && <span className="small" style={{ color: "var(--danger)" }}>{error}</span>}
          </div>
        </div>
      </div>
    </Card>
  );
}

function FactRow({ fact, roles, onChange, review }) {
  const [text, setText] = useState(fact.text || fact.title);
  const [skills, setSkills] = useState((fact.skills || []).join(", "));
  const [role, setRole] = useState(fact.role_company || roles[0]?.company || "");
  const [busy, setBusy] = useState(false);

  const save = async (patch) => {
    setBusy(true);
    try { await api.careerUpdateFact(fact.id, patch); onChange(); } finally { setBusy(false); }
  };
  const approve = () => save({
    status: "approved",
    ...(fact.kind === "skill" ? {} : { text, skills: splitList(skills) }),
    ...(fact.kind === "highlight" ? { role_company: role } : {}),
  });

  return (
    <div className="list-row" style={{ alignItems: "flex-start", gap: 12 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          <Badge tone={fact.kind === "project" ? "primary" : fact.kind === "highlight" ? "accent" : "neutral"}>{KIND_LABELS[fact.kind]}</Badge>
          <span className="small secondary">{SOURCE_LABELS[fact.source] || fact.source}{fact.source_ref ? ` · ${fact.source_ref}` : ""}</span>
        </div>
        {fact.kind === "project" && <div style={{ fontWeight: 600, marginBottom: 4 }}>{fact.title}</div>}
        {fact.kind === "skill" ? (
          <div style={{ fontWeight: 600 }}>{fact.title}</div>
        ) : review ? (
          <>
            <textarea className="textarea" rows={fact.kind === "project" ? 3 : 2} value={text} onChange={e => setText(e.target.value)} />
            <input className="input" style={{ marginTop: 6 }} value={skills} onChange={e => setSkills(e.target.value)} placeholder="Technologies" />
            {fact.kind === "highlight" && roles.length > 0 && (
              <select className="select" style={{ marginTop: 6 }} value={role} onChange={e => setRole(e.target.value)}>
                {roles.map(r => <option key={r.company} value={r.company}>Belongs to: {r.title} at {r.company}</option>)}
              </select>
            )}
          </>
        ) : (
          <>
            <p className="small" style={{ lineHeight: 1.55 }}>{fact.text}</p>
            {(fact.skills || []).length > 0 && (
              <div className="row" style={{ gap: 6, marginTop: 6 }}>{fact.skills.map(s => <span key={s} className="chip">{s}</span>)}</div>
            )}
            {fact.kind === "highlight" && fact.role_company && <p className="small secondary" style={{ marginTop: 4 }}>At {fact.role_company}</p>}
          </>
        )}
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
        {review ? (
          <>
            <Button size="sm" variant="primary" icon={Check} loading={busy} onClick={approve}>Approve</Button>
            <Button size="sm" variant="ghost" icon={X} disabled={busy} onClick={() => save({ status: "rejected" })} title="Not mine / not useful" />
          </>
        ) : (
          <Button size="sm" variant="ghost" icon={Trash2} title="Remove from your profile"
            onClick={async () => { if (confirm("Remove this from your career profile?")) { await api.careerDeleteFact(fact.id); onChange(); } }} />
        )}
      </div>
    </div>
  );
}

export default function CareerPage() {
  const [data, setData] = useState(null);
  const load = useCallback(() => api.careerFacts().then(setData).catch(() => setData({ facts: [], roles: [] })), []);
  useEffect(() => { load(); }, [load]);

  if (!data) return <Loading label="Loading your career profile..." />;
  // Projects and work highlights matter most; skills come last.
  const order = { project: 0, highlight: 1, skill: 2 };
  const byKind = (a, b) => order[a.kind] - order[b.kind];
  const pending = data.facts.filter(f => f.status === "pending").sort(byKind);
  const approved = data.facts.filter(f => f.status === "approved").sort(byKind);

  return (
    <div>
      <PageHeader
        title="Career profile"
        description="Everything you have done beyond your resume. Approved items are used to score jobs and tailor resumes - and every resume line still traces back to something you approved."
      />
      <ImportCard roles={data.roles} onImported={load} />

      {pending.length > 0 && (
        <Card title={`To review (${pending.length})`} icon={Check} style={{ marginBottom: 20 }}
          actions={<Button size="sm" onClick={async () => { await api.careerApproveAll(); load(); }}>Approve all</Button>}>
          <p className="small secondary" style={{ marginBottom: 10 }}>
            Edit anything that is not quite right. Reject items that describe someone else's work - company documents often cover the whole team.
          </p>
          {pending.map(f => <FactRow key={f.id} fact={f} roles={data.roles} onChange={load} review />)}
        </Card>
      )}

      <AddManually roles={data.roles} onAdded={load} />

      <Card title={`In your profile (${approved.length})`} icon={Layers}>
        {approved.length === 0
          ? <EmptyState icon={Layers} title="Nothing here yet" description="Import from GitHub, your portfolio or a document, or add something yourself." />
          : approved.map(f => <FactRow key={f.id} fact={f} roles={data.roles} onChange={load} />)}
      </Card>
    </div>
  );
}
