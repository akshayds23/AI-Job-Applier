"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, KeyRound, Target, Upload } from "lucide-react";
import Logo from "@/components/Logo";
import { api, startDiscovery } from "@/lib/api";
import { Button, Card, Field, Notice } from "@/components/ui";
import TitleSuggestions from "@/components/TitleSuggestions";

const STEPS = [
  { title: "Your resume", icon: Upload },
  { title: "Your AI key", icon: KeyRound },
  { title: "What you're looking for", icon: Target },
];

const splitList = (text) => text.split(",").map(s => s.trim()).filter(Boolean);

export default function Onboarding() {
  const router = useRouter();
  const fileRef = useRef(null);
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [prefs, setPrefs] = useState({ roles: "", locations: "", remote: "any" });
  const [key, setKey] = useState({ provider: "groq", api_key: "" });

  const run = async (fn) => {
    setBusy(true);
    setNotice(null);
    try { await fn(); } catch (e) { setNotice({ tone: "danger", text: e.message }); } finally { setBusy(false); }
  };

  const upload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    run(async () => {
      const res = await api.uploadResume(file);
      if (res?.detail) throw new Error(res.detail);
      setNotice({ tone: "success", text: "Resume imported." });
      setStep(1);
    });
  };

  const savePrefs = () => run(async () => {
    if (!splitList(prefs.roles).length) throw new Error("Add at least one target role.");
    await api.updateProfile({
      target_roles: splitList(prefs.roles),
      target_locations: splitList(prefs.locations),
      remote_preference: prefs.remote,
      is_onboarded: true,
    });
    // First search starts right away (it runs in the background; the dashboard shows progress).
    // Without an AI key it still runs, scoring with the free heuristics.
    await startDiscovery().catch(() => {});
    router.push("/");
  });

  const addKey = () => run(async () => {
    const res = await api.addAiKey({ provider: key.provider, api_key: key.api_key });
    if (res?.resume_reimport) {
      // The resume was uploaded before this key existed; the backend is re-reading it with AI now.
      setNotice({ tone: "success", text: "Key added. Reading your resume with AI in the background to import your work history." });
    }
    setStep(2);
  });

  const StepIcon = STEPS[step].icon;

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, background: "var(--bg)" }}>
      <div style={{ width: "100%", maxWidth: 560 }}>
        <div className="row" style={{ gap: 10, marginBottom: 24 }}>
          <Logo size={32} />
          <strong style={{ fontSize: "1.05rem" }}>AutoApplier</strong>
          <span className="spacer" />
          <span className="small muted">Step {step + 1} of {STEPS.length}</span>
        </div>

        <div className="row" style={{ gap: 6, marginBottom: 20 }}>
          {STEPS.map((s, i) => (
            <div key={s.title} style={{ flex: 1, height: 4, borderRadius: 4, background: i <= step ? "var(--primary)" : "var(--surface-3)" }} />
          ))}
        </div>

        <Card>
          <div className="row" style={{ marginBottom: 6 }}>
            <StepIcon size={20} style={{ color: "var(--primary)" }} />
            <h1 style={{ fontSize: "1.25rem" }}>{STEPS[step].title}</h1>
          </div>

          {step === 0 && (
            <>
              <p className="secondary" style={{ marginBottom: 18 }}>
                Upload your resume (PDF or DOCX). Your experience, education, skills and achievements are imported,
                and every tailored resume is built only from these facts.
              </p>
              <input ref={fileRef} type="file" accept=".pdf,.docx,.doc,.txt" onChange={upload} style={{ display: "none" }} />
              <Button variant="primary" icon={Upload} loading={busy} onClick={() => fileRef.current?.click()}>Upload resume</Button>
            </>
          )}

          {step === 2 && (
            <div className="stack" style={{ gap: 14 }}>
              <p className="secondary">Jobs are searched and scored against these. Pick from titles suggested by your experience, or type your own.</p>
              <Field label="Target roles" hint="Comma separated, e.g. Technical Program Manager, Product Manager">
                <input className="input" value={prefs.roles} onChange={e => setPrefs({ ...prefs, roles: e.target.value })} />
              </Field>
              <TitleSuggestions value={prefs.roles} onChange={v => setPrefs({ ...prefs, roles: v })} />
              <Field label="Locations" hint="Up to 6, e.g. Bengaluru, Hyderabad, Pune, Remote">
                <input className="input" value={prefs.locations} onChange={e => setPrefs({ ...prefs, locations: e.target.value })} />
              </Field>
              <Field label="Work arrangement">
                <select className="select" value={prefs.remote} onChange={e => setPrefs({ ...prefs, remote: e.target.value })}>
                  <option value="any">On-site, hybrid or remote</option>
                  <option value="remote">Remote only</option>
                  <option value="onsite">On-site or hybrid in my locations</option>
                </select>
              </Field>
            </div>
          )}

          {step === 1 && (
            <div className="stack" style={{ gap: 14 }}>
              <p className="secondary">
                AutoApplier uses your own AI key to score jobs and tailor resumes. Groq and Google Gemini both have free tiers;
                you can add more keys later in Settings to search faster.
              </p>
              <Field label="Provider">
                <select className="select" value={key.provider} onChange={e => setKey({ ...key, provider: e.target.value })}>
                  <option value="groq">Groq (free tier)</option>
                  <option value="gemini">Google Gemini (free tier)</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic Claude</option>
                </select>
              </Field>
              <Field label="API key">
                <input className="input" type="password" autoComplete="off" value={key.api_key} onChange={e => setKey({ ...key, api_key: e.target.value })} />
              </Field>
            </div>
          )}

          {notice && <Notice tone={notice.tone} style={{ marginTop: 14 }}>{notice.text}</Notice>}

          <div className="row" style={{ marginTop: 24 }}>
            {step > 0 && <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep(step - 1)}>Back</Button>}
            <span className="spacer" />
            {step === 0 && <Button variant="ghost" onClick={() => setStep(1)}>Skip for now</Button>}
            {step === 1 && (
              <>
                <Button variant="ghost" onClick={() => setStep(2)}>Skip</Button>
                <Button variant="primary" icon={ArrowRight} loading={busy} disabled={key.api_key.trim().length < 12} onClick={addKey}>Test key and continue</Button>
              </>
            )}
            {step === 2 && <Button variant="primary" icon={Check} loading={busy} onClick={savePrefs}>Finish</Button>}
          </div>
        </Card>
      </div>
    </div>
  );
}
