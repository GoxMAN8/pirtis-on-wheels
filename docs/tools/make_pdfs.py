"""Prints the main documents to PDF, with their Mermaid diagrams drawn, into docs/pdf/.

    python3 docs/tools/make_pdfs.py

Each Markdown file becomes a print-styled page in headless Chrome: Markdown rendered by
marked, diagrams by Mermaid 11 (the major version GitHub uses), both pinned and loaded
from a CDN. The page says when every diagram is drawn, and only then is it printed.
Needs Google Chrome and a network connection; the PDFs are rebuilt after any doc edit.
"""

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "pdf"
CHROME = os.environ.get(
    "CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
MARKED = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"

# output name -> (title, the files it prints, in order)
DOCS = {
    "readme.pdf": ("Pirtis on Wheels: the receptionist", ["README.md"]),
    "using-the-app.pdf": (
        "Using the app",
        ["docs/using-the-app.md", "docs/guide-caller.md", "docs/guide-admin.md"],
    ),
    "architecture.pdf": (
        "Architecture",
        [
            "docs/architecture/containers.md",
            "docs/architecture/data-flow.md",
            "docs/architecture/sequences.md",
            "docs/architecture/deployment.md",
        ],
    ),
    "evaluation.pdf": ("Evaluation", ["docs/evaluation.md"]),
    "data-science.pdf": ("Data science: methods and metrics", ["docs/data-science.md"]),
    "spec.pdf": ("SPEC: design, decisions and change log", ["SPEC.md"]),
    "deploy.pdf": ("Deploy", ["deploy/README.md"]),
    "build-log.pdf": ("Build log", ["BUILD_LOG.md"]),
}

STYLE = """
@page { size: A4; margin: 16mm 14mm 18mm; }
:root { --ink: #1c191b; --muted: #615c5f; --line: #cdd5d8; --ember: #b0450f; --fog: #f2f5f6; }
body { font: 10.5pt/1.5 "Helvetica Neue", Arial, sans-serif; color: var(--ink); margin: 0; }
h1, h2, h3 { line-height: 1.2; break-after: avoid; }
h1 { font-size: 20pt; border-bottom: 2px solid var(--ember); padding-bottom: 4px; margin: 0 0 10px; }
h2 { font-size: 14pt; color: var(--ember); margin-top: 22px; }
h3 { font-size: 11.5pt; margin-top: 16px; }
.doc + .doc { break-before: page; }
.cover { font-size: 9pt; color: var(--muted); margin-bottom: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; margin: 8px 0 12px; }
th, td { border: 1px solid var(--line); padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: var(--fog); }
tr { break-inside: avoid; }
code { font: 8.8pt Menlo, Consolas, monospace; background: var(--fog); padding: 0 3px; border-radius: 3px; }
pre { background: var(--fog); padding: 8px 10px; border-radius: 4px; white-space: pre-wrap; break-inside: avoid; }
pre code { padding: 0; }
blockquote { margin: 8px 0; padding: 4px 12px; border-left: 3px solid var(--ember); color: var(--muted); }
img { display: block; max-width: 100%; max-height: 105mm; margin: 6px auto; border: 1px solid var(--line); border-radius: 4px; break-inside: avoid; }
.mermaid { text-align: center; margin: 10px 0 14px; break-inside: avoid; }
.mermaid svg { max-width: 100%; max-height: 150mm; height: auto; }
a { color: var(--ink); }
hr { border: 0; border-top: 1px solid var(--line); margin: 18px 0; }
"""

SCRIPT = """
const docs = %s;
const main = document.getElementById("main");
for (const [path, md] of docs) {
  const section = document.createElement("section");
  section.className = "doc";
  section.innerHTML = marked.parse(md);
  main.append(section);
}
for (const code of document.querySelectorAll("pre code.language-mermaid")) {
  const div = document.createElement("div");
  div.className = "mermaid";
  div.textContent = code.textContent;
  code.parentElement.replaceWith(div);
}
mermaid.initialize({
  startOnLoad: false, theme: "neutral", flowchart: { useMaxWidth: true },
  themeVariables: { xyChart: { plotColorPalette: "#c24e18" } },  // the Steam theme's ember
});
mermaid.run({ querySelector: ".mermaid" })
  .then(() => { document.title = "READY"; })
  .catch((e) => { document.title = "FAILED " + e; });
"""


def page(title: str, files: list[str]) -> str:
    docs = [(f, (ROOT / f).read_text(encoding="utf-8")) for f in files]
    base = (ROOT / files[0]).parent.as_uri() + "/"  # images resolve as in the repo
    sources = ", ".join(files)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>LOADING</title><base href="{base}"><style>{STYLE}</style>
<script src="{MARKED}"></script><script src="{MERMAID}"></script></head>
<body><p class="cover">{html.escape(title)} · printed from {html.escape(sources)}</p>
<main id="main"></main><script>{SCRIPT % json.dumps(docs)}</script></body></html>"""


def main() -> None:
    if not Path(CHROME).exists():
        sys.exit(f"Chrome not found at {CHROME}; set CHROME to its path")
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pdfs-"))
    try:
        for name, (title, files) in DOCS.items():
            src = tmp / (name + ".html")
            src.write_text(page(title, files), encoding="utf-8")
            # the page prints only once every diagram is drawn: wait for its title
            dump = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=30000",
                 "--allow-file-access-from-files", "--dump-dom", src.as_uri()],
                capture_output=True, text=True, timeout=180,
            )
            if "<title>READY</title>" not in dump.stdout:
                sys.exit(f"{name}: the diagrams did not finish drawing")
            subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=30000",
                 "--allow-file-access-from-files", "--no-pdf-header-footer",
                 f"--print-to-pdf={OUT / name}", src.as_uri()],
                capture_output=True, timeout=180, check=True,
            )
            print(f"{OUT.relative_to(ROOT) / name}  <- {', '.join(files)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
