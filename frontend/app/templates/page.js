"use client";

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { api } from "@/lib/api";
import { Button, Card, Notice, PageHeader } from "@/components/ui";

// Mirrors backend/services/resume_generator.py TEMPLATES.
const TEMPLATES = [
  { id: "classic", title: "Classic Professional", desc: "LaTeX-style serif with blue section rules. The default, and what the LaTeX PDF uses.", font: "Georgia, 'Times New Roman', serif", accent: "#037e91", rule: true },
  { id: "modern", title: "Modern Tech", desc: "The same structure in a clean sans-serif with a blue accent.", font: "Calibri, 'Segoe UI', sans-serif", accent: "#2563af", rule: true },
  { id: "minimal", title: "Ultra Clean ATS", desc: "Monochrome with no rules - pure typographic hierarchy.", font: "Arial, sans-serif", accent: "#1e1e1e", rule: false },
];

function Preview({ t }) {
  const line = (w, o = 1) => <div style={{ height: 5, width: w, background: "#cbd5e1", borderRadius: 2, opacity: o, marginTop: 5 }} />;
  const heading = (label) => (
    <div style={{ marginTop: 12, color: t.accent, fontWeight: 700, fontSize: 9, borderBottom: t.rule ? `1px solid ${t.accent}` : "none", paddingBottom: 2 }}>{label}</div>
  );
  return (
    <div style={{ background: "#fff", border: "1px solid var(--border)", borderRadius: 6, padding: "16px 18px", fontFamily: t.font, color: "#111", height: 230, overflow: "hidden" }}>
      <div style={{ textAlign: "center", fontWeight: 700, fontSize: 13, letterSpacing: 0.5 }}>YOUR NAME</div>
      <div style={{ textAlign: "center", fontSize: 8, color: "#555", marginTop: 2 }}>Headline for this role</div>
      {heading("Professional Summary")}{line("100%")}{line("92%")}
      {heading("Experience")}
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 8, fontWeight: 700, marginTop: 4 }}><span>Title | Company</span><span style={{ fontWeight: 400 }}>2022 - Present</span></div>
      {line("88%", 0.8)}{line("80%", 0.8)}
      {heading("Education")}{line("70%")}
    </div>
  );
}

export default function TemplatesPage() {
  const [selected, setSelected] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    api.getProfile().then(p => setSelected(p.preferred_template || "classic")).catch(() => setSelected("classic"));
  }, []);

  const choose = async (id) => {
    setSelected(id);
    try {
      await api.updateProfile({ preferred_template: id });
      setNotice({ tone: "success", text: "Template saved. New downloads use it; use Re-tailor or download again for existing applications." });
    } catch (e) {
      setNotice({ tone: "danger", text: e.message });
    }
  };

  return (
    <div>
      <PageHeader title="Resume templates" description="Choose the layout for your PDF and DOCX resumes. All are single-column and ATS-friendly." />
      {notice && <Notice tone={notice.tone} style={{ marginBottom: 16 }}>{notice.text}</Notice>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        {TEMPLATES.map(t => (
          <Card key={t.id} className={selected === t.id ? "selected" : ""}>
            <Preview t={t} />
            <h2 style={{ marginTop: 14 }}>{t.title}</h2>
            <p className="small secondary" style={{ margin: "4px 0 14px", minHeight: 40 }}>{t.desc}</p>
            <Button variant={selected === t.id ? "primary" : "secondary"} icon={selected === t.id ? Check : undefined}
              style={{ width: "100%" }} onClick={() => choose(t.id)}>
              {selected === t.id ? "Selected" : "Use this template"}
            </Button>
          </Card>
        ))}
      </div>
    </div>
  );
}
