"use client";

import { useEffect, useState } from "react";
import { BadgeCheck, Briefcase, Gauge, Inbox, Send, TrendingUp } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, Loading, PageHeader, Stat } from "@/components/ui";
import { STATUS_META } from "@/lib/status";

export default function AnalyticsPage() {
  const [stats, setStats] = useState(null);

  useEffect(() => { api.getAnalytics().then(setStats).catch(() => setStats({})); }, []);

  if (!stats) return <Loading label="Loading analytics..." />;

  const funnel = [
    { label: "Jobs analysed", value: stats.total_matched_jobs || 0, color: "var(--text-3)" },
    { label: "In your pipeline", value: stats.total_applications || 0, color: "var(--primary)" },
    { label: "Applications sent", value: stats.submitted || 0, color: "var(--info)" },
    { label: "Replies", value: stats.replies || 0, color: "var(--accent)" },
    { label: "Interviews", value: stats.interviews || 0, color: "var(--success)" },
    { label: "Offers", value: stats.offers || 0, color: "var(--success)" },
  ];
  const top = Math.max(1, funnel[0].value);
  const byStatus = Object.entries(stats.by_status || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div>
      <PageHeader title="Analytics" description="How your search converts, from jobs found to offers." />

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", marginBottom: 20 }}>
        <Stat icon={Briefcase} label="Jobs analysed" value={stats.total_matched_jobs ?? 0} />
        <Stat icon={BadgeCheck} label="Verified openings" value={stats.verified_jobs ?? 0} tone="accent" />
        <Stat icon={Send} label="Applications sent" value={stats.submitted ?? 0} tone="primary" />
        <Stat icon={Inbox} label="Response rate" value={`${stats.response_rate_percent ?? 0}%`} hint={`${stats.replies ?? 0} replies`} tone="success" />
        <Stat icon={TrendingUp} label="Interview rate" value={`${stats.interview_rate_percent ?? 0}%`} hint={`${stats.interviews ?? 0} interviews`} />
        <Stat icon={Gauge} label="Average match" value={`${stats.average_match_score ?? 0}%`} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(240px, 1fr)", alignItems: "start" }}>
        <Card title="Conversion funnel" icon={TrendingUp}>
          <div className="stack" style={{ gap: 14 }}>
            {funnel.map((step, i) => {
              const previous = i > 0 ? funnel[i - 1].value : null;
              const rate = previous ? Math.round((step.value / previous) * 100) : null;
              return (
                <div key={step.label}>
                  <div className="row small" style={{ marginBottom: 6 }}>
                    <span style={{ fontWeight: 600 }}>{step.label}</span>
                    <span className="spacer" />
                    <strong>{step.value}</strong>
                    {rate != null && <span className="muted" style={{ width: 70, textAlign: "right" }}>{rate}% of prev.</span>}
                  </div>
                  <div className="progress"><div style={{ width: `${Math.max(step.value ? 2 : 0, (step.value / top) * 100)}%`, background: step.color }} /></div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card title="Applications by status">
          {byStatus.length === 0 ? <p className="small muted">No applications yet.</p> : byStatus.map(([status, count]) => (
            <div key={status} className="list-row">
              <Badge tone={STATUS_META[status]?.tone || "neutral"}>{STATUS_META[status]?.label || status}</Badge>
              <span className="spacer" />
              <strong>{count}</strong>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}
