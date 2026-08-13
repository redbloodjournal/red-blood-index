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

# Always refresh this many newest reports.
LATEST_TO_ENRICH = 25


CATEGORY_RULES = {
    "Politics & Geopolitics": [
        "politics",
        "political",
        "geopolitics",
        "geopolitical",
        "iran",
        "israel",
        "trump",
        "government",
        "war",
        "military",
        "diplomacy",
        "foreign policy",
        "sovereignty",
        "middle east",
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
        "information warfare",
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
        "labor",
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
        "digital privacy",
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
        "vaccines",
        "public health",
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
        "wisdom",
        "inner journey",
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
        "culture of fear",
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
        "judaism",
        "christianity",
        "islam",
        "historical",
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
        },
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def clean_tag(value):
    value = str(value or "").strip()

    value = re.sub(r"\s+", " ", value)

    return value


def add_unique(items, value):
    value = clean_tag(value)

    if not value:
        return

    existing = {
        item.lower()
        for item in items
    }

    if value.lower() not in existing:
        items.append(value)


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

        self.in_json_ld = False
        self.json_ld_chunks = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        data = dict(attrs)

        if tag == "meta":
            prop = (
                data.get("property", "")
                or data.get("name", "")
            ).lower()

            content = data.get("content", "").strip()

            if (
                prop == "og:image"
                and content
                and not self.image
            ):
                self.image = content

            if (
                prop == "og:title"
                and content
                and not self.title
            ):
                self.title = content

            # Common article-tag metadata.
            if prop in (
                "article:tag",
                "keywords",
                "news_keywords",
            ) and content:

                if prop == "article:tag":
                    add_unique(
                        self.tags,
                        content
                    )

                else:
                    for item in content.split(","):
                        add_unique(
                            self.tags,
                            item
                        )

        # Some sites expose tags as clickable topic links.
        if tag == "a":
            href = data.get("href", "")

            if (
                "/tag/" in href
                or "/tags/" in href
                or "/topic/" in href
            ):
                slug = href.rstrip("/").split("/")[-1]

                slug = slug.replace("-", " ").strip()

                if slug:
                    add_unique(
                        self.tags,
                        slug
                    )

        # Capture JSON-LD.
        if tag == "script":
            script_type = data.get(
                "type",
                ""
            ).lower()

            if script_type == "application/ld+json":
                self.in_json_ld = True
                self.json_ld_chunks = []

    def handle_endtag(self, tag):
        if (
            tag.lower() == "script"
            and self.in_json_ld
        ):
            self.in_json_ld = False

            raw = "".join(
                self.json_ld_chunks
            ).strip()

            if raw:
                self.extract_json_ld_tags(raw)

            self.json_ld_chunks = []

    def handle_data(self, data):
        if self.in_json_ld:
            self.json_ld_chunks.append(data)

    def extract_json_ld_tags(self, raw):
        try:
            parsed = json.loads(raw)
        except Exception:
            return

        def walk(obj):
            if isinstance(obj, dict):

                for key, value in obj.items():

                    if key.lower() in (
                        "keywords",
                        "articleSection".lower(),
                    ):
                        self.add_json_value(value)

                    walk(value)

            elif isinstance(obj, list):

                for item in obj:
                    walk(item)

        walk(parsed)

    def add_json_value(self, value):
        if isinstance(value, str):

            for item in value.split(","):
                add_unique(
                    self.tags,
                    item
                )

        elif isinstance(value, list):

            for item in value:
                if isinstance(item, str):
                    add_unique(
                        self.tags,
                        item
                    )


def get_article_metadata(url):
    try:
        page = fetch(url).decode(
            "utf-8",
            errors="ignore"
        )

        parser = MetaParser()
        parser.feed(page)

        return {
            "image": parser.image,
            "title": parser.title,
            "tags": parser.tags,
        }

    except Exception as error:
        print(
            f"Could not enrich {url}: "
            f"{error}"
        )

        return {
            "image": "",
            "title": "",
            "tags": [],
        }


def classify_report(title, tags):
    scores = {
        category: 0
        for category in CATEGORY_RULES
    }

    title_text = str(
        title or ""
    ).lower()

    tag_texts = [
        str(tag).lower()
        for tag in tags
    ]

    for category, keywords in CATEGORY_RULES.items():

        for keyword in keywords:
            keyword = keyword.lower()

            # Tags are strongest evidence.
            for tag in tag_texts:

                if (
                    keyword == tag
                    or keyword in tag
                    or tag in keyword
                ):
                    scores[category] += 5

            # Title is secondary evidence.
            if keyword in title_text:
                scores[category] += 1

    highest_score = max(
        scores.values()
    )

    if highest_score == 0:
        return "Unclassified"

    winners = [
        category
        for category, score
        in scores.items()
        if score == highest_score
    ]

    if len(winners) > 1:
        return (
            "Investigations & "
            "Special Reports"
        )

    return winners[0]


def load_existing_reports():
    if not OUTPUT.exists():
        return {}

    try:
        existing = json.loads(
            OUTPUT.read_text(
                encoding="utf-8"
            )
        )

        return {
            report.get("url", ""): report
            for report in existing
            if report.get("url")
        }

    except Exception as error:
        print(
            "Could not read existing "
            f"reports.json: {error}"
        )

        return {}


def main():
    existing_reports = (
        load_existing_reports()
    )

    root = ET.fromstring(
        fetch(SITEMAP_URL)
    )

    ns = {
        "sm":
        "http://www.sitemaps.org/"
        "schemas/sitemap/0.9"
    }

    out = []
    seen = set()

    for node in root.findall(
        "sm:url",
        ns
    ):

        loc = node.find(
            "sm:loc",
            ns
        )

        lm = node.find(
            "sm:lastmod",
            ns
        )

        if (
            loc is None
            or not loc.text
        ):
            continue

        url = loc.text.strip()

        if (
            "/p/" not in url
            or url in seen
        ):
            continue

        seen.add(url)

        slug = (
            urlparse(url)
            .path
            .split("/p/", 1)[1]
        )

        rid = extract_id(slug)

        sitemap_lastmod = (
            lm.text.strip()
            if (
                lm is not None
                and lm.text
            )
            else ""
        )

        previous = (
            existing_reports.get(
                url,
                {}
            )
        )

        report = {
            "id": rid,

            "title": previous.get(
                "title",
                title_from_slug(
                    slug,
                    rid
                )
            ),

            "subtitle":
            previous.get(
                "subtitle",
                ""
            ),

            "url": url,

            "image":
            previous.get(
                "image",
                ""
            ),

            "category":
            previous.get(
                "category",
                "Unclassified"
            ),

            "tags":
            previous.get(
                "tags",
                []
            ),

            "page":
            previous.get(
                "page",
                0
            ),

            "lastmod":
            sitemap_lastmod,

            "_previous_lastmod":
            previous.get(
                "lastmod",
                ""
            ),
        }

        out.append(report)

    # Newest first.
    out.sort(
        key=lambda item:
        item.get(
            "lastmod",
            ""
        ),
        reverse=True
    )

    # Determine which reports should be refreshed.
    #
    # Always refresh newest 25.
    # Also refresh any report whose sitemap
    # lastmod changed since previous run.
    refresh_urls = set()

    for report in out[
        :LATEST_TO_ENRICH
    ]:
        refresh_urls.add(
            report["url"]
        )

    for report in out:

        if (
            report.get("lastmod", "")
            !=
            report.get(
                "_previous_lastmod",
                ""
            )
        ):
            refresh_urls.add(
                report["url"]
            )

    # Fetch metadata for selected reports.
    for report in out:

        if (
            report["url"]
            not in refresh_urls
        ):
            continue

        print(
            "Updating metadata: "
            f"{report['url']}"
        )

        meta = (
            get_article_metadata(
                report["url"]
            )
        )

        if meta["image"]:
            report["image"] = (
                meta["image"]
            )

        if meta["title"]:
            report["title"] = (
                meta["title"]
            )

        # Replace stored tags whenever
        # Substack exposes current tags.
        if meta["tags"]:
            report["tags"] = (
                meta["tags"]
            )

        report["category"] = (
            classify_report(
                report["title"],
                report["tags"]
            )
        )

    # Remove internal helper key.
    for report in out:
        report.pop(
            "_previous_lastmod",
            None
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
        for report in out[
            :LATEST_TO_ENRICH
        ]
        if report.get("image")
    )

    reports_with_tags = sum(
        1
        for report in out
        if report.get("tags")
    )

    categorized = sum(
        1
        for report in out
        if (
            report.get(
                "category"
            )
            != "Unclassified"
        )
    )

    print(
        f"Wrote {len(out)} "
        f"publications to {OUTPUT}"
    )

    print(
        "Found cover images for "
        f"{images_found} of the newest "
        f"{min(LATEST_TO_ENRICH, len(out))} "
        "publications"
    )

    print(
        f"{reports_with_tags} "
        "publications currently have "
        "imported tags"
    )

    print(
        f"{categorized} publications "
        "currently have a Red Blood "
        "Journal category"
    )


if __name__ == "__main__":
    main()
