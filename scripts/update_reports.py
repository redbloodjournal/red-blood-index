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

# Number of newest reports whose Substack pages are checked each run.
# This retrieves current titles, covers, and tags.
LATEST_TO_ENRICH = 25


CATEGORY_RULES = {
    "Politics & Geopolitics": [
        "politics",
        "geopolitics",
        "iran",
        "israel",
        "trump",
        "government",
        "war",
        "military",
        "diplomacy",
        "foreign policy",
        "sovereignty",
    ],

    "Power, Intelligence & Media": [
        "intelligence",
        "cia",
        "fbi",
        "media",
        "propaganda",
        "censorship",
        "shadowban",
        "shadowbanning",
        "surveillance",
        "influence",
        "deep state",
    ],

    "Money, Economics & Work": [
        "money",
        "economics",
        "economy",
        "banking",
        "finance",
        "financial",
        "inflation",
        "tax",
        "taxes",
        "employment",
        "work",
        "business",
        "retirement",
        "market",
        "markets",
    ],

    "Technology, AI & Privacy": [
        "technology",
        "tech",
        "ai",
        "artificial intelligence",
        "privacy",
        "google",
        "microsoft",
        "windows",
        "chrome",
        "software",
        "computer",
        "internet",
        "social media",
        "automation",
        "data",
    ],

    "Health, Medicine & Science": [
        "health",
        "medicine",
        "medical",
        "science",
        "covid",
        "pharmaceutical",
        "pharma",
        "nutrition",
        "doctor",
        "disease",
        "fauci",
        "vaccine",
    ],

    "Spirituality & Consciousness": [
        "spirituality",
        "spiritual",
        "consciousness",
        "soul",
        "ocean of love",
        "ocean of love and positivity",
        "university of life",
        "inner growth",
        "hope",
        "willpower",
        "meditation",
        "being",
    ],

    "Society, Psychology & Life": [
        "society",
        "psychology",
        "life",
        "family",
        "relationships",
        "relationship",
        "education",
        "behavior",
        "fear",
        "children",
        "marriage",
    ],

    "History, Culture & Religion": [
        "history",
        "culture",
        "religion",
        "religious",
        "civilization",
        "symbol",
        "symbols",
        "architecture",
        "identity",
        "jewish",
        "christianity",
        "islam",
    ],

    "Investigations & Special Reports": [
        "investigation",
        "investigations",
        "special report",
        "special reports",
    ],
}


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
        self.tags = []

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

        # Common metadata fields that may contain article keywords/tags.
        if prop in ("keywords", "news_keywords") and content:
            for item in content.split(","):
                item = item.strip()

                if item and item not in self.tags:
                    self.tags.append(item)


def get_article_metadata(url):
    try:
        page = fetch(url).decode("utf-8", errors="ignore")

        parser = MetaParser()
        parser.feed(page)

        return {
            "image": parser.image,
            "title": parser.title,
            "tags": parser.tags,
        }

    except Exception as error:
        print(f"Could not enrich {url}: {error}")

        return {
            "image": "",
            "title": "",
            "tags": [],
        }


def classify_report(title, tags):
    """
    Choose one primary Red Blood Journal category.

    Tags receive more weight than title words because tags were
    deliberately assigned to the publication.
    """

    scores = {
        category: 0
        for category in CATEGORY_RULES
    }

    title_text = title.lower()

    tag_texts = [
        str(tag).lower()
        for tag in tags
    ]

    for category, keywords in CATEGORY_RULES.items():

        for keyword in keywords:
            keyword = keyword.lower()

            # A matching Substack tag is strong evidence.
            for tag in tag_texts:
                if keyword == tag or keyword in tag:
                    scores[category] += 5

            # A title match is supporting evidence.
            if keyword in title_text:
                scores[category] += 1

    highest_score = max(scores.values())

    if highest_score == 0:
        return "Unclassified"

    winners = [
        category
        for category, score in scores.items()
        if score == highest_score
    ]

    # If there is no clear primary subject, treat it as a
    # cross-category/special investigation.
    if len(winners) > 1:
        return "Investigations & Special Reports"

    return winners[0]


def load_existing_reports():
    if not OUTPUT.exists():
        return {}

    try:
        existing = json.loads(
            OUTPUT.read_text(encoding="utf-8")
        )

        return {
            report.get("url", ""): report
            for report in existing
            if report.get("url")
        }

    except Exception as error:
        print(f"Could not read existing reports.json: {error}")
        return {}


def main():
    existing_reports = load_existing_reports()

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

        previous = existing_reports.get(url, {})

        report = {
            "id": rid,
            "title": previous.get(
                "title",
                title_from_slug(slug, rid)
            ),
            "subtitle": previous.get("subtitle", ""),
            "url": url,
            "image": previous.get("image", ""),
            "category": previous.get(
                "category",
                "Unclassified"
            ),
            "tags": previous.get("tags", []),
            "page": previous.get("page", 0),
            "lastmod": (
                lm.text.strip()
                if lm is not None and lm.text
                else ""
            )
        }

        out.append(report)

    # Newest publications first.
    out.sort(
        key=lambda item: item.get("lastmod", ""),
        reverse=True
    )

    # Revisit the newest reports on every run.
    # This means later Substack tag changes to recent reports
    # can change their Red Blood Journal category.
    for report in out[:LATEST_TO_ENRICH]:

        print(f"Updating metadata: {report['url']}")

        meta = get_article_metadata(report["url"])

        if meta["image"]:
            report["image"] = meta["image"]

        if meta["title"]:
            report["title"] = meta["title"]

        if meta["tags"]:
            report["tags"] = meta["tags"]

        report["category"] = classify_report(
            report["title"],
            report["tags"]
        )

    OUTPUT.write_text(
        json.dumps(
            out,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    images_found = sum(
        1
        for report in out[:LATEST_TO_ENRICH]
        if report.get("image")
    )

    categorized = sum(
        1
        for report in out
        if report.get("category") != "Unclassified"
    )

    print(f"Wrote {len(out)} publications to {OUTPUT}")

    print(
        f"Found cover images for "
        f"{images_found} of the newest "
        f"{min(LATEST_TO_ENRICH, len(out))} publications"
    )

    print(
        f"{categorized} publications currently have "
        f"a Red Blood Journal category"
    )


if __name__ == "__main__":
    main()
