"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Building2, ExternalLink, Eye, MapPin, Pause, Play, Plus, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Field, Loading, Notice, PageHeader, timeAgo } from "@/components/ui";

const ATS_LABELS = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby" };

export default function CompaniesPage() {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [careersUrl, setCareersUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const [checkingId, setCheckingId] = useState(null);

  const load = () => {
    api.getCompanies().then(res => setCompanies(res || [])).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleAdd = async (e) => {
    e.preventDefault();
    setError("");
    setAdding(true);
    try {
      await api.addCompany(name.trim(), careersUrl.trim());
      setName("");
      setCareersUrl("");
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setAdding(false);
    }
  };

  const handleCheck = async (company) => {
    setCheckingId(company.id);
    try {
      setPreview(await api.checkCompany(company.id));
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setCheckingId(null);
    }
  };

  const handleToggle = async (company) => {
    await api.setCompanyActive(company.id, !company.is_active).catch(err => setError(err.message));
    load();
  };

  const handleRemove = async (company) => {
    if (!confirm(`Stop watching ${company.name}?`)) return;
    await api.removeCompany(company.id).catch(err => setError(err.message));
    if (preview?.company?.id === company.id) setPreview(null);
    load();
  };

  return (
    <div>
      <PageHeader
        title="Target companies"
        description="Watch employers' own career pages. Their openings are real and current, are checked on every search, and are analysed before job-board results."
      />

      <Card title="Add a company" icon={Plus} style={{ marginBottom: 20 }}>
        <form onSubmit={handleAdd} className="row" style={{ alignItems: "flex-end", gap: 12 }}>
          <Field label="Company name" style={{ flex: 1, minWidth: 200 }}>
            <input className="input" placeholder="e.g. Stripe" value={name} onChange={e => setName(e.target.value)} />
          </Field>
          <Field label="Careers page URL (optional)" style={{ flex: 2, minWidth: 260 }}>
            <input className="input" placeholder="https://www.figma.com/careers/" value={careersUrl} onChange={e => setCareersUrl(e.target.value)} />
          </Field>
          <Button variant="primary" type="submit" icon={Plus} loading={adding} disabled={!name.trim() && !careersUrl.trim()}>
            Watch company
          </Button>
        </form>
        <p className="tiny muted" style={{ marginTop: 10 }}>
          Supported: companies hiring through Greenhouse, Lever or Ashby. If the name alone isn't found, paste the careers-page URL.
        </p>
        {error && <Notice tone="danger" style={{ marginTop: 12 }}>{error}</Notice>}
      </Card>

      {loading ? (
        <Loading label="Loading watchlist..." />
      ) : companies.length === 0 ? (
        <Card><EmptyState icon={Building2} title="No companies yet" description="Add the companies you most want to work for." /></Card>
      ) : (
        <Card title={`Watching ${companies.length} ${companies.length === 1 ? "company" : "companies"}`} icon={Building2}>
          {companies.map(company => (
            <div key={company.id} className="list-row" style={{ opacity: company.is_active ? 1 : 0.6 }}>
              <div style={{
                width: 36, height: 36, borderRadius: 8, background: "var(--surface-2)", display: "grid", placeItems: "center",
                fontWeight: 700, color: "var(--text-2)", flexShrink: 0,
              }}>{company.name[0]?.toUpperCase()}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row">
                  <strong>{company.name}</strong>
                  <Badge>{ATS_LABELS[company.ats] || company.ats}</Badge>
                  {!company.is_active && <Badge tone="warning">Paused</Badge>}
                </div>
                <div className="small muted">
                  {company.last_error
                    ? <span style={{ color: "var(--danger-text)" }}><AlertTriangle size={13} style={{ verticalAlign: -2 }} /> {company.last_error}</span>
                    : <>{company.last_job_count ?? "?"} open roles · checked {timeAgo(company.last_checked_at) || "never"}</>}
                  {" · "}
                  <a href={company.careers_url} target="_blank" rel="noopener noreferrer">careers page</a>
                </div>
              </div>
              <Button size="sm" icon={Eye} loading={checkingId === company.id} onClick={() => handleCheck(company)}>Openings</Button>
              <Button size="sm" variant="ghost" icon={company.is_active ? Pause : Play} onClick={() => handleToggle(company)} title={company.is_active ? "Pause" : "Resume"} />
              <Button size="sm" variant="ghost" icon={Trash2} onClick={() => handleRemove(company)} title="Remove" />
            </div>
          ))}
        </Card>
      )}

      {preview && (
        <Card title={`${preview.company.name}: ${preview.jobs.length} open roles`} icon={Eye} style={{ marginTop: 20 }}
          actions={<Button size="sm" variant="ghost" icon={X} onClick={() => setPreview(null)} title="Close" />}>
          <p className="tiny muted" style={{ marginBottom: 8 }}>Newest first. Only roles matching your target roles are analysed during a search.</p>
          <div style={{ maxHeight: 480, overflowY: "auto" }}>
            {preview.jobs.map(job => (
              <div key={job.url} className="list-row small">
                <a href={job.url} target="_blank" rel="noopener noreferrer" style={{ flex: 1, color: "var(--text)" }}>{job.title}</a>
                {job.location && <span className="muted row" style={{ gap: 4 }}><MapPin size={13} />{job.location}</span>}
                {job.posted_date && <span className="muted">{timeAgo(job.posted_date)}</span>}
                <ExternalLink size={14} style={{ color: "var(--text-3)" }} />
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
