#!/usr/bin/env python3
"""Convert a Markdown file to PDF (HTML + Chrome/Edge headless)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import markdown


def _browser() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    for name in ("chrome", "msedge", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def md_to_html(md_path: Path, html_path: Path, title: str) -> None:
    body = markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "toc"],
    )
    html_path.write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ margin: 18mm 16mm; }}
  body {{ font-family: Segoe UI, Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.45; color: #111; margin: 0; padding: 12mm 14mm; }}
  h1 {{ font-size: 22pt; border-bottom: 2px solid #333; padding-bottom: 0.25em; }}
  h2 {{ font-size: 15pt; margin-top: 1.4em; page-break-after: avoid; }}
  h3 {{ font-size: 12pt; margin-top: 1.1em; }}
  pre, code {{ font-family: Consolas, monospace; font-size: 9pt; }}
  pre {{ background: #f4f4f4; border: 1px solid #ddd; padding: 0.75em 1em; white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }}
  code {{ background: #f0f0f0; padding: 0.1em 0.35em; border-radius: 3px; }}
  pre code {{ background: transparent; padding: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4em 0.6em; text-align: left; }}
  th {{ background: #eee; }}
  hr {{ border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }}
  a {{ color: #0645ad; }}
</style>
</head>
<body>
{body}
</body>
</html>""",
        encoding="utf-8",
    )


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    browser = _browser()
    if browser is None:
        raise SystemExit("No Chrome or Edge found for headless PDF export.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    url = html_path.resolve().as_uri()
    cmd = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path.resolve()}",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Markdown to PDF.")
    parser.add_argument("markdown", type=Path, help="Input .md file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .pdf (default: same name as input)",
    )
    args = parser.parse_args()
    md_path = args.markdown.resolve()
    if not md_path.is_file():
        print(f"Not found: {md_path}", file=sys.stderr)
        return 1
    pdf_path = (args.output or md_path.with_suffix(".pdf")).resolve()
    html_path = pdf_path.with_suffix(".html")
    title = md_path.stem.replace("-", " ")
    md_to_html(md_path, html_path, title)
    html_to_pdf(html_path, pdf_path)
    print(pdf_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
