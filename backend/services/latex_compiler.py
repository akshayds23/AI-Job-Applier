"""Compile resume .tex files to PDF with Tectonic (a self-contained LaTeX engine).

Tectonic fetches only the packages a document needs and caches them, so the
first compile is slow (package download) and later ones take a few seconds.
`warm_up()` runs once at startup (and at Docker build time) to fill the cache.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import PROJECT_ROOT
from core.logging_config import get_logger

logger = get_logger("latex")

COMPILE_TIMEOUT_SECONDS = 90
WARMUP_TIMEOUT_SECONDS = 900


class LatexUnavailable(RuntimeError):
    pass


# Serverless (Vercel): the build step puts a Linux binary in backend/bin and a
# pre-filled package cache in backend/.tectonic-cache. Only /tmp is writable at
# run time, so the cache is copied there on first use.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_BINARY = BACKEND_ROOT / "bin" / "tectonic"
BUNDLED_CACHE = BACKEND_ROOT / ".tectonic-cache"
RUNTIME_DIR = Path("/tmp/tectonic")
TECTONIC_VERSION = "0.17.0"
_RELEASE_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    f"tectonic%40{TECTONIC_VERSION}/tectonic-{TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz"
)


def find_tectonic() -> str | None:
    """TECTONIC_PATH, the bundled build-time copy, the local tools/ copy, a runtime download, then PATH."""
    candidates = [
        os.environ.get("TECTONIC_PATH", ""),
        str(BUNDLED_BINARY),
        str(PROJECT_ROOT / "tools" / "tectonic" / ("tectonic.exe" if os.name == "nt" else "tectonic")),
        str(RUNTIME_DIR / "tectonic"),
        shutil.which("tectonic") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def is_available() -> bool:
    from config import settings

    # Serverless hosts can fetch the binary on first use.
    return find_tectonic() is not None or settings.serverless


def _serverless_setup() -> dict[str, str]:
    """On read-only hosts: writable cache seeded from the bundle; download the binary if missing."""
    from config import settings

    if not settings.serverless:
        return {}
    cache = RUNTIME_DIR / "cache"
    if not cache.exists() and BUNDLED_CACHE.exists():
        shutil.copytree(BUNDLED_CACHE, cache)
    cache.mkdir(parents=True, exist_ok=True)
    if find_tectonic() is None:
        _download_binary()
    # Tectonic keeps its cache under $XDG_CACHE_HOME / TECTONIC_CACHE_DIR.
    return {"TECTONIC_CACHE_DIR": str(cache), "XDG_CACHE_HOME": str(RUNTIME_DIR), "HOME": str(RUNTIME_DIR)}


def _download_binary() -> None:
    import io
    import tarfile
    import urllib.request

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading Tectonic %s", TECTONIC_VERSION)
    with urllib.request.urlopen(_RELEASE_URL, timeout=60) as response:
        archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
    member = archive.getmember("tectonic")
    target = RUNTIME_DIR / "tectonic"
    with archive.extractfile(member) as source, open(target, "wb") as out:
        shutil.copyfileobj(source, out)
    target.chmod(0o755)


def _run(tex_path: Path, out_dir: Path, timeout: int) -> Path:
    env_extra = _serverless_setup()
    binary = find_tectonic()
    if binary is None:
        raise LatexUnavailable("Tectonic is not installed")
    # --untrusted disables shell-escape and other features unsafe for generated input.
    command = [binary, "-X", "compile", "--untrusted", "--outdir", str(out_dir), str(tex_path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                encoding="utf-8", errors="replace", env={**os.environ, **env_extra})
    except subprocess.TimeoutExpired as exc:
        raise LatexUnavailable(f"LaTeX compile timed out after {timeout}s") from exc
    pdf = out_dir / (tex_path.stem + ".pdf")
    if result.returncode != 0 or not pdf.exists():
        tail = "\n".join((result.stderr or result.stdout or "").strip().splitlines()[-6:])
        raise LatexUnavailable(f"LaTeX compile failed: {tail[:500]}")
    return pdf


def page_count(pdf_path: Path) -> int:
    import pymupdf

    with pymupdf.open(str(pdf_path)) as document:
        return len(document)


def compile_to_one_page(variants: list[str], out_pdf: Path, timeout: int = COMPILE_TIMEOUT_SECONDS) -> Path:
    """Compile tex variants (loosest first) until one fits a page; keep the best.

    The result is cached against a hash of the first variant, so re-downloading
    an unchanged resume does not recompile.
    """
    key = hashlib.sha256(variants[0].encode("utf-8")).hexdigest()
    key_file = out_pdf.with_suffix(".key")
    if out_pdf.exists() and key_file.exists() and key_file.read_text().strip() == key:
        return out_pdf

    with tempfile.TemporaryDirectory(prefix="resume-tex-") as tmp:
        work = Path(tmp)
        best: Path | None = None
        for index, source in enumerate(variants):
            tex = work / f"resume_{index}.tex"
            tex.write_text(source, encoding="utf-8")
            pdf = _run(tex, work, timeout)
            best = pdf
            if page_count(pdf) == 1:
                break
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(best, out_pdf)
    key_file.write_text(key)
    return out_pdf


async def compile_resume(variants: list[str], out_pdf: Path) -> Path:
    return await asyncio.to_thread(compile_to_one_page, variants, out_pdf)


_WARMUP_DOC = r"""\documentclass[10pt,a4paper]{article}
\usepackage[a4paper,margin=0.4in]{geometry}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage[dvipsnames]{xcolor}
\usepackage{fontawesome5}
\usepackage{parskip}
\begin{document}
\section{Warm up}\faPhone\ \faEnvelope\ \faMapMarker*\ \faLinkedin\ \faGithub\ \faGlobe\ \textbf{Bold} \textit{italic} $\rightarrow$
\begin{itemize}\item item\end{itemize}
\end{document}
"""


def warm_up() -> bool:
    """Download and cache every package the resume template uses."""
    if not is_available():
        logger.info("Tectonic not found - LaTeX PDFs disabled, using the built-in PDF renderer")
        return False
    with tempfile.TemporaryDirectory(prefix="tectonic-warmup-") as tmp:
        tex = Path(tmp) / "warmup.tex"
        tex.write_text(_WARMUP_DOC, encoding="utf-8")
        try:
            _run(tex, Path(tmp), WARMUP_TIMEOUT_SECONDS)
            logger.info("Tectonic ready (package cache warm)")
            return True
        except LatexUnavailable as exc:
            logger.warning("Tectonic warm-up failed: %s", exc)
            return False


if __name__ == "__main__":  # used by the Docker build to pre-fill the cache
    raise SystemExit(0 if warm_up() else 1)
