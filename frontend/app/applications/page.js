"use client";

import { useEffect, useState } from "react";
import { Building2, SquareKanban } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, EmptyState, Loading, PageHeader, timeAgo } from "@/components/ui";
import { STATUS_META } from "@/lib/status";

const COLUMNS = [
  { id: "queue", statuses: ["pending", "approved"], title: "To review", tone: "warning" },
  { id: "applying", statuses: ["applying", "awaiting_confirmation"], title: "Applying", tone: "primary" },
  { id: "submitted", statuses: ["submitted"], title: "Submitted", tone: "info" },
  { id: "viewed", statuses: ["viewed"], title: "Replied", tone: "accent" },
  { id: "interview", statuses: ["interview"], title: "Interview", tone: "success" },
  { id: "offer", statuses: ["offer"], title: "Offer", tone: "success" },
  { id: "closed", statuses: ["rejected", "skipped"], title: "Closed", tone: "neutral" },
];

const STATUS_OPTIONS = ["pending", "approved", "submitted", "viewed", "interview", "offer", "rejected", "skipped"];

export default function TrackerPage() {
  const [apps, setApps] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    api.getApplications().then(res => setApps(res || [])).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const changeStatus = async (id, status) => {
    try {
      await api.updateApplicationStatus(id, status);
      load();
    } catch (e) {
      alert(e.message);
    }
  };

  return (
    <div>
      <PageHeader title="Tracker" description="Statuses update automatically from confirmation pages and recruiter replies. You can also change them by hand." />

      {loading ? (
        <Loading label="Loading applications..." />
      ) : apps.length === 0 ? (
        <Card><EmptyState icon={SquareKanban} title="Nothing to track yet" description="Applications you approve and send will appear here." /></Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${COLUMNS.length}, minmax(200px, 1fr))`, gap: 12, overflowX: "auto", paddingBottom: 8 }}>
          {COLUMNS.map(col => {
            const items = apps.filter(a => col.statuses.includes(a.status));
            return (
              <div key={col.id} style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 10, minHeight: 420 }}>
                <div className="row" style={{ padding: "2px 4px 10px" }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: col.tone === "neutral" ? "var(--text-3)" : `var(--${col.tone})` }} />
                  <strong className="small">{col.title}</strong>
                  <span className="spacer" />
                  <Badge>{items.length}</Badge>
                </div>
                <div className="stack" style={{ gap: 8 }}>
                  {items.map(item => (
                    <div key={item.id} className="card" style={{ padding: 12 }}>
                      <div style={{ fontWeight: 600, fontSize: "0.88rem", lineHeight: 1.35 }}>{item.job.title}</div>
                      <div className="tiny muted row" style={{ gap: 4, marginTop: 4 }}><Building2 size={12} />{item.job.company}</div>
                      <div className="tiny muted" style={{ marginTop: 4 }}>
                        {item.submitted_at ? `Sent ${timeAgo(item.submitted_at)}` : `Updated ${timeAgo(item.updated_at || item.created_at)}`}
                      </div>
                      <select className="select" style={{ height: 30, fontSize: "0.8rem", marginTop: 8, padding: "2px 8px" }}
                        value={item.status} onChange={e => changeStatus(item.id, e.target.value)}>
                        {!STATUS_OPTIONS.includes(item.status) && <option value={item.status}>{STATUS_META[item.status]?.label || item.status}</option>}
                        {STATUS_OPTIONS.map(s => <option key={s} value={s}>{STATUS_META[s]?.label || s}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
