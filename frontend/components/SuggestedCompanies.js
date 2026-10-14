"use client";

import { useEffect, useState } from "react";
import { Lightbulb, Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Button, Card, Notice } from "@/components/ui";

/**
 * Companies suggested for the user's own roles and places. The backend asks the AI for
 * employers in the user's field, then keeps only those whose careers board AutoApplier
 * can read, with how many open roles match right now. Cached until roles, places or the
 * resume change; "Suggest again" regenerates (one AI call).
 */
export default function SuggestedCompanies({ watched, onAdded }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [failed, setFailed] = useState({});

  const load = async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getCompanySuggestions(refresh));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(false); }, []);

  const watchedNames = new Set((watched || []).map(c => (c.name || "").toLowerCase()));
  const remaining = (data?.suggestions || []).filter(c => !watchedNames.has(c.name.toLowerCase()));

  const add = async (list) => {
    for (const company of list) {
      setBusy(company.name);
      try {
        await api.addCompany(company.name, company.careers_url);
      } catch (e) {
        setFailed(f => ({ ...f, [company.name]: e.message }));
      }
    }
    setBusy(null);
    onAdded?.();
  };

  const actions = (
    <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} disabled={!!busy} onClick={() => load(true)}
            title="Ask the AI again and re-check the careers pages (one API call)">
      Suggest again
    </Button>
  );

  return (
    <Card title="Suggested companies" icon={Lightbulb} actions={actions} style={{ marginBottom: 20 }}>
      <p className="small muted" style={{ marginBottom: 12 }}>
        Picked for your target roles and locations. Every company here has a careers page AutoApplier can read,
        and the count shows roles that match your titles right now.
      </p>

      {error && <Notice tone="danger">{error}</Notice>}
      {!data && !error && (
        <div className="small muted">
          {loading ? "Finding companies that hire for your roles and checking their careers pages. This can take up to a minute." : null}
        </div>
      )}
      {data?.note && <Notice tone="info">{data.note}</Notice>}
      {data && !data.note && remaining.length === 0 && (
        <div className="small muted">You are already watching every suggested company. Use “Suggest again” for more.</div>
      )}

      {remaining.length > 0 && (
        <>
          <div className="row" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
            <Button size="sm" icon={Plus} loading={!!busy} disabled={!!busy || loading} onClick={() => add(remaining)}>
              Watch all {remaining.length}
            </Button>
          </div>
          <div className="stack" style={{ gap: 6 }}>
            {remaining.map(company => (
              <div key={`${company.ats}:${company.slug}`} className="row" style={{ gap: 10, alignItems: "flex-start" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong className="small">{company.name}</strong>{" "}
                  <span className="tiny muted">
                    {company.matching_roles > 0
                      ? `${company.matching_roles} matching ${company.matching_roles === 1 ? "role" : "roles"} open`
                      : `${company.open_roles} open roles, none matching yet`}
                  </span>
                  {company.why && <div className="tiny secondary">{company.why}</div>}
                  {company.sample_titles?.length > 0 && (
                    <div className="tiny muted" title={company.sample_titles.join("\n")}>e.g. {company.sample_titles.join(" · ")}</div>
                  )}
                  {failed[company.name] && <div className="tiny" style={{ color: "var(--danger-text)" }}>{failed[company.name]}</div>}
                </div>
                <Button size="sm" variant="ghost" icon={busy === company.name ? undefined : Plus}
                        loading={busy === company.name} disabled={!!busy} onClick={() => add([company])}>
                  Watch
                </Button>
              </div>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}
