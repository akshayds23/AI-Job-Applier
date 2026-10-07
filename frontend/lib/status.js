// Application status -> label and badge tone, shared by every page.
export const STATUS_META = {
  pending: { label: "Needs review", tone: "warning" },
  approved: { label: "Approved", tone: "primary" },
  applying: { label: "Applying", tone: "primary" },
  awaiting_confirmation: { label: "Confirm submission", tone: "warning" },
  submitted: { label: "Submitted", tone: "info" },
  viewed: { label: "Replied", tone: "accent" },
  interview: { label: "Interview", tone: "success" },
  offer: { label: "Offer", tone: "success" },
  rejected: { label: "Rejected", tone: "danger" },
  skipped: { label: "Skipped", tone: "neutral" },
};

export const TRUST_META = {
  verified: { label: "Verified", tone: "success" },
  likely_real: { label: "Likely real", tone: "info" },
  unconfirmed: { label: "Unconfirmed", tone: "warning" },
  stale: { label: "Stale", tone: "warning" },
  suspicious: { label: "Suspicious", tone: "danger" },
};

export const COMPANY_SITE_PLATFORMS = ["greenhouse", "lever", "ashby"];

export const PLATFORM_LABELS = {
  greenhouse: "Company site",
  lever: "Company site",
  ashby: "Company site",
  linkedin: "LinkedIn",
  remoteok: "RemoteOK",
  remotive: "Remotive",
  jobicy: "Jobicy",
  himalayas: "Himalayas",
  arbeitnow: "Arbeitnow",
};
