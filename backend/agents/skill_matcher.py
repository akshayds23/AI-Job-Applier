"""Deterministic skill extraction and match scoring.

This module deliberately contains no LLM calls. It serves three purposes:

1. A cheap pre-filter so we only spend tokens on plausible jobs.
2. A real fallback when no LLM provider is configured or the provider is down -
   the app still produces genuine, explainable scores instead of a constant.
3. A cross-check that keeps the LLM honest (see ``agents.guardrails``).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Canonical skill vocabulary with the aliases that appear in real job posts.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "Python": ("python", "python3", "py"),
    "JavaScript": ("javascript", "js", "es6", "ecmascript"),
    "TypeScript": ("typescript", "ts"),
    "Java": ("java",),
    "Go": ("golang", "go lang"),
    "Rust": ("rust",),
    "C++": ("c++", "cpp"),
    "C#": ("c#", "csharp", ".net", "dotnet"),
    "Ruby": ("ruby", "rails", "ruby on rails"),
    "PHP": ("php", "laravel"),
    "Scala": ("scala",),
    "Kotlin": ("kotlin",),
    "Swift": ("swift", "ios"),
    "SQL": ("sql", "t-sql", "pl/sql"),
    "R": ("r language", " r,"),
    "Bash": ("bash", "shell scripting", "zsh"),
    "React": ("react", "react.js", "reactjs"),
    "Next.js": ("next.js", "nextjs", "next js"),
    "Vue": ("vue", "vue.js", "vuejs", "nuxt"),
    "Angular": ("angular", "angularjs"),
    "Svelte": ("svelte", "sveltekit"),
    "Node.js": ("node.js", "nodejs", "node js", "express.js", "expressjs"),
    "Django": ("django",),
    "Flask": ("flask",),
    "FastAPI": ("fastapi", "fast api"),
    "Spring": ("spring boot", "springboot", "spring framework"),
    "GraphQL": ("graphql", "apollo"),
    "REST API": ("rest api", "restful", "rest apis"),
    "gRPC": ("grpc",),
    "HTML/CSS": ("html", "css", "scss", "sass", "tailwind"),
    "PostgreSQL": ("postgresql", "postgres", "psql"),
    "MySQL": ("mysql", "mariadb"),
    "MongoDB": ("mongodb", "mongo"),
    "Redis": ("redis",),
    "Elasticsearch": ("elasticsearch", "opensearch"),
    "DynamoDB": ("dynamodb",),
    "Snowflake": ("snowflake",),
    "BigQuery": ("bigquery", "big query"),
    "Vector Databases": ("pinecone", "weaviate", "qdrant", "chromadb", "pgvector", "milvus"),
    "AWS": ("aws", "amazon web services", "ec2", "s3", "lambda"),
    "Azure": ("azure", "microsoft azure"),
    "GCP": ("gcp", "google cloud"),
    "Docker": ("docker", "containeriz"),
    "Kubernetes": ("kubernetes", "k8s", "eks", "gke"),
    "Terraform": ("terraform", "infrastructure as code", "iac"),
    "CI/CD": ("ci/cd", "cicd", "github actions", "jenkins", "gitlab ci", "circleci"),
    "Linux": ("linux", "unix", "ubuntu"),
    "Kafka": ("kafka", "event streaming"),
    "RabbitMQ": ("rabbitmq", "celery"),
    "Airflow": ("airflow", "dagster", "prefect"),
    "Spark": ("spark", "pyspark", "databricks"),
    "Machine Learning": ("machine learning", "ml ", "scikit-learn", "sklearn", "xgboost"),
    "Deep Learning": ("deep learning", "neural network"),
    "PyTorch": ("pytorch", "torch"),
    "TensorFlow": ("tensorflow", "keras"),
    "NLP": ("nlp", "natural language processing", "spacy", "transformers"),
    "Computer Vision": ("computer vision", "opencv", "image recognition"),
    "LLM": ("llm", "large language model", "gpt", "claude", "gemini", "prompt engineering"),
    "LangChain": ("langchain", "langgraph", "llamaindex"),
    "RAG": ("rag", "retrieval augmented", "retrieval-augmented"),
    "MLOps": ("mlops", "model deployment", "mlflow"),
    "Data Analysis": ("data analysis", "pandas", "numpy", "analytics"),
    "Git": ("git", "github", "gitlab", "version control"),
    "Agile": ("agile", "scrum", "kanban"),
    "Testing": ("unit test", "pytest", "jest", "tdd", "integration test"),
    "Microservices": ("microservice", "distributed system"),
    "System Design": ("system design", "architecture", "scalab"),
    "Security": ("security", "oauth", "authentication", "penetration"),
    "Figma": ("figma", "ui/ux", "design system"),
    "Product Management": ("product management", "roadmap", "stakeholder"),
}

# Reverse index: alias -> canonical name, longest alias first so that
# "next.js" wins over "js".
_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    ((alias, canonical) for canonical, aliases in SKILL_ALIASES.items() for alias in aliases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)

# Whole-word matching: "scala" must not match "scalable", nor "ts" match "parts".
_ALIAS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?<![a-z0-9.])" + re.escape(alias.strip()) + r"(?![a-z0-9])"), canonical)
    for alias, canonical in _ALIAS_INDEX
    if alias.strip()
]

_SENIORITY_RANK = {"junior": 1, "mid": 2, "senior": 3, "lead": 4, "principal": 5}

_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?year", re.I)

_RESPONSIBILITY_RE = re.compile(
    r"^\s*[-*•]?\s*(?:you will|responsibilities?|what you.{0,10}ll do)?\s*(.{25,200})$", re.I
)


@dataclass
class MatchResult:
    score: float
    matched_skills: list[str]
    gap_skills: list[str]
    reasoning: str
    method: str = "heuristic"

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "matched_skills": self.matched_skills,
            "gap_skills": self.gap_skills,
            "reasoning": self.reasoning,
            "method": self.method,
        }


def extract_skills(text: str, limit: int = 40) -> list[str]:
    """Extract canonical skill names mentioned in free text."""
    if not text:
        return []
    haystack = f" {text.lower()} "
    found: list[str] = []
    for pattern, canonical in _ALIAS_PATTERNS:
        if canonical in found:
            continue
        if pattern.search(haystack):
            found.append(canonical)
            if len(found) >= limit:
                break
    return found


def canonicalise(skill: str) -> str:
    """Map a user-entered skill onto the canonical vocabulary when possible."""
    if not skill:
        return ""
    lowered = f" {skill.lower().strip()} "
    for pattern, canonical in _ALIAS_PATTERNS:
        if pattern.search(lowered):
            return canonical
    return skill.strip()


def extract_required_years(text: str) -> int | None:
    matches = _YEARS_RE.findall(text or "")
    values = [int(m) for m in matches if m.isdigit() and 0 < int(m) <= 25]
    return min(values) if values else None


def analyse_job_description(title: str, jd_text: str) -> dict:
    """Structured JD facts derived without an LLM."""
    from scrapers.base_scraper import infer_seniority

    combined = f"{title}\n{jd_text}"
    skills = extract_skills(combined)

    # Skills named in the first third of the JD are usually the hard requirements.
    head = jd_text[: max(len(jd_text) // 3, 600)]
    required = extract_skills(f"{title}\n{head}")
    preferred = [s for s in skills if s not in required]

    responsibilities: list[str] = []
    for line in (jd_text or "").splitlines():
        stripped = line.strip(" -*•\t")
        if 30 <= len(stripped) <= 200 and stripped[0:1].isalpha():
            responsibilities.append(stripped)
        if len(responsibilities) >= 8:
            break

    return {
        "required_skills": required[:15],
        "preferred_skills": preferred[:10],
        "responsibilities": responsibilities,
        "tech_stack": skills[:15],
        "seniority_level": infer_seniority(title),
        "years_experience_required": extract_required_years(jd_text),
        "education_required": _detect_education(jd_text),
        "job_type": "full_time",
        "key_qualifications": responsibilities[:5],
        "source": "heuristic",
    }


def _detect_education(text: str) -> str:
    lowered = (text or "").lower()
    if "phd" in lowered or "ph.d" in lowered or "doctorate" in lowered:
        return "phd"
    if "master" in lowered or "m.s." in lowered or "msc" in lowered:
        return "masters"
    if "bachelor" in lowered or "b.s." in lowered or "bsc" in lowered or "degree" in lowered:
        return "bachelors"
    return "none"


def score_match(
    candidate_skills: list[str],
    candidate_years: int | None,
    candidate_titles: list[str],
    target_roles: list[str],
    job_title: str,
    jd_analysis: dict,
) -> MatchResult:
    """Weighted, explainable compatibility score in the range 0-100.

    Weights: required skills 45, preferred skills 15, title alignment 25,
    seniority fit 15. Every component is bounded, so the total is too.
    """
    canonical_candidate = {canonicalise(s) for s in candidate_skills if s}
    canonical_candidate.discard("")

    required = [canonicalise(s) for s in jd_analysis.get("required_skills") or []]
    preferred = [canonicalise(s) for s in jd_analysis.get("preferred_skills") or []]
    required = [s for s in dict.fromkeys(required) if s]
    preferred = [s for s in dict.fromkeys(preferred) if s and s not in required]

    matched_required = [s for s in required if s in canonical_candidate]
    matched_preferred = [s for s in preferred if s in canonical_candidate]
    gaps = [s for s in required if s not in canonical_candidate]

    # 1. Required-skill coverage (45 pts)
    if required:
        coverage = len(matched_required) / len(required)
    else:
        # No parseable requirements - fall back to overlap with the whole stack.
        stack = {canonicalise(s) for s in jd_analysis.get("tech_stack") or []}
        coverage = len(stack & canonical_candidate) / len(stack) if stack else 0.5
    required_points = 45.0 * coverage

    # 2. Preferred-skill bonus (15 pts)
    preferred_points = 15.0 * (len(matched_preferred) / len(preferred)) if preferred else 7.5

    # 3. Title / target-role alignment (25 pts)
    title_points = 25.0 * _title_similarity(job_title, target_roles, candidate_titles)

    # 4. Seniority fit (15 pts) - penalise distance in either direction.
    job_rank = _SENIORITY_RANK.get(str(jd_analysis.get("seniority_level") or "mid").lower(), 2)
    candidate_rank = _years_to_rank(candidate_years, candidate_titles)
    seniority_points = 15.0 * max(0.0, 1.0 - abs(job_rank - candidate_rank) / 3.0)

    total = required_points + preferred_points + title_points + seniority_points

    # Years-of-experience shortfall is a hard-ish penalty recruiters apply.
    required_years = jd_analysis.get("years_experience_required")
    if required_years and candidate_years is not None and candidate_years < required_years:
        shortfall = required_years - candidate_years
        total -= min(15.0, shortfall * 4.0)

    total = max(0.0, min(100.0, total))

    reasoning = (
        f"Matched {len(matched_required)}/{len(required) or 0} required skills "
        f"({', '.join(matched_required[:6]) or 'none'}). "
        f"Title alignment {title_points / 25:.0%}, seniority fit {seniority_points / 15:.0%}."
    )
    if gaps:
        reasoning += f" Gaps: {', '.join(gaps[:5])}."

    return MatchResult(
        score=total,
        matched_skills=matched_required + matched_preferred,
        gap_skills=gaps[:10],
        reasoning=reasoning,
        method="heuristic",
    )


def _years_to_rank(years: int | None, titles: list[str]) -> int:
    from scrapers.base_scraper import infer_seniority

    if titles:
        ranks = [_SENIORITY_RANK.get(infer_seniority(t), 2) for t in titles]
        title_rank = max(ranks) if ranks else 2
    else:
        title_rank = 0

    if years is None:
        return title_rank or 2
    if years < 2:
        year_rank = 1
    elif years < 5:
        year_rank = 2
    elif years < 8:
        year_rank = 3
    elif years < 12:
        year_rank = 4
    else:
        year_rank = 5
    return max(year_rank, title_rank)


def _title_similarity(job_title: str, target_roles: list[str], candidate_titles: list[str]) -> float:
    """Token-overlap similarity against the best-matching reference title."""
    job_tokens = _title_tokens(job_title)
    if not job_tokens:
        return 0.5

    best = 0.0
    for reference in list(target_roles or []) + list(candidate_titles or []):
        reference_tokens = _title_tokens(reference)
        if not reference_tokens:
            continue
        overlap = len(job_tokens & reference_tokens)
        if not overlap:
            continue
        # Dice coefficient keeps short titles from dominating.
        similarity = 2 * overlap / (len(job_tokens) + len(reference_tokens))
        best = max(best, similarity)

    # A total mismatch still scores slightly above zero - transferable skills.
    return min(1.0, best * 1.35) if best else 0.15


_TITLE_NOISE = {
    "senior", "junior", "lead", "staff", "principal", "sr", "jr", "i", "ii", "iii",
    "remote", "hybrid", "onsite", "contract", "fulltime", "full", "time", "the", "and",
    "of", "for", "at", "in", "to", "with", "a", "an", "m", "f", "d", "w", "x",
}


def _title_tokens(title: str) -> set[str]:
    tokens = re.split(r"[^a-z0-9+#.]+", (title or "").lower())
    return {t for t in tokens if t and t not in _TITLE_NOISE and len(t) > 1}


def rank_projects(projects: list[dict], jd_skills: list[str], limit: int = 3) -> list[dict]:
    """Order projects by relevance to the JD's technology stack."""
    wanted = {canonicalise(s) for s in jd_skills if s}
    scored: list[tuple[float, dict]] = []

    for project in projects:
        technologies = project.get("technologies") or []
        text = f"{project.get('name', '')} {project.get('description', '')} {' '.join(map(str, technologies))}"
        project_skills = {canonicalise(s) for s in extract_skills(text)}
        overlap = len(project_skills & wanted)
        # Slight preference for richer descriptions when relevance ties.
        detail_bonus = math.log1p(len(project.get("description") or "")) / 20
        scored.append((overlap + detail_bonus, project))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [project for _score, project in scored[:limit]]
