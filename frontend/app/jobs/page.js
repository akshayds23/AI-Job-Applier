"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bookmark, Briefcase, Building2, Calendar, ExternalLink, MapPin, RotateCcw, Search, ShieldCheck, SlidersHorizontal, Trash2,
} from "lucide-react";
import { api, startDiscovery } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Field, Loading, PageHeader, ScoreRing, timeAgo } from "@/components/ui";
import { COMPANY_SITE_PLATFORMS, PLATFORM_LABELS, TRUST_META } from "@/lib/status";

const DEFAULT_FILTERS = {
  query: "",
  location: "",
  remoteOnly: false,
  source: "all",
  trust: "hide_risky",
  minScore: 0,
  postedWithin: 0,
  seniority: "any",
  includeLow: false,
  sort: "match",
};

const PRESETS_KEY = "jobFeedPresets";

function loadPresets() {
  try { return JSON.parse(localStorage.getItem(PRESETS_KEY) || "[]"); } catch { return []; }
}

function savePresets(presets) {
  try { localStorage.setItem(PRESETS_KEY, JSON.stringify(presets)); } catch {}
}

function ageInDays(date) {
  if (!date) return null;
  const then = new Date(String(date).endsWith("Z") ? date : `${date}Z`);
  return (Date.now() - then.getTime()) / 86400000;
}

// Postings that came without a description get no AI score (it would be a guess from
// the title). Pasting the description from the posting scores that one job with AI.
function AddDescription({ jobId, onDone }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api.addJobDescription(jobId, text);
      setOpen(false);
      setText("");
      onDone?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <p className="small secondary" style={{ marginTop: 10, lineHeight: 1.55 }}>
        No description came with this posting, so it is matched on the title only.{" "}
        <button className="link-button" onClick={() => setOpen(true)} style={{ color: "var(--primary)", fontWeight: 600 }}>
          Add description
        </button>{" "}
        to get a real AI match score.
      </p>
    );
  }
  return (
    <div style={{ marginTop: 10 }}>
      <textarea
        className="textarea"
        rows={6}
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="Open the posting, copy the full job description and paste it here."
      />
      <div className="row" style={{ marginTop: 8 }}>
        <Button variant="primary" size="sm" loading={busy} disabled={text.trim().length < 200} onClick={submit}>
          Score with AI
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
        <span className="small secondary">Uses one AI call.</span>
      </div>
      {error && <p className="small" style={{ color: "var(--danger)", marginTop: 6 }}>{error}</p>}
    </div>
  );
}

export default function JobsPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [presets, setPresets] = useState([]);
  const [presetName, setPresetName] = useState("");
  const [expanded, setExpanded] = useState({});

  const set = (key, value) => setFilters(prev => ({ ...prev, [key]: value }));

  const load = useCallback(() => {
    setLoading(true);
    api.getMatchedJobs(0, filters.includeLow)
      .then(res => setItems(res || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [filters.includeLow]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    setPresets(loadPresets());
    window.addEventListener("discovery-finished", load);
    return () => window.removeEventListener("discovery-finished", load);
  }, [load]);

  const visible = useMemo(() => {
    const q = filters.query.trim().toLowerCase();
    const loc = filters.location.trim().toLowerCase();
    const list = items.filter(({ job, match_score }) => {
      if (q && !`${job.title} ${job.company} ${(job.tags || []).join(" ")} ${job.description_text || ""}`.toLowerCase().includes(q)) return false;
      if (loc && !(job.location || "").toLowerCase().includes(loc)) return false;
      if (filters.remoteOnly && !job.is_remote) return false;
      if (filters.source === "company_sites" && !COMPANY_SITE_PLATFORMS.includes(job.platform)) return false;
      if (!["all", "company_sites"].includes(filters.source) && job.platform !== filters.source) return false;
      if (filters.trust === "verified" && job.trust_label !== "verified") return false;
      if (filters.trust === "hide_risky" && ["suspicious", "stale"].includes(job.trust_label)) return false;
      if (match_score < filters.minScore) return false;
      if (filters.postedWithin) {
        const age = ageInDays(job.posted_date);
        if (age == null || age > filters.postedWithin) return false;
      }
      if (filters.seniority !== "any" && (job.seniority_level || "mid") !== filters.seniority) return false;
      return true;
    });
    const sorters = {
      match: (a, b) => b.match_score - a.match_score,
      newest: (a, b) => new Date(b.job.posted_date || 0) - new Date(a.job.posted_date || 0),
      trust: (a, b) => (b.job.trust_score ?? 50) - (a.job.trust_score ?? 50) || b.match_score - a.match_score,
    };
    return list.sort(sorters[filters.sort]);
  }, [items, filters]);

  const addPreset = () => {
    const name = presetName.trim();
    if (!name) return;
    const next = [...presets.filter(p => p.name !== name), { name, filters }];
    setPresets(next);
    savePresets(next);
    setPresetName("");
  };

  const removePreset = (name) => {
    const next = presets.filter(p => p.name !== name);
    setPresets(next);
    savePresets(next);
  };

  const sources = useMemo(() => [...new Set(items.map(i => i.job.platform))].filter(p => !COMPANY_SITE_PLATFORMS.includes(p)), [items]);

  return (
    <div>
      <PageHeader
        title="Job feed"
        description="Jobs scored against your profile. Verified jobs were confirmed on the employer's own careers page."
      />

      <div className="grid" style={{ gridTemplateColumns: "280px minmax(0, 1fr)", alignItems: "start" }}>
        {/* Filters */}
        <Card title="Filters" icon={SlidersHorizontal} style={{ position: "sticky", top: 76 }}
          actions={<Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => setFilters(DEFAULT_FILTERS)} title="Reset filters" />}>
          <div className="stack" style={{ gap: 14 }}>
            <Field label="Search">
              <div style={{ position: "relative" }}>
                <Search size={15} style={{ position: "absolute", left: 10, top: 11, color: "var(--text-3)" }} />
                <input className="input" style={{ paddingLeft: 32 }} placeholder="Title, company, skill" value={filters.query} onChange={e => set("query", e.target.value)} />
              </div>
            </Field>
            <Field label="Location">
              <input className="input" placeholder="e.g. Bengaluru" value={filters.location} onChange={e => set("location", e.target.value)} />
            </Field>
            <label className="check"><input type="checkbox" checked={filters.remoteOnly} onChange={e => set("remoteOnly", e.target.checked)} /> Remote only</label>
            <Field label="Source">
              <select className="select" value={filters.source} onChange={e => set("source", e.target.value)}>
                <option value="all">All sources</option>
                <option value="company_sites">Company career sites</option>
                {sources.map(s => <option key={s} value={s}>{PLATFORM_LABELS[s] || s}</option>)}
              </select>
            </Field>
            <Field label="Trust">
              <select className="select" value={filters.trust} onChange={e => set("trust", e.target.value)}>
                <option value="hide_risky">Hide suspicious and stale</option>
                <option value="verified">Verified only</option>
                <option value="all">Show everything</option>
              </select>
            </Field>
            <Field label={`Minimum match: ${filters.minScore}%`}>
              <input type="range" min={0} max={90} step={5} value={filters.minScore} onChange={e => set("minScore", Number(e.target.value))} />
            </Field>
            <Field label="Posted within">
              <select className="select" value={filters.postedWithin} onChange={e => set("postedWithin", Number(e.target.value))}>
                <option value={0}>Any time</option>
                <option value={3}>Last 3 days</option>
                <option value={7}>Last 7 days</option>
                <option value={14}>Last 14 days</option>
                <option value={30}>Last 30 days</option>
              </select>
            </Field>
            <Field label="Seniority">
              <select className="select" value={filters.seniority} onChange={e => set("seniority", e.target.value)}>
                <option value="any">Any level</option>
                <option value="junior">Junior / entry</option>
                <option value="mid">Mid</option>
                <option value="senior">Senior</option>
                <option value="lead">Lead / manager</option>
                <option value="principal">Principal / staff</option>
              </select>
            </Field>
            <label className="check"><input type="checkbox" checked={filters.includeLow} onChange={e => set("includeLow", e.target.checked)} /> Include low matches</label>

            <div className="divider" style={{ margin: "4px 0" }} />
            <div className="field-label">Saved filters</div>
            {presets.length === 0 && <div className="tiny muted">Save the current filters to reuse them.</div>}
            {presets.map(p => (
              <div key={p.name} className="row" style={{ flexWrap: "nowrap" }}>
                <button className="btn btn-ghost btn-sm" style={{ flex: 1, justifyContent: "flex-start" }} onClick={() => setFilters({ ...DEFAULT_FILTERS, ...p.filters })}>
                  <Bookmark size={14} /> {p.name}
                </button>
                <Button size="sm" variant="ghost" icon={Trash2} onClick={() => removePreset(p.name)} title="Delete preset" />
              </div>
            ))}
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input className="input" style={{ height: 32 }} placeholder="Preset name" value={presetName} onChange={e => setPresetName(e.target.value)} />
              <Button size="sm" onClick={addPreset} disabled={!presetName.trim()}>Save</Button>
            </div>
          </div>
        </Card>

        {/* Results */}
        <div className="stack">
          <div className="row small secondary">
            <span><strong style={{ color: "var(--text)" }}>{visible.length}</strong> of {items.length} jobs</span>
            <span className="spacer" />
            <span>Sort</span>
            <select className="select" style={{ width: 180, height: 32 }} value={filters.sort} onChange={e => set("sort", e.target.value)}>
              <option value="match">Best match</option>
              <option value="newest">Newest</option>
              <option value="trust">Most trustworthy</option>
            </select>
          </div>

          {loading ? (
            <Loading label="Loading jobs..." />
          ) : items.length === 0 ? (
            <Card>
              <EmptyState icon={Briefcase} title="No jobs yet"
                description="Search your target companies and the job boards. Analysing takes a few minutes; only promising jobs use your AI key."
                action={<Button variant="primary" icon={Search} onClick={() => startDiscovery().catch(e => alert(e.message))}>Find new jobs</Button>} />
            </Card>
          ) : visible.length === 0 ? (
            <Card><EmptyState icon={SlidersHorizontal} title="No jobs match these filters" description="Loosen a filter or reset them." /></Card>
          ) : (
            visible.map(item => {
              const { job } = item;
              const companySite = COMPANY_SITE_PLATFORMS.includes(job.platform);
              const trust = TRUST_META[job.trust_label];
              const open = expanded[item.match_id];
              return (
                <Card key={item.match_id}>
                  <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap", gap: 16 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="row" style={{ marginBottom: 4 }}>
                        <a href={job.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--text)", fontWeight: 600, fontSize: "1.02rem" }}>{job.title}</a>
                        {companySite
                          ? <Badge tone="accent" icon={Building2}>Company site</Badge>
                          : <Badge>{PLATFORM_LABELS[job.platform] || job.platform}</Badge>}
                        {trust && !companySite && (
                          <Badge tone={trust.tone} icon={job.trust_label === "verified" ? ShieldCheck : undefined} title={(job.trust_flags || []).join("\n")}>
                            {trust.label}
                          </Badge>
                        )}
                        {item.status === "queued" && <Badge tone="primary">In review queue</Badge>}
                      </div>
                      <div className="row small secondary" style={{ gap: 14 }}>
                        <span className="row" style={{ gap: 4 }}><Building2 size={14} /> {job.company}</span>
                        {job.location && <span className="row" style={{ gap: 4 }}><MapPin size={14} /> {job.location}{job.is_remote && !/remote/i.test(job.location) ? " · Remote" : ""}</span>}
                        {job.posted_date && <span className="row" style={{ gap: 4 }}><Calendar size={14} /> {timeAgo(job.posted_date)}</span>}
                        {job.salary_min && <span>{job.salary_currency || ""} {job.salary_min.toLocaleString()}{job.salary_max ? `-${job.salary_max.toLocaleString()}` : ""}</span>}
                      </div>
                      {item.scoring_method === "title_only" ? (
                        <AddDescription jobId={job.id} onDone={load} />
                      ) : (
                        <p className="small secondary" style={{ marginTop: 10, lineHeight: 1.55 }}>
                          {(job.description_text || "No description provided.").slice(0, open ? 1200 : 240)}{(job.description_text || "").length > 240 ? "…" : ""}
                        </p>
                      )}
                      {(item.matched_skills?.length > 0 || item.gap_skills?.length > 0) && (
                        <div className="row" style={{ marginTop: 10, gap: 6 }}>
                          {(item.matched_skills || []).slice(0, 8).map(s => <span key={s} className="chip chip-success">{s}</span>)}
                          {(item.gap_skills || []).slice(0, 5).map(s => <span key={s} className="chip chip-warning" title="Asked for, not on your profile">{s}</span>)}
                        </div>
                      )}
                      {open && (job.trust_flags || []).length > 0 && (
                        <ul className="small secondary" style={{ marginTop: 10, paddingLeft: 18 }}>
                          {job.trust_flags.map(f => <li key={f}>{f}</li>)}
                        </ul>
                      )}
                      <div className="row" style={{ marginTop: 12 }}>
                        <a href={job.url} target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-sm"><ExternalLink size={14} /> View posting</a>
                        {job.verified_url && job.verified_url !== job.url && (
                          <a href={job.verified_url} target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-sm"><Building2 size={14} /> Apply on company site</a>
                        )}
                        {item.status === "queued" && <Link href="/queue" className="btn btn-primary btn-sm">Open in queue</Link>}
                        <button className="btn btn-ghost btn-sm" onClick={() => setExpanded(e => ({ ...e, [item.match_id]: !open }))}>
                          {open ? "Less" : "More details"}
                        </button>
                      </div>
                    </div>
                    {item.scoring_method === "title_only"
                      ? <Badge title="No description came with this posting, so it has no real match score yet">Title only</Badge>
                      : <ScoreRing score={item.match_score} />}
                  </div>
                </Card>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
