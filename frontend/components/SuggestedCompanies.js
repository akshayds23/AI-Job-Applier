"use client";

import { useState } from "react";
import { Lightbulb, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { Button, Card } from "@/components/ui";

// Companies whose career pages AutoApplier can read directly (Greenhouse / Lever / Ashby),
// checked in October 2026 to have open AI/ML roles in India or remote. Review periodically.
const GROUPS = [
  {
    title: "AI teams hiring in India",
    companies: [
      ["Sarvam AI", "https://jobs.ashbyhq.com/sarvam"],
      ["Meesho", "https://jobs.lever.co/meesho"],
      ["InMobi", "https://job-boards.greenhouse.io/inmobi"],
      ["Observe.AI", "https://job-boards.greenhouse.io/observeai"],
      ["HackerRank", "https://job-boards.greenhouse.io/hackerrank"],
      ["Mindtickle", "https://jobs.lever.co/mindtickle"],
      ["Paytm", "https://jobs.lever.co/paytm"],
      ["Zeta", "https://jobs.lever.co/zeta"],
      ["Groww", "https://job-boards.greenhouse.io/groww"],
      ["CRED", "https://jobs.lever.co/cred"],
      ["Atlan", "https://jobs.ashbyhq.com/atlan"],
      ["Druva", "https://job-boards.greenhouse.io/druva"],
    ],
  },
  {
    title: "Global AI companies with remote roles",
    companies: [
      ["Supabase", "https://jobs.ashbyhq.com/supabase"],
      ["PostHog", "https://jobs.ashbyhq.com/posthog"],
      ["Replit", "https://jobs.ashbyhq.com/replit"],
      ["LangChain", "https://jobs.ashbyhq.com/langchain"],
      ["Anyscale", "https://jobs.ashbyhq.com/anyscale"],
      ["Cohere", "https://jobs.ashbyhq.com/cohere"],
      ["Elastic", "https://job-boards.greenhouse.io/elastic"],
      ["MongoDB", "https://job-boards.greenhouse.io/mongodb"],
    ],
  },
];

export default function SuggestedCompanies({ watched, onAdded }) {
  const [busy, setBusy] = useState(null);
  const [failed, setFailed] = useState({});
  const watchedNames = new Set((watched || []).map(c => (c.name || "").toLowerCase()));

  const add = async (list) => {
    for (const [name, url] of list) {
      if (watchedNames.has(name.toLowerCase())) continue;
      setBusy(name);
      try {
        await api.addCompany(name, url);
      } catch (e) {
        setFailed(f => ({ ...f, [name]: e.message }));
      }
    }
    setBusy(null);
    onAdded?.();
  };

  const groups = GROUPS.map(g => ({ ...g, remaining: g.companies.filter(([n]) => !watchedNames.has(n.toLowerCase())) }))
    .filter(g => g.remaining.length);
  if (!groups.length) return null;

  return (
    <Card title="Suggested companies" icon={Lightbulb} style={{ marginBottom: 20 }}>
      <p className="small muted" style={{ marginBottom: 12 }}>
        Companies whose career pages AutoApplier can read directly and that recently had AI / ML roles open.
      </p>
      {groups.map(group => (
        <div key={group.title} style={{ marginBottom: 14 }}>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <strong className="small">{group.title}</strong>
            <Button size="sm" icon={Plus} loading={busy && group.remaining.some(([n]) => n === busy)}
                    disabled={!!busy} onClick={() => add(group.remaining)}>
              Watch all {group.remaining.length}
            </Button>
          </div>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            {group.remaining.map(([name, url]) => (
              <Button key={name} size="sm" variant="ghost" icon={failed[name] ? undefined : busy === name ? undefined : Plus}
                      loading={busy === name} disabled={!!busy} title={failed[name] || url}
                      onClick={() => add([[name, url]])}>
                {name}{failed[name] ? " (not found)" : ""}
              </Button>
            ))}
          </div>
        </div>
      ))}
    </Card>
  );
}

