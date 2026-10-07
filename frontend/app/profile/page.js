"use client";

import { useEffect, useRef, useState } from "react";
import { Award, Briefcase, GraduationCap, Link2, Save, Upload, User, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Field, Loading, Notice, PageHeader } from "@/components/ui";

const CONTACT_FIELDS = [
  ["name", "Full name"],
  ["phone", "Phone"],
  ["location", "Location"],
  ["linkedin_url", "LinkedIn URL"],
  ["github_url", "GitHub URL"],
  ["portfolio_url", "Portfolio / website"],
];

export default function ProfilePage() {
  const [profile, setProfile] = useState(null);
  const [form, setForm] = useState({});
  const [uploading, setUploading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const fileRef = useRef(null);

  const load = () => api.getProfile().then(p => {
    setProfile(p);
    setForm({
      name: p.name || "", phone: p.phone || "", location: p.location || "", linkedin_url: p.linkedin_url || "",
      github_url: p.github_url || "", portfolio_url: p.portfolio_url || "", professional_summary: p.professional_summary || "",
    });
  }).catch(e => setNotice({ tone: "danger", text: e.message }));

  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const res = await api.updateProfile(form);
      if (res?.name) sessionStorage.setItem("app_user_data", JSON.stringify({ name: res.name, email: res.email }));
      setNotice({ tone: "success", text: "Profile saved." });
      load();
    } catch (e) {
      setNotice({ tone: "danger", text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const upload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploading(true);
    setNotice(null);
    try {
      const res = await api.uploadResume(file);
      if (res?.detail) throw new Error(res.detail);
      setNotice({ tone: "success", text: "Resume imported. Check the sections below - everything on your tailored resumes comes from here." });
      load();
    } catch (err) {
      setNotice({ tone: "danger", text: err.message || "Upload failed" });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const importLinks = async () => {
    setImporting(true);
    setNotice(null);
    try {
      const res = await api.autoImportLinks({ github_url: form.github_url, portfolio_url: form.portfolio_url });
      setNotice({ tone: "success", text: `Imported ${res.skills_imported?.length || 0} skills and ${res.projects_imported || 0} projects.` });
      load();
    } catch (err) {
      setNotice({ tone: "danger", text: err.message });
    } finally {
      setImporting(false);
    }
  };

  if (!profile) return <Loading label="Loading profile..." />;

  const experiences = profile.experiences || [];
  const education = profile.education || [];

  return (
    <div>
      <PageHeader
        title="Profile & resume"
        description="Your master profile. Tailored resumes only ever use facts from here."
        actions={<Button variant="primary" icon={Save} loading={saving} onClick={save}>Save changes</Button>}
      />
      {notice && <Notice tone={notice.tone} style={{ marginBottom: 16 }}>{notice.text}</Notice>}

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Import your resume" icon={Upload}>
            <p className="small secondary" style={{ marginBottom: 12 }}>
              Upload a PDF or DOCX. Your experience, education, skills and achievements are extracted - uploading again replaces them.
            </p>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.doc,.txt" onChange={upload} style={{ display: "none" }} />
            <Button variant="primary" icon={Upload} loading={uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? "Reading resume..." : "Upload resume"}
            </Button>
          </Card>

          <Card title="Contact details" icon={User}>
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {CONTACT_FIELDS.map(([key, label]) => (
                <Field key={key} label={label}>
                  <input className="input" value={form[key] || ""} onChange={e => setForm({ ...form, [key]: e.target.value })} />
                </Field>
              ))}
            </div>
            <Field label="Professional summary" style={{ marginTop: 12 }}>
              <textarea className="textarea" style={{ minHeight: 120 }} value={form.professional_summary || ""} onChange={e => setForm({ ...form, professional_summary: e.target.value })} />
            </Field>
            <div className="row" style={{ marginTop: 12 }}>
              <Button icon={Link2} loading={importing} onClick={importLinks} disabled={!form.github_url && !form.portfolio_url}>
                Import projects from GitHub / portfolio
              </Button>
            </div>
          </Card>
        </div>

        <div className="stack" style={{ gap: 16 }}>
          <Card title={`Experience (${experiences.length})`} icon={Briefcase}>
            {experiences.length === 0 ? (
              <EmptyState icon={Briefcase} title="No experience yet" description="Upload your resume to import it." />
            ) : experiences.map(exp => (
              <div key={exp.id} className="list-row" style={{ alignItems: "flex-start", flexDirection: "column", gap: 4 }}>
                <div className="row" style={{ width: "100%" }}>
                  <strong style={{ flex: 1 }}>{exp.title} · {exp.company}</strong>
                  <span className="tiny muted">{exp.dates}</span>
                </div>
                {(exp.bullets || []).length > 0 ? (
                  <ul className="small secondary" style={{ paddingLeft: 18 }}>
                    {exp.bullets.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                ) : <span className="tiny muted">No details - listed on one line on resumes.</span>}
              </div>
            ))}
          </Card>

          <Card title="Education" icon={GraduationCap}>
            {education.length === 0 ? <p className="small muted">None imported.</p> : education.map((e, i) => (
              <div key={i} className="list-row">
                <div style={{ flex: 1 }}>
                  <strong>{e.institution}</strong>
                  <div className="small secondary">{[e.degree, e.field].filter(Boolean).join(" in ")}{e.gpa ? ` · CGPA ${e.gpa}` : ""}</div>
                </div>
                <span className="tiny muted">{[e.start_date, e.end_date].filter(Boolean).join(" – ")}</span>
              </div>
            ))}
          </Card>

          <Card title={`Skills (${(profile.skills || []).length})`} icon={Wrench}>
            {(profile.skills || []).length === 0 ? <p className="small muted">None imported.</p> : (
              <div className="row" style={{ gap: 6 }}>{profile.skills.map(s => <span key={s} className="chip">{s}</span>)}</div>
            )}
          </Card>

          <Card title="Achievements" icon={Award} actions={<a href="/settings" className="small">Edit in settings</a>}>
            {(profile.achievements || []).length === 0 ? <p className="small muted">None yet.</p> : (
              <ul className="small" style={{ paddingLeft: 18 }}>{profile.achievements.map((a, i) => <li key={i}>{a}</li>)}</ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
