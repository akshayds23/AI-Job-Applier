"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3, Briefcase, Building2, FileText, Inbox, KeyRound, LayoutDashboard, ListChecks,
  Settings, SquareKanban, User,
} from "lucide-react";

const SECTIONS = [
  {
    label: "Overview",
    items: [{ label: "Dashboard", href: "/", icon: LayoutDashboard }],
  },
  {
    label: "Discover",
    items: [
      { label: "Job Feed", href: "/jobs", icon: Briefcase },
      { label: "Target Companies", href: "/companies", icon: Building2 },
    ],
  },
  {
    label: "Apply",
    items: [
      { label: "Review Queue", href: "/queue", icon: ListChecks },
      { label: "Tracker", href: "/applications", icon: SquareKanban },
      { label: "Inbox & Follow-ups", href: "/inbox", icon: Inbox },
    ],
  },
  {
    label: "Insights",
    items: [{ label: "Analytics", href: "/analytics", icon: BarChart3 }],
  },
  {
    label: "Account",
    items: [
      { label: "Profile & Resume", href: "/profile", icon: User },
      { label: "Resume Templates", href: "/templates", icon: FileText },
      { label: "AI Keys", href: "/settings#ai-keys", icon: KeyRound },
      { label: "Settings", href: "/settings", icon: Settings },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  if (pathname === "/login" || pathname === "/onboarding") return null;

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><Briefcase size={17} /></div>
        <div>
          <div className="brand-name">AutoApplier</div>
          <div className="brand-sub">Job search workspace</div>
        </div>
      </div>
      <nav style={{ overflowY: "auto", flex: 1 }}>
        {SECTIONS.map(section => (
          <div key={section.label} className="nav-section">
            <div className="nav-label">{section.label}</div>
            {section.items.map(item => {
              const base = item.href.split("#")[0];
              const active = item.href.includes("#") ? false : pathname === base;
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} className={`nav-item${active ? " active" : ""}`}>
                  <Icon size={17} />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
