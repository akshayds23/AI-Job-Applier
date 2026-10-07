"use client";

import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react";

export function PageHeader({ title, description, actions }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Card({ title, icon: Icon, actions, children, className = "", style, ...rest }) {
  return (
    <div className={`card ${className}`} style={style} {...rest}>
      {(title || actions) && (
        <div className="card-header">
          {title && (
            <div className="card-title">
              {Icon && <Icon size={18} />}
              <span>{title}</span>
            </div>
          )}
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

export function Button({ variant = "secondary", size, icon: Icon, loading = false, children, className = "", ...rest }) {
  const cls = [
    variant === "primary" ? "btn btn-primary" : variant === "ghost" ? "btn btn-ghost" : variant === "danger" ? "btn btn-danger" : "btn btn-secondary",
    size === "sm" ? "btn-sm" : "",
    !children ? "btn-icon" : "",
    className,
  ].join(" ");
  return (
    <button className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <Loader2 size={size === "sm" ? 14 : 16} className="spin" /> : Icon && <Icon size={size === "sm" ? 14 : 16} />}
      {children}
    </button>
  );
}

export function Badge({ tone = "neutral", icon: Icon, children, title }) {
  const cls = tone === "neutral" ? "badge" : `badge badge-${tone}`;
  return (
    <span className={cls} title={title}>
      {Icon && <Icon size={12} />}
      {children}
    </span>
  );
}

export function Field({ label, hint, children, style }) {
  return (
    <label className="field" style={style}>
      {label && <span className="field-label">{label}</span>}
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

const NOTICE_ICONS = { info: Info, success: CheckCircle2, warning: AlertTriangle, danger: XCircle };

export function Notice({ tone = "info", children, style }) {
  const Icon = NOTICE_ICONS[tone] || Info;
  return (
    <div className={`notice notice-${tone}`} style={style}>
      <Icon size={16} />
      <div>{children}</div>
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="empty-state">
      {Icon && <div className="empty-icon"><Icon size={22} /></div>}
      <h2>{title}</h2>
      {description && <p className="small" style={{ maxWidth: 460, margin: "0 auto" }}>{description}</p>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, icon: Icon, tone }) {
  const color = tone ? `var(--${tone})` : "var(--text)";
  return (
    <div className="card">
      <div className="stat-label">{Icon && <Icon size={15} />}{label}</div>
      <div className="stat-value" style={{ color }}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Loading({ label = "Loading..." }) {
  return (
    <div className="card row" style={{ justifyContent: "center", padding: 40, color: "var(--text-2)" }}>
      <Loader2 size={18} className="spin" /> {label}
    </div>
  );
}

/** Match score shown as a tinted circle; colour follows the score band. */
export function ScoreRing({ score }) {
  const value = Math.round(score ?? 0);
  const tone = value >= 75 ? "success" : value >= 55 ? "primary" : value >= 40 ? "warning" : "danger";
  return (
    <div className="score-ring" style={{ background: `var(--${tone}-soft)`, color: `var(--${tone}-text)` }} title="Match score">
      {value}%
    </div>
  );
}

export function timeAgo(value) {
  if (!value) return "";
  const then = new Date(String(value).endsWith("Z") || String(value).includes("+") ? value : `${value}Z`);
  const minutes = Math.round((Date.now() - then.getTime()) / 60000);
  if (Number.isNaN(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}
