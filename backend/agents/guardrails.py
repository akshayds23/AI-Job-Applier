"""Truthfulness guardrails.

Unlike a pure LLM "check", this agent *enforces*: it strips fabricated skills,
restores altered employers and dates, and flags invented metrics before the
content can reach a generated resume.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agents.skill_matcher import canonicalise
from core.logging_config import get_logger

logger = get_logger("agent.guardrails")

# Numbers that read like an achievement metric rather than a date or version.
_METRIC_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*%|[$₹€£]\s?\d[\d,.]*\s*[kmb]?\b|\b\d[\d,.]*\s*[kmb]?\+"
    r"|\b\d[\d,.]*\s*(?:x|users|customers|requests|records|hours|ms|qps|rps|schools|learners|students|clients)\b)",
    re.I,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# "over seven years", "8+ years of experience", "a decade of"
_EXPERIENCE_CLAIM_RE = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|a decade)\s*\+?\s*"
    r"(?:-\s*\d{1,2}\s*)?years?\b",
    re.I,
)
_CLAIM_VERB_RE = re.compile(
    r"\b(i (?:have|has|had|built|led|delivered|designed|developed|implemented|managed|shipped|architected|owned)"
    r"|my (?:experience|background|work) (?:with|in|on)"
    r"|hands-on (?:experience )?with"
    r"|expertise in|proficient in|specialis?ed in|specializ?ed in)\b",
    re.I,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "a decade": 10,
}


_MIN_GROUNDING = 0.35
_GROUNDING_STOPWORDS = {
    "with", "that", "this", "from", "have", "were", "will", "into", "their", "across", "while",
    "ensure", "ensuring", "including", "within", "using", "based", "also", "more", "over", "through",
}


def _content_words(text: str) -> set[str]:
    """Meaningful words (crude stemming) used to check a rewrite against its source."""
    text = text.lower().replace("‑", "-").replace("‐", "-")
    return {
        word.rstrip("s")
        for word in re.findall(r"[a-z0-9+#]+", text)
        if len(word) >= 4 and word not in _GROUNDING_STOPWORDS
    }


def near_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    """Two lines saying the same thing (Jaccard overlap of content words)."""
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def _to_int(value: str) -> int | None:
    text = str(value).strip().lower()
    if text.isdigit():
        number = int(text)
        return number if 0 < number <= 50 else None
    return _WORD_NUMBERS.get(text)


@dataclass
class GuardrailReport:
    is_valid: bool = True
    violations: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "violations": self.violations,
            "corrections": self.corrections,
        }


class GuardrailsAgent:
    """Validates and repairs tailored resume content against the source profile."""

    async def verify(
        self,
        original_skills: list[dict | str],
        original_experience: list[dict],
        tailored_output: dict[str, Any],
    ) -> dict[str, Any]:
        report = self.enforce(original_skills, original_experience, tailored_output)
        return report.to_dict()

    def enforce(
        self,
        original_skills: list[dict | str],
        original_experience: list[dict],
        tailored: dict[str, Any],
        extra_sources: list[str] | None = None,
    ) -> GuardrailReport:
        """Mutates ``tailored`` in place, removing anything unsupported.

        ``extra_sources`` is other candidate-written text (summary, achievements)
        whose numbers may legitimately be reused.
        """
        report = GuardrailReport()

        owned = {str(s.get("name") if isinstance(s, dict) else s).strip() for s in original_skills}
        owned.discard("")
        owned_lookup = {s.lower() for s in owned} | {canonicalise(s).lower() for s in owned}

        # 1. skills_order must be a subset of the candidate's real skills.
        kept_skills: list[str] = []
        for skill in tailored.get("skills_order") or []:
            name = str(skill).strip()
            if not name:
                continue
            if name.lower() in owned_lookup or canonicalise(name).lower() in owned_lookup:
                kept_skills.append(name)
            else:
                report.violations.append(f"Removed unclaimed skill: {name}")
        if report.violations:
            tailored["skills_order"] = kept_skills
            report.corrections.append("skills_order filtered to claimed skills")

        # 2. Bullets may not introduce metrics that are absent from the source.
        source_metrics = self._collect_metrics(original_experience)
        for text in extra_sources or []:
            source_metrics.update(self._normalise_metric(m) for m in _METRIC_RE.findall(str(text)))

        def unverified(text: str) -> list[str]:
            return [m for m in _METRIC_RE.findall(text) if self._normalise_metric(m) not in source_metrics]

        # Achievements and summary sentences carrying invented numbers are dropped.
        achievements = []
        for item in tailored.get("achievements") or []:
            bad = unverified(str(item))
            if bad:
                report.violations.append(f"Dropped achievement with unverifiable metric {bad[0]!r}")
            else:
                achievements.append(item)
        tailored["achievements"] = achievements
        summary_sentences = _SENTENCE_RE.split(str(tailored.get("tailored_summary") or ""))
        kept_sentences = [s for s in summary_sentences if not unverified(s)]
        if len(kept_sentences) != len(summary_sentences):
            report.violations.append("Removed a summary sentence with an unverifiable metric")
            tailored["tailored_summary"] = " ".join(kept_sentences)
        bullets = tailored.get("tailored_bullets")
        if isinstance(bullets, dict):
            for exp_id, exp_bullets in list(bullets.items()):
                if not isinstance(exp_bullets, list):
                    continue
                checked: list[str] = []
                rejected: list[str] = []
                for bullet in exp_bullets:
                    text = str(bullet)
                    invented = [
                        m for m in _METRIC_RE.findall(text) if self._normalise_metric(m) not in source_metrics
                    ]
                    if invented:
                        # Deleting the number mid-sentence leaves broken grammar,
                        # so drop the whole bullet - the source has others.
                        report.violations.append(
                            f"Role {exp_id}: dropped a bullet with the unverifiable metric {invented[0]!r}"
                        )
                        rejected.append(text)
                        continue
                    if text:
                        checked.append(text)

                # Never leave a role with no bullets at all: fall back to the
                # candidate's own original wording.
                if not checked and rejected:
                    original = next(
                        (
                            [str(b) for b in (e.get("bullets") or [])][:3]
                            for e in original_experience
                            if str(e.get("id", "")) == str(exp_id)
                        ),
                        [],
                    )
                    checked = original
                    report.corrections.append(f"Restored original bullets for role {exp_id}")

                bullets[exp_id] = checked

        # 2b. Bullets must be grounded in what the candidate wrote for that role.
        #     Metrics are not the only way to embellish: "reduced time-to-market"
        #     is an invented outcome too. A rewrite has to reuse most of the
        #     role's own vocabulary; otherwise fall back to the original line.
        if isinstance(bullets, dict):
            for exp_id, exp_bullets in list(bullets.items()):
                role = next((e for e in original_experience if str(e.get("id", "")) == str(exp_id)), None)
                if role is None or not isinstance(exp_bullets, list):
                    continue
                own_lines = [str(b) for b in (role.get("bullets") or []) if str(b).strip()]
                source = _content_words(
                    " ".join(own_lines + [str(role.get("title") or ""), str(role.get("company") or "")])
                )
                grounded = []
                for bullet in exp_bullets:
                    words = _content_words(str(bullet))
                    overlap = len(words & source) / max(1, len(words))
                    if overlap >= _MIN_GROUNDING:
                        grounded.append(bullet)
                    else:
                        report.violations.append(f"Role {exp_id}: dropped ungrounded bullet ({overlap:.0%}): {str(bullet)[:70]}")
                # Top up with the candidate's own wording rather than leave a role thin.
                for line in own_lines:
                    if len(grounded) >= max(2, min(len(exp_bullets), len(own_lines))):
                        break
                    if not any(near_duplicate(line, g) for g in grounded):
                        grounded.append(line)
                bullets[exp_id] = grounded

        # 2c. Summary sentences must also come from the candidate's material.
        summary_text = str(tailored.get("tailored_summary") or "")
        if summary_text:
            corpus = _content_words(" ".join(
                [str(t) for t in (extra_sources or [])]
                + [str(e.get("title") or "") + " " + " ".join(str(b) for b in (e.get("bullets") or []))
                   for e in original_experience]
                + list(owned)
            ))
            sentences = [x for x in _SENTENCE_RE.split(summary_text) if x.strip()]
            kept = [x for x in sentences if len(_content_words(x) & corpus) / max(1, len(_content_words(x))) >= _MIN_GROUNDING]
            if len(kept) != len(sentences):
                report.violations.append(f"Removed {len(sentences) - len(kept)} ungrounded summary sentence(s)")
                original_summary = str((extra_sources or [""])[0])
                tailored["tailored_summary"] = " ".join(kept) if len(kept) >= 2 else original_summary

        # 2d. Each "|" phrase of the headline must describe something the candidate has.
        headline = str(tailored.get("headline") or "")
        if headline:
            corpus = _content_words(" ".join(
                [str(t) for t in (extra_sources or [])]
                + [str(e.get("title") or "") + " " + " ".join(str(b) for b in (e.get("bullets") or []))
                   for e in original_experience]
                + list(owned)
            ))
            parts = [p.strip() for p in headline.split("|") if p.strip()]
            kept_parts = [p for p in parts if not _content_words(p) or len(_content_words(p) & corpus) / len(_content_words(p)) >= 0.5]
            if len(kept_parts) != len(parts):
                report.violations.append(f"Removed headline phrase(s): {', '.join(p for p in parts if p not in kept_parts)}")
                tailored["headline"] = " | ".join(kept_parts) if len(kept_parts) >= 2 else ""

        # 3. Employer names, titles and dates must survive untouched. The
        #    tailoring contract only allows bullet rewrites, so any experience
        #    id we do not recognise is dropped rather than trusted.
        valid_ids = {str(e.get("id", i)) for i, e in enumerate(original_experience)}
        if isinstance(bullets, dict):
            for exp_id in list(bullets):
                if str(exp_id) not in valid_ids:
                    bullets.pop(exp_id)
                    report.violations.append(f"Dropped bullets for unknown experience id {exp_id}")

        # 4. The summary must not claim a year the profile never mentions.
        summary = str(tailored.get("tailored_summary") or "")
        profile_years = {
            year for exp in original_experience for year in _YEAR_RE.findall(str(exp.get("start_date", "")) + str(exp.get("end_date", "")))
        }
        for year in _YEAR_RE.findall(summary):
            if profile_years and year not in profile_years:
                report.violations.append(f"Summary references year {year} not present in the profile")
                break

        report.is_valid = not report.violations
        if report.violations:
            logger.info("Guardrails corrected %d issue(s)", len(report.violations))
        return report

    def check_cover_letter(
        self,
        letter: str,
        *,
        candidate_years: int | None,
        gap_skills: list[str],
    ) -> tuple[str, GuardrailReport]:
        """Catch the two fabrications letter-writing models reliably produce:
        inflating years of experience, and claiming a known gap skill.

        Offending sentences are removed rather than rewritten, so the letter
        stays truthful without another model round-trip.
        """
        report = GuardrailReport()
        if not letter:
            return letter, report

        gap_lookup = {g.strip().lower() for g in gap_skills if g and len(g.strip()) > 2}
        gap_lookup |= {canonicalise(g).lower() for g in gap_skills if g}
        gap_lookup.discard("")

        kept: list[str] = []
        for sentence in _SENTENCE_RE.split(letter):
            stripped = sentence.strip()
            if not stripped:
                continue
            lowered = stripped.lower()

            # 1. Overstated tenure.
            overstated = False
            for raw_years in _EXPERIENCE_CLAIM_RE.findall(stripped):
                claimed = _to_int(raw_years)
                if claimed is None:
                    continue
                if candidate_years is None or claimed > candidate_years:
                    overstated = True
                    report.violations.append(
                        f"Removed sentence claiming {claimed} years of experience "
                        f"(profile states {candidate_years if candidate_years is not None else 'none'})"
                    )
                    break
            if overstated:
                continue

            # 2. Claiming a skill the scorer already flagged as missing.
            claimed_gap = next(
                (
                    gap
                    for gap in gap_lookup
                    if gap in lowered and _CLAIM_VERB_RE.search(stripped)
                ),
                None,
            )
            if claimed_gap:
                report.violations.append(f"Removed sentence claiming gap skill: {claimed_gap}")
                continue

            kept.append(stripped)

        if report.violations:
            cleaned = self._rejoin(kept)
            report.corrections.append("Removed unsupported claims from the cover letter")
            report.is_valid = False
            logger.info("Guardrails cleaned %d cover-letter claim(s)", len(report.violations))
            return cleaned, report

        return letter, report

    @staticmethod
    def _rejoin(sentences: list[str]) -> str:
        """Reassemble sentences, preserving paragraph breaks."""
        text = " ".join(sentences)
        text = re.sub(r"\s+\n", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @staticmethod
    def _collect_metrics(experiences: list[dict]) -> set[str]:
        metrics: set[str] = set()
        for exp in experiences:
            blob = " ".join(
                [str(exp.get("description") or "")] + [str(b) for b in (exp.get("bullets") or [])]
            )
            for match in _METRIC_RE.findall(blob):
                metrics.add(GuardrailsAgent._normalise_metric(match))
        return metrics

    @staticmethod
    def _normalise_metric(value: str) -> str:
        return re.sub(r"[\s,]", "", str(value)).lower()
