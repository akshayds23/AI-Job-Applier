"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { ChevronDown, Clock, Loader2, LogOut, Moon, Search, Settings, Sun, User, X } from "lucide-react";
import { api, describeRun, pumpJobs, startDiscovery } from "@/lib/api";
import { Notice } from "@/components/ui";

function useTheme() {
  const [theme, setTheme] = useState("light");
  useEffect(() => {
    setTheme(document.documentElement.getAttribute("data-theme") || "light");
  }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch {}
    setTheme(next);
  };
  return [theme, toggle];
}

export default function TopBar() {
  const router = useRouter();
  const pathname = usePathname();
  const [theme, toggleTheme] = useTheme();
  const [user, setUser] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [run, setRun] = useState(null);         // the run currently in progress
  const [result, setResult] = useState(null);   // { tone, text } after a run finishes
  const [starting, setStarting] = useState(false);
  const pollRef = useRef(null);
  const wasRunningRef = useRef(false);

  useEffect(() => {
    if (pathname === "/login" || pathname === "/onboarding") return;
    try {
      const stored = sessionStorage.getItem("app_user_data");
      if (stored) setUser(JSON.parse(stored));
    } catch {}
    api.getProfile().then(res => res && setUser(prev => ({ ...prev, name: res.name, email: res.email }))).catch(() => {});
  }, [pathname]);

  const poll = useCallback(async () => {
    clearTimeout(pollRef.current);
    try {
      const runs = await api.getScrapeRuns(1);
      const latest = runs && runs[0];
      if (latest && latest.status === "running") {
        wasRunningRef.current = true;
        setRun(latest);
        pumpJobs();
        pollRef.current = setTimeout(poll, 3000);
      } else {
        if (wasRunningRef.current && latest) {
          wasRunningRef.current = false;
          setResult(describeRun(latest));
          window.dispatchEvent(new CustomEvent("discovery-finished", { detail: latest }));
        }
        setRun(null);
      }
    } catch {
      pollRef.current = setTimeout(poll, 6000);
    }
  }, []);

  useEffect(() => {
    if (pathname === "/login" || pathname === "/onboarding") return;
    poll();
    const onStart = () => { setResult(null); wasRunningRef.current = true; setRun({ status: "running" }); poll(); };
    window.addEventListener("discovery-started", onStart);
    return () => { window.removeEventListener("discovery-started", onStart); clearTimeout(pollRef.current); };
  }, [pathname, poll]);

  const handleFind = async () => {
    setStarting(true);
    try {
      await startDiscovery();
    } catch (e) {
      setResult({ tone: "danger", text: e.message });
    } finally {
      setStarting(false);
    }
  };

  const handleLogout = () => {
    sessionStorage.removeItem("app_auth_token");
    sessionStorage.removeItem("app_user_data");
    router.push("/login");
  };

  if (pathname === "/login" || pathname === "/onboarding") return null;

  const waiting = run?.llm_waiting_seconds > 0;
  const initials = (user?.name || user?.email || "U").trim()[0]?.toUpperCase();

  return (
    <>
      <header className="topbar">
        <div className="run-status">
          {run ? (
            waiting ? (
              <><Clock size={16} style={{ color: "var(--warning)" }} /> Waiting for your API rate limit to reset ({run.llm_waiting_seconds}s)</>
            ) : (
              <><Loader2 size={16} className="spin" style={{ color: "var(--primary)" }} /> {run.progress || "Searching company sites and job boards, scoring matches..."}</>
            )
          ) : null}
        </div>

        <div className="row" style={{ gap: 10 }}>
          <button className="btn btn-primary" onClick={handleFind} disabled={!!run || starting}>
            {run || starting ? <Loader2 size={16} className="spin" /> : <Search size={16} />}
            {run ? "Searching..." : "Find new jobs"}
          </button>
          <button className="btn btn-ghost btn-icon" onClick={toggleTheme} title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} aria-label="Toggle theme">
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <div style={{ position: "relative" }}>
            <button className="btn btn-ghost" onClick={() => setMenuOpen(o => !o)} style={{ height: 40, gap: 10 }}>
              <span style={{
                width: 30, height: 30, borderRadius: "50%", background: "var(--primary-soft)", color: "var(--primary-text)",
                display: "grid", placeItems: "center", fontWeight: 700, fontSize: "0.85rem",
              }}>{initials}</span>
              <span style={{ textAlign: "left", lineHeight: 1.2 }}>
                <span style={{ display: "block", fontSize: "0.85rem", color: "var(--text)" }}>{user?.name || "Account"}</span>
                <span style={{ display: "block", fontSize: "0.72rem", color: "var(--text-3)", fontWeight: 500 }}>{user?.email || ""}</span>
              </span>
              <ChevronDown size={14} />
            </button>
            {menuOpen && (
              <div className="menu" onMouseLeave={() => setMenuOpen(false)}>
                <Link href="/profile" className="menu-item" onClick={() => setMenuOpen(false)}><User size={16} /> Profile & resume</Link>
                <Link href="/settings" className="menu-item" onClick={() => setMenuOpen(false)}><Settings size={16} /> Settings</Link>
                <div className="divider" style={{ margin: "6px 0" }} />
                <button className="menu-item" onClick={handleLogout} style={{ color: "var(--danger-text)" }}><LogOut size={16} /> Sign out</button>
              </div>
            )}
          </div>
        </div>
      </header>

      {result && (
        <div style={{ marginBottom: 16, position: "relative" }}>
          <Notice tone={result.tone}>
            <span style={{ paddingRight: 28, display: "block" }}>{result.text}</span>
          </Notice>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => setResult(null)} style={{ position: "absolute", right: 6, top: 6 }} aria-label="Dismiss">
            <X size={14} />
          </button>
        </div>
      )}
    </>
  );
}
