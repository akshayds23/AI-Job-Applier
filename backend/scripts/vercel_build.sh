#!/usr/bin/env bash
# Vercel build step for the backend service: bundle a Linux Tectonic binary and a
# pre-filled LaTeX package cache, so resume PDFs compile in seconds on a cold start.
# Failure here is not fatal: at run time the binary is downloaded on first use and
# PDFs fall back to the built-in renderer until LaTeX is ready.
set -u
cd "$(dirname "$0")/.."

VERSION=0.17.0
URL="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${VERSION}/tectonic-${VERSION}-x86_64-unknown-linux-musl.tar.gz"

mkdir -p bin .tectonic-cache
if [ ! -x bin/tectonic ]; then
  curl -fsSL "$URL" | tar -xz -C bin tectonic || { echo "Tectonic download failed - continuing without it"; exit 0; }
fi

WARM=$(mktemp -d)
cat > "$WARM/warmup.tex" <<'TEX'
\documentclass[10pt,a4paper]{article}
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
TEX
TECTONIC_CACHE_DIR="$PWD/.tectonic-cache" XDG_CACHE_HOME="$PWD/.tectonic-cache" \
  ./bin/tectonic -X compile --untrusted --outdir "$WARM" "$WARM/warmup.tex" >/dev/null 2>&1 \
  && echo "Tectonic cache ready ($(du -sh .tectonic-cache | cut -f1))" \
  || echo "Tectonic warm-up failed - packages will download on first use"
rm -rf "$WARM"
exit 0
