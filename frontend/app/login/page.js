"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { BadgeCheck, FileText, Inbox } from "lucide-react";
import Logo from "@/components/Logo";
import { api } from "@/lib/api";
import { Button, Field, Notice } from "@/components/ui";

const POINTS = [
  { icon: BadgeCheck, title: "Verified openings", text: "Jobs checked against the employer's own careers page; stale and scam posts are filtered out." },
  { icon: FileText, title: "Truthful tailoring", text: "Every resume line traces back to your CV - nothing invented." },
  { icon: Inbox, title: "Replies tracked", text: "Recruiter emails update your tracker automatically." },
];

export default function LoginPage() {
  const router = useRouter();
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = isRegister
        ? await api.register(name || email.split("@")[0], email, password)
        : await api.login(email, password);
      if (res && res.access_token) {
        sessionStorage.setItem("app_auth_token", res.access_token);
        if (res.user) sessionStorage.setItem("app_user_data", JSON.stringify(res.user));
        router.push(isRegister ? "/onboarding" : "/");
      }
    } catch (err) {
      setError(err.message || "Sign-in failed. Check your details and try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-shell">
      <aside className="auth-aside">
        <div className="row" style={{ gap: 10 }}>
          <Logo size={36} style={{ borderRadius: 9, boxShadow: "0 0 0 1.5px rgba(255,255,255,0.55)" }} />
          <span style={{ fontWeight: 700, fontSize: "1.1rem" }}>AutoApplier</span>
        </div>
        <div>
          <h1 style={{ fontSize: "2rem", lineHeight: 1.2, marginBottom: 12 }}>Apply where it counts.</h1>
          <p style={{ fontSize: "1rem", maxWidth: 440 }}>
            Find real openings on company career sites, send resumes tailored to each role,
            and keep every application in one place.
          </p>
          <div className="stack" style={{ marginTop: 32, gap: 18 }}>
            {POINTS.map(({ icon: Icon, title, text }) => (
              <div key={title} className="row" style={{ alignItems: "flex-start", gap: 12, flexWrap: "nowrap" }}>
                <Icon size={20} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <div style={{ fontWeight: 600 }}>{title}</div>
                  <p className="small">{text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
        <p className="tiny">Bring your own AI key. Your data stays in your account.</p>
      </aside>

      <main className="auth-main">
        <div className="auth-card">
          <h1 style={{ marginBottom: 4 }}>{isRegister ? "Create your account" : "Sign in"}</h1>
          <p className="secondary" style={{ marginBottom: 24 }}>
            {isRegister ? "It takes a minute to set up your profile." : "Welcome back."}
          </p>
          <form onSubmit={handleSubmit} className="stack" style={{ gap: 14 }}>
            {isRegister && (
              <Field label="Full name">
                <input className="input" value={name} onChange={e => setName(e.target.value)} autoComplete="name" />
              </Field>
            )}
            <Field label="Email">
              <input className="input" type="email" required value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" />
            </Field>
            <Field label="Password" hint={isRegister ? "At least 8 characters." : undefined}>
              <input className="input" type="password" required value={password} onChange={e => setPassword(e.target.value)}
                autoComplete={isRegister ? "new-password" : "current-password"} />
            </Field>
            {error && <Notice tone="danger">{error}</Notice>}
            <Button variant="primary" type="submit" loading={loading} style={{ width: "100%", height: 40 }}>
              {isRegister ? "Create account" : "Sign in"}
            </Button>
          </form>
          <p className="small secondary" style={{ marginTop: 20, textAlign: "center" }}>
            {isRegister ? "Already have an account? " : "New here? "}
            <a href="#" onClick={e => { e.preventDefault(); setIsRegister(!isRegister); setError(""); }}>
              {isRegister ? "Sign in" : "Create an account"}
            </a>
          </p>
        </div>
      </main>
    </div>
  );
}
