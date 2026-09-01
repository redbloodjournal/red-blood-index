#!/usr/bin/env python3
import html
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
MOST_CLICKED = ROOT / "most-clicked.json"
TRANSLATIONS = ROOT / "translations.json"

LANGUAGES = {
    "es": {
        "name": "Español",
        "html_lang": "es",
        "dir": "ltr",
        "english": "Spanish",
        "intro": "Informes seleccionados de Red Blood Journal traducidos del original en inglés.",
        "read": "Leer traducción →",
    },
    "fa": {
        "name": "فارسی",
        "html_lang": "fa",
        "dir": "rtl",
        "english": "Persian",
        "intro": "گزارش‌های برگزیدهٔ رد بلاد ژورنال، ترجمه‌شده از نسخهٔ اصلی انگلیسی.",
        "read": "خواندن ترجمه ←",
    },
    "zh-cn": {
        "name": "简体中文",
        "html_lang": "zh-CN",
        "dir": "ltr",
        "english": "Simplified Chinese",
        "intro": "Red Blood Journal 精选报道的简体中文译文。",
        "read": "阅读译文 →",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RedBloodJournalTranslationBot/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
}

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def canonicalize_url(url):
    p = urlparse(url)
    return urlunparse((p.scheme or "https", p.netloc, p.path, "", "", ""))


def report_id_from_url(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    m = re.match(r"(\d{3,})[-_]", slug)
    return m.group(1) if m else None


def choose_report(click_data, translations):
    already = {str(x) for x in translations.get("reports", {}).keys()}
    candidates = sorted(
        click_data.get("reports", []),
        key=lambda x: int(x.get("clicks", 0)),
        reverse=True,
    )
    for item in candidates:
        url = canonicalize_url(item.get("url", ""))
        rid = report_id_from_url(url)
        if not rid:
            print("Skipping URL with no report number:", url)
            continue
        if rid in already:
            continue
        return {"id": rid, "url": url, "clicks": int(item.get("clicks", 0))}
    return None


def clean_text(value):
    value = str(value or "").replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_source(url):
    r = requests.get(url, headers=HEADERS, timeout=35, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = ""
    subtitle = ""
    author = "Red Blood"
    pub_date = ""

    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = clean_text(og["content"])

    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        subtitle = clean_text(desc["content"])

    article = (
        soup.select_one("div.available-content")
        or soup.select_one("div.body.markup")
        or soup.select_one("article")
    )

    if article is None:
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            blobs = data if isinstance(data, list) else [data]
            for blob in blobs:
                if isinstance(blob, dict) and blob.get("articleBody"):
                    body = clean_text(blob["articleBody"])
                    if len(body) >= 500:
                        return {
                            "title": title,
                            "subtitle": subtitle,
                            "author": author,
                            "date": pub_date,
                            "body": body,
                            "final_url": r.url,
                        }

    if article is None:
        raise RuntimeError("Could not locate article body in source HTML.")

    for node in article.select(
        "script,style,button,form,nav,aside,"
        ".subscription-widget,.subscribe-widget,.footer,"
        ".audio-player,.podcast-player"
    ):
        node.decompose()

    pieces = []
    for node in article.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        text = clean_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name in ("h1", "h2", "h3"):
            pieces.append("## " + text)
        elif node.name == "li":
            pieces.append("- " + text)
        elif node.name == "blockquote":
            pieces.append("> " + text)
        else:
            pieces.append(text)

    body = clean_text("\n\n".join(pieces))
    if len(body) < 500:
        raise RuntimeError("Extracted article body is unexpectedly short.")

    if not title:
        h1 = soup.find("h1")
        if h1:
            title = clean_text(h1.get_text(" ", strip=True))

    dt = soup.select_one("time[datetime]")
    if dt and dt.get("datetime"):
        pub_date = dt["datetime"][:10]

    return {
        "title": title,
        "subtitle": subtitle,
        "author": author,
        "date": pub_date,
        "body": body,
        "final_url": r.url,
    }


def parse_json_response(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def validate_translation(data, source, lang_code):
    if not isinstance(data, dict):
        raise ValueError("translation is not an object")
    if not str(data.get("title", "")).strip():
        raise ValueError("missing title")
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("missing sections")

    bodies = []
    nonempty_sections = 0
    for sec in sections:
        if not isinstance(sec, dict):
            raise ValueError("invalid section")
        paras = sec.get("body", [])
        if not isinstance(paras, list):
            raise ValueError("section body is not a list")

        cleaned = [str(p).strip() for p in paras if str(p).strip()]

        # A model may occasionally emit a heading-only section.
        # That is harmless, so validate the translation as a whole
        # instead of failing the entire report for one empty section.
        if cleaned:
            nonempty_sections += 1
            bodies.extend(cleaned)

    if nonempty_sections == 0:
        raise ValueError("translation has no non-empty sections")

    joined = "\n".join(bodies)
    if len(joined) < max(350, int(len(source["body"]) * 0.35)):
        raise ValueError("translation is suspiciously short")

    if lang_code == "fa" and len(re.findall(r"[\u0600-\u06FF]", joined)) < 80:
        raise ValueError("not enough Persian script")
    if lang_code == "zh-cn" and len(re.findall(r"[\u4e00-\u9fff]", joined)) < 80:
        raise ValueError("not enough Chinese characters")
    if lang_code == "es":
        lower = " " + joined.lower() + " "
        markers = sum(lower.count(w) for w in [" que ", " de ", " la ", " el ", " una ", " los "])
        if markers < 10:
            raise ValueError("Spanish language sanity check failed")


def translate_one(client, source, lang_code):
    lang = LANGUAGES[lang_code]
    prompt = f"""
Translate this Red Blood Journal report from English into {lang['english']}.

Rules:
- Translate faithfully. Do not summarize, shorten, expand, fact-check, soften, or add claims.
- Preserve the argument, tone, uncertainty, metaphors, section order, and emphasis.
- Use natural {lang['english']}, not awkward word-for-word phrasing.
- Preserve the report number exactly.
- Preserve the author name Red Blood.
- Do not translate URLs.
- Return ONLY valid JSON, without Markdown fences.

JSON shape:
{{
  "title": "translated full title",
  "subtitle": "translated subtitle or empty string",
  "author": "Red Blood",
  "date": "natural translated/display date",
  "sections": [
    {{"heading": "translated heading", "body": ["paragraph 1", "paragraph 2"]}}
  ]
}}

Every substantive source paragraph must appear exactly once. Do not create empty sections; if a heading has no body text, merge it with the following section.

SOURCE TITLE:
{source['title']}

SOURCE SUBTITLE:
{source['subtitle']}

SOURCE DATE:
{source['date']}

SOURCE BODY:
{source['body']}
""".strip()

    last_error = None
    for attempt in range(2):
        response = client.responses.create(model=MODEL, input=prompt)
        try:
            data = parse_json_response(response.output_text)
            validate_translation(data, source, lang_code)
            return data
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                prompt += "\n\nPrevious output failed validation: " + str(exc) + ". Return corrected JSON only."
                time.sleep(1)
    raise RuntimeError(f"{lang_code} translation failed: {last_error}")


def page_html(source_url, lang_code, t):
    lang = LANGUAGES[lang_code]
    blocks = []
    for sec in t["sections"]:
        heading = html.escape(str(sec.get("heading", "")).strip())
        paras = "".join(
            "<p>" + html.escape(str(p).strip()) + "</p>"
            for p in sec.get("body", [])
            if str(p).strip()
        )
        if heading:
            blocks.append("<section><h2>" + heading + "</h2>" + paras + "</section>")
        else:
            blocks.append("<section>" + paras + "</section>")

    return f"""<!doctype html>
<html lang="{lang['html_lang']}" dir="{lang['dir']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(t['title'])} | Red Blood Journal</title>
<link rel="icon" type="image/png" href="/favicon.png">
<meta name="description" content="{html.escape(t.get('subtitle',''))}">
<style>
:root{{--bg:#0B0B0B;--panel:#171717;--line:#3A3A3A;--text:#F4EFE3;--muted:#C9C2B8;--red:#D71920;--gold:#E0B323}}
*{{box-sizing:border-box}}
body{{margin:0;background:linear-gradient(180deg,#171717,#0B0B0B);color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.75}}
a{{color:inherit;text-decoration:none}}
.top{{border-bottom:1px solid var(--line);background:#090909;position:sticky;top:0;z-index:10}}
.nav{{width:min(980px,calc(100% - 28px));margin:auto;min-height:68px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:10px 0}}
.brand{{font-weight:900;letter-spacing:.10em}}
.links{{margin-inline-start:auto;display:flex;gap:16px;white-space:nowrap}}
.container{{width:min(860px,calc(100% - 28px));margin:auto}}
.hero{{padding:58px 0 24px}}
.kicker{{color:var(--red);font-weight:900;letter-spacing:.12em;font-size:12px}}
h1{{font-size:clamp(36px,6vw,62px);line-height:1.08;margin:12px 0}}
.meta{{color:var(--muted);margin-bottom:24px}}
.source{{display:inline-block;border:1px solid var(--gold);color:var(--gold);border-radius:999px;padding:8px 12px;font-weight:800;font-size:13px}}
article{{border-top:1px solid var(--line);padding-top:12px}}
section{{padding:18px 0}}
h2{{font-size:28px;line-height:1.25;margin:0 0 12px}}
p{{font-size:17px;margin:0 0 14px}}
footer{{border-top:1px solid var(--line);margin-top:52px;padding:30px 0 48px;color:var(--muted)}}
@media(max-width:700px){{.links{{width:100%;margin-inline-start:0;overflow-x:auto}}}}
</style>
</head>
<body>
<header class="top"><div class="nav">
<a class="brand" href="/">♦ RED BLOOD JOURNAL 🌊</a>
<nav class="links"><a href="/">English</a><a href="/es/">Español</a><a href="/fa/">فارسی</a><a href="/zh-cn/">简体中文</a></nav>
</div></header>
<main class="container">
<section class="hero">
<div class="kicker">🩸 REDBLOODJOURNAL.COM</div>
<h1>{html.escape(t['title'])}</h1>
<div class="meta">{html.escape(t.get('subtitle',''))}<br><strong>{html.escape(t.get('author','Red Blood'))}</strong><br>{html.escape(t.get('date',''))}</div>
<a class="source" href="{html.escape(source_url)}" target="_blank" rel="noopener">English source edition ↗</a>
</section>
<article>
{''.join(blocks)}
<p><strong>🩸🌊✨ Fantastic!</strong></p>
</article>
</main>
<footer><div class="container"><strong>🩸 RedBloodJournal.com 🩸</strong><br>In an Ocean of Love and Positivity.<br>🩸🌊✨ Fantastic!</div></footer>
</body></html>"""


def landing_html(lang_code, translations):
    lang = LANGUAGES[lang_code]
    sortable = []
    for rid, rec in translations.get("reports", {}).items():
        path = rec.get("translations", {}).get(lang_code)
        if not path:
            continue
        numeric = int(rid) if str(rid).isdigit() else 0
        sortable.append((rec.get("translated_on", ""), numeric, str(rid), rec))
    sortable.sort(reverse=True)

    cards = []
    for _, _, rid, rec in sortable:
        title = rec.get("translated_titles", {}).get(lang_code) or rec.get("title", f"Report #{rid}")
        path = rec["translations"][lang_code]
        card = (
            '<a class="card" href="' + html.escape(path) + '">'
            '<div class="num">#' + html.escape(rid) + '</div>'
            '<h2>' + html.escape(title) + '</h2>'
            '<div class="go">' + lang["read"] + '</div>'
            '</a>'
        )
        cards.append(card)

    return f"""<!doctype html>
<html lang="{lang['html_lang']}" dir="{lang['dir']}">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{lang['name']} | Red Blood Journal</title><link rel="icon" href="/favicon.png">
<style>
body{{margin:0;background:#0B0B0B;color:#F4EFE3;font-family:Inter,system-ui,sans-serif;line-height:1.6}}
a{{color:inherit;text-decoration:none}}.wrap{{width:min(1000px,calc(100% - 28px));margin:auto}}
header{{border-bottom:1px solid #3A3A3A;padding:18px 0}}nav{{display:flex;gap:16px;flex-wrap:wrap}}
.hero{{padding:58px 0 24px}}h1{{font-size:clamp(42px,7vw,70px);margin:0 0 12px}}p{{color:#C9C2B8}}
.card{{display:block;border:1px solid #3A3A3A;background:#171717;border-radius:18px;padding:22px;margin:22px 0}}
.card:hover{{border-color:#E0B323}}.num{{color:#D71920;font-weight:900}}.go{{color:#E0B323;font-weight:800}}
</style></head>
<body>
<header><div class="wrap"><a href="/"><strong>♦ RED BLOOD JOURNAL 🌊</strong></a>
<nav><a href="/">English</a><a href="/es/">Español</a><a href="/fa/">فارسی</a><a href="/zh-cn/">简体中文</a></nav></div></header>
<main class="wrap"><section class="hero"><h1>{lang['name']}</h1><p>{lang['intro']}</p></section>
{''.join(cards) if cards else '<p>No translations published yet.</p>'}
</main></body></html>"""


def main():
    if not MOST_CLICKED.exists():
        raise SystemExit("most-clicked.json is missing.")
    if not TRANSLATIONS.exists():
        raise SystemExit("translations.json is missing.")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not configured.")

    click_data = load_json(MOST_CLICKED)
    translations = load_json(TRANSLATIONS)
    chosen = choose_report(click_data, translations)

    if not chosen:
        print("No untranslated numbered reports remain in most-clicked.json.")
        return 0

    print(f"Selected #{chosen['id']} ({chosen['clicks']} clicks): {chosen['url']}")
    source = extract_source(chosen["url"])
    print("Fetched source:", source["title"])
    print("Source body length:", len(source["body"]))

    client = OpenAI()
    results = {}
    for code in LANGUAGES:
        print("Translating", code, "with", MODEL)
        results[code] = translate_one(client, source, code)

    rid = chosen["id"]
    translated_on = date.today().isoformat()

    # No files are written until all 3 translations have passed validation.
    for code, translated in results.items():
        report_dir = ROOT / code / rid
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "index.html").write_text(
            page_html(chosen["url"], code, translated),
            encoding="utf-8",
        )

    translations.setdefault("reports", {})[rid] = {
        "title": source["title"] or f"Report #{rid}",
        "original": chosen["url"],
        "clicks_when_selected": chosen["clicks"],
        "translated_on": translated_on,
        "status": "published",
        "translations": {code: f"/{code}/{rid}/" for code in LANGUAGES},
        "translated_titles": {code: results[code]["title"] for code in LANGUAGES},
    }
    translations["selection_rule"] = "highest-clicked untranslated numbered report from most-clicked.json"
    write_json(TRANSLATIONS, translations)

    for code in LANGUAGES:
        folder = ROOT / code
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(landing_html(code, translations), encoding="utf-8")

    print(f"Published #{rid} in Spanish, Persian, and Simplified Chinese.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
