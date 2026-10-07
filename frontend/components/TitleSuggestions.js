"use client";

import { useEffect, useState } from "react";
import { Check, Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui";

const splitList = (text) => text.split(",").map(s => s.trim()).filter(Boolean);

/**
 * Job titles suggested from the user's own history. Clicking a title adds it to
 * (or removes it from) the comma-separated `value`.
 */
export default function TitleSuggestions({ value, onChange, autoLoad = true }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getTitleSuggestions(refresh));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (autoLoad) load(false); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const chosen = splitList(value).map(s => s.toLowerCase());
  const toggle = (title) => {
    const list = splitList(value);
    const next = chosen.includes(title.toLowerCase())
      ? list.filter(t => t.toLowerCase() !== title.toLowerCase())
      : [...list, title];
    onChange(next.join(", "));
  };

  const suggestions = data?.suggestions || [];

  return (
    <div className="subtle-panel">
      <div className="row" style={{ marginBottom: 8 }}>
        <span className="field-label">Suggested from your experience</span>
        <span className="spacer" />
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => load(true)}
          title="Re-read your history with AI (one API call)">
          {data?.source === "ai" ? "Refresh" : "Suggest with AI"}
        </Button>
      </div>
      {error && <div className="tiny" style={{ color: "var(--danger-text)" }}>{error}</div>}
      {!data && !error && <div className="tiny muted">{loading ? "Reading your work history..." : "Upload your resume to get suggestions."}</div>}
      {data && suggestions.length === 0 && <div className="tiny muted">No work history yet - upload your resume first.</div>}
      <div className="row" style={{ gap: 6 }}>
        {suggestions.map(s => {
          const on = chosen.includes(s.title.toLowerCase());
          return (
            <button key={s.title} type="button" onClick={() => toggle(s.title)} title={s.why}
              className={`btn btn-sm ${on ? "btn-primary" : "btn-secondary"}`} style={{ fontWeight: 500 }}>
              {on ? <Check size={13} /> : <Plus size={13} />}
              {s.title}
              {s.kind === "adjacent" && !on && <span className="tiny" style={{ color: "var(--accent-text)", fontWeight: 600 }}>related</span>}
            </button>
          );
        })}
      </div>
      {data?.source === "history" && suggestions.length > 0 && (
        <div className="tiny muted" style={{ marginTop: 8 }}>Based on your past titles. “Suggest with AI” also finds related titles your work supports.</div>
      )}
    </div>
  );
}
