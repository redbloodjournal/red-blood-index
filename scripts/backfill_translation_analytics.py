#!/usr/bin/env python3
"""Backfill GA4 on the published multilingual HTML archive. Safe to rerun."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("es", "fa", "zh-cn", "ar")
MEASUREMENT_ID = "G-16V1EX0PXJ"
TAG = """<!-- Google Analytics: Red Blood Journal multilingual pages -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-16V1EX0PXJ"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-16V1EX0PXJ');
</script>"""

def main():
    updated = 0
    present = 0
    for lang in LANGS:
        directory = ROOT / lang
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.html")):
            html = path.read_text(encoding="utf-8")
            if MEASUREMENT_ID in html:
                present += 1
                continue
            if "<head>" not in html:
                raise RuntimeError(f"No <head> element: {path}")
            html = html.replace("<head>", "<head>\n" + TAG, 1)
            path.write_text(html, encoding="utf-8")
            updated += 1
            print(f"GA4 added: {path.relative_to(ROOT)}")
    print(f"GA4 backfill completed: {updated} updated, {present} already configured")

if __name__ == "__main__":
    main()
