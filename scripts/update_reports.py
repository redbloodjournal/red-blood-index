#!/usr/bin/env python3

import json
import re
import urllib.request
import xml.etree.ElementTree as ET

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITEMAP_URL = "https://redblood.win/sitemap.xml"
OUTPUT = Path("reports.json")

# We only need images for the newest reports shown on the homepage.
LATEST_TO_ENRICH = 12


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RedBloodJournalArchiveBot/1.0"
        }
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def extract_id(slug):
    first = slug.split("-", 1)[0]

    if re.fullmatch(r"\d+", first):
        return first

    if re.fullmatch(r"[A-Za-z]+\d+", first):
        return first.upper()

    return ""


def title_from_slug(slug, rid):
    base = (
        slug[len(rid) + 1:]
        if rid and slug.lower().startswith(rid.lower() + "-")
        else slug
    )

    return base.replace("-", " ").strip().title()


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = ""
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return

        data = dict(attrs)

        prop = (
            data.get("property", "")
            or data.get("name", "")
        ).lower()

        content = data.get("content", "").strip()

        if prop == "og:image" and content and not self.image:
            self.image = content

        if prop == "og:title" and content and not self.title:
            self.title = content


def get_article_metadata(url):
    try:
        page = fetch(url).decode("utf-8", errors="ignore")

        parser = MetaParser()
        parser.feed(page)

        return {
            "image": parser.image,
            "title": parser.title
        }

    except Exception as error:
        print(f"Could not enrich {url}: {error}")

        return {
            "image": "",
            "title": ""
        }


def main():
    root = ET.fromstring(fetch(SITEMAP_URL))

    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9"
    }

    out = []
    seen = set()

    for node in root.findall("sm:url", ns):

        loc = node.find("sm:loc", ns)
        lm = node.find("sm:lastmod", ns)

        if loc is None or not loc.text:
            continue

        url = loc.text.strip()

        if "/p/" not in url or url in seen:
            continue

        seen.add(url)

        slug = urlparse(url).path.split("/p/", 1)[1]
        rid = extract_id(slug)

        out.append({
            "id": rid,
            "title": title_from_slug(slug, rid),
            "subtitle": "",
            "url": url,
            "image": "",
            "category": "Unclassified",
            "tags": [],
            "page": 0,
            "lastmod": (
                lm.text.strip()
                if lm is not None and lm.text
                else ""
            )
        })

    # Put newest modified publications first.
    out.sort(
        key=lambda item: item.get("lastmod", ""),
        reverse=True
    )

    # Retrieve real Substack title and cover for newest publications.
    for report in out[:LATEST_TO_ENRICH]:

        print(f"Getting cover: {report['url']}")

        meta = get_article_metadata(report["url"])

        if meta["image"]:
            report["image"] = meta["image"]

        if meta["title"]:
            report["title"] = meta["title"]

    OUTPUT.write_text(
        json.dumps(
            out,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    images_found = sum(
        1 for report in out[:LATEST_TO_ENRICH]
        if report.get("image")
    )

    print(f"Wrote {len(out)} publications to {OUTPUT}")
    print(
        f"Found cover images for "
        f"{images_found} of the newest "
        f"{min(LATEST_TO_ENRICH, len(out))} publications"
    )


if __name__ == "__main__":
    main()
