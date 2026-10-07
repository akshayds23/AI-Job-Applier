"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight, BadgeCheck, Briefcase, CheckCircle2, Circle, Inbox, KeyRound, ListChecks, Mail, Send, Target, User,
} from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, EmptyState, PageHeader, Stat, timeAgo } from "@/components/ui";
import { STATUS_META } from "@/lib/status";

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState([]);
  const [setup, setSetup] = useState(null);

  const load = useCallback(() => {
    api.getAnalytics().then(setStats).catch(() => {});
    api.getApplications().then(apps => setRecent((apps || []).slice(0, 6))).catch(() => {});
    Promise.allSettled([api.getAiKeys(), api.getProfile(), api.getEmailAccount()]).then(([keys, profile, email]) => {
      const p = profile.value || {};
      setSetup([
        { done: (keys.value?.keys || []).some(k => k.is_enabled && k.status === "active"), label: "Add your AI API key", href: "/settings#ai-keys", icon: KeyRound },
        { done: (p.experiences || []).length > 0, label: "Upload your resume", href: "/profile", icon: User },
        { done: (p.target_roles || []).length > 0, label: "Set target roles and locations", href: "/settings", icon: Target },
        { done: !!email.value?.connected, label: "Connect your email (optional)", href: "/settings", icon: Mail },
      ]);
    });
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("discovery-finished", load);
    return () => window.removeEventListener("discovery-finished", load);
  }, [load]);

  const pending = setup ? setup.filter(s => !s.done) : [];

  return (
    <div>
      <PageHeader title="Dashboard" description="Your job search at a glance." />

      {setup && pending.length > 0 && (
        <Card title="Finish setting up" icon={ListChecks} style={{ marginBottom: 20 }}>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 10 }}>
            {setup.map(step => (
              <Link key={step.label} href={step.href} className="subtle-panel row" style={{ color: "var(--text)", flexWrap: "nowrap" }}>
                {step.done
                  ? <CheckCircle2 size={18} style={{ color: "var(--success)" }} />
                  : <Circle size={18} style={{ color: "var(--text-3)" }} />}
                <span style={{ textDecoration: step.done ? "line-through" : "none", color: step.done ? "var(--text-3)" : "var(--text)" }}>{step.label}</span>
              </Link>
            ))}
          </div>
        </Card>
      )}

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", marginBottom: 20 }}>
        <Stat icon={Briefcase} label="Jobs analysed" value={stats?.total_matched_jobs ?? "-"} hint="Scored against your profile" />
        <Stat icon={BadgeCheck} label="Verified openings" value={stats?.verified_jobs ?? "-"} hint="Confirmed on company sites" tone="accent" />
        <Stat icon={Send} label="Applications sent" value={stats?.submitted ?? "-"} hint={`${stats?.total_applications ?? 0} in your pipeline`} tone="primary" />
        <Stat icon={Inbox} label="Replies" value={stats?.replies ?? "-"} hint={`${stats?.response_rate_percent ?? 0}% response rate`} tone="success" />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)", alignItems: "start" }}>
        <Card title="Recent applications" icon={ListChecks} actions={<Link href="/applications" className="small">Open tracker</Link>}>
          {recent.length === 0 ? (
            <EmptyState icon={Briefcase} title="No applications yet" description="Use “Find new jobs” at the top. Good matches land in your review queue." />
          ) : (
            <div>
              {recent.map(app => {
                const meta = STATUS_META[app.status] || { label: app.status, tone: "neutral" };
                return (
                  <div key={app.id} className="list-row">
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{app.job.title}</div>
                      <div className="small muted">{app.job.company} · {timeAgo(app.updated_at || app.created_at)}</div>
                    </div>
                    {app.match_score != null && <Badge tone="primary">{Math.round(app.match_score)}% match</Badge>}
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Next steps" icon={ArrowRight}>
          <div className="stack">
            <Link href="/queue" className="btn btn-primary"><ListChecks size={16} /> Review queue</Link>
            <Link href="/jobs" className="btn btn-secondary"><Briefcase size={16} /> Browse job feed</Link>
            <Link href="/inbox" className="btn btn-secondary"><Inbox size={16} /> Replies & follow-ups</Link>
          </div>
          {stats && (
            <div className="stack small" style={{ marginTop: 18, gap: 6 }}>
              <div className="row"><span className="secondary">Interviews</span><span className="spacer" /><strong>{stats.interviews}</strong></div>
              <div className="row"><span className="secondary">Offers</span><span className="spacer" /><strong>{stats.offers}</strong></div>
              <div className="row"><span className="secondary">Average match</span><span className="spacer" /><strong>{stats.average_match_score}%</strong></div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
