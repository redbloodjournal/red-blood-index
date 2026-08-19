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

# Always recheck this many newest reports every hourly run.
LATEST_TO_ENRICH = 25

# Gradually import tags/categories for older reports.
BACKFILL_BATCH = 50


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
            "User-Agent": "RedBloodJournalArchiveBot/2.0",
            "Accept": "application/json,text/html,*/*",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def clean_tag(value):
    value = str(value or "").strip()
    return re.sub(r"\s+", " ", value)


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
        if rid
        and slug.lower().startswith(
            rid.lower() + "-"
        )
        else slug
    )

    return (
        base
        .replace("-", " ")
        .strip()
        .title()
    )


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

        content = (
            data.get("content", "")
            .strip()
        )

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

        # Keep HTML tag methods as fallback.
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


def extract_tags_from_api(data):
    """
    Search Substack JSON for likely tag containers.

    We deliberately search several possible key names so
    small Substack API changes do not immediately break us.
    """

    found = []

    interesting_keys = {
        "tags",
        "tag",
        "posttags",
        "post_tags",
        "posttag",
        "post_tags_data",
        "publicationtags",
        "publication_tags",
    }

    def add_tag_value(value):

        if isinstance(value, str):
            add_unique(
                found,
                value
            )

        elif isinstance(value, list):

            for item in value:
                add_tag_value(item)

        elif isinstance(value, dict):

            # Typical object structures:
            # {"name": "Politics"}
            # {"title": "Politics"}
            # {"tag": "Politics"}
            for key in (
                "name",
                "title",
                "tag",
                "display_name",
                "displayName",
            ):
                candidate = value.get(key)

                if isinstance(
                    candidate,
                    str
                ):
                    add_unique(
                        found,
                        candidate
                    )

            # Sometimes tags are nested further.
            for child in value.values():

                if isinstance(
                    child,
                    (dict, list)
                ):
                    add_tag_value(child)

    def walk(obj):

        if isinstance(obj, dict):

            for key, value in obj.items():

                normalized = (
                    str(key)
                    .replace("-", "")
                    .replace("_", "")
                    .lower()
                )

                normalized_targets = {
                    item
                    .replace("-", "")
                    .replace("_", "")
                    .lower()
                    for item
                    in interesting_keys
                }

                if normalized in normalized_targets:
                    add_tag_value(value)

                walk(value)

        elif isinstance(obj, list):

            for item in obj:
                walk(item)

    walk(data)

    return found


def get_api_metadata(article_url):
    """
    Try Substack's publication post endpoint.

    Example:
    https://redblood.win/api/v1/posts/article-slug
    """

    parsed = urlparse(article_url)

    slug = (
        parsed.path
        .split("/p/", 1)[1]
        .strip("/")
    )

    api_url = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"/api/v1/posts/{slug}"
    )

    try:
        raw = fetch(api_url)

        data = json.loads(
            raw.decode(
                "utf-8",
                errors="ignore"
            )
        )

        tags = extract_tags_from_api(
            data
        )

        return {
            "tags": tags,
            "api_url": api_url,
        }

    except Exception as error:

        print(
            f"API metadata unavailable "
            f"for {article_url}: {error}"
        )

        return {
            "tags": [],
            "api_url": api_url,
        }


def get_article_metadata(url):
    """
    Retrieve title + image from HTML,
    and tags from Substack JSON API.
    """

    result = {
        "image": "",
        "title": "",
        "tags": [],
    }

    # HTML keeps the cover-image system
    # that is already working.
    try:
        page = fetch(url).decode(
            "utf-8",
            errors="ignore"
        )

        parser = MetaParser()
        parser.feed(page)

        result["image"] = parser.image
        result["title"] = parser.title

        for tag in parser.tags:
            add_unique(
                result["tags"],
                tag
            )

    except Exception as error:

        print(
            f"Could not read HTML "
            f"{url}: {error}"
        )

    # Try Substack post JSON for real tags.
    api_meta = get_api_metadata(url)

    for tag in api_meta["tags"]:
        add_unique(
            result["tags"],
            tag
        )

    if result["tags"]:

        print(
            f"Tags found for {url}: "
            f"{result['tags']}"
        )

    else:

        print(
            f"No tags found for {url}"
        )

    return result


def classify_report(title, tags):
    scores = {
        category: 0
        for category
        in CATEGORY_RULES
    }

    title_text = str(
        title or ""
    ).lower()

    tag_texts = [
        str(tag).lower()
        for tag in tags
    ]

    for category, keywords in (
        CATEGORY_RULES.items()
    ):

        for keyword in keywords:

            keyword = (
                keyword.lower()
            )

            # Tags are deliberately given
            # much more weight than title words.
            for tag in tag_texts:

                if (
                    keyword == tag
                    or keyword in tag
                    or tag in keyword
                ):
                    scores[
                        category
                    ] += 5

            if keyword in title_text:
                scores[
                    category
                ] += 1

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
            report.get(
                "url",
                ""
            ): report

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

        url = (
            loc.text.strip()
        )

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

        rid = extract_id(
            slug
        )

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

            "title":
            previous.get(
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

            "url":
            url,

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

        out.append(
            report
        )

    # Newest reports first.
    out.sort(
        key=lambda item:
        item.get(
            "lastmod",
            ""
        ),
        reverse=True
    )

    refresh_urls = set()

    # Always refresh newest reports.
    for report in out[:LATEST_TO_ENRICH]:
        refresh_urls.add(
            report["url"]
        )

    # Gradually backfill older reports missing tags or cover images.
    backfill_count = 0

    for report in out[LATEST_TO_ENRICH:]:
        if backfill_count >= BACKFILL_BATCH:
            break

        if not report.get("tags") or not report.get("image"):
            refresh_urls.add(
                report["url"]
            )
            backfill_count += 1

    # Also refresh any article whose
    # sitemap modification date changed.
    for report in out:
        if (
            report.get(
                "lastmod",
                ""
            )
            !=
            report.get(
                "_previous_lastmod",
                ""
            )
        ):
            refresh_urls.add(
                report["url"]
            )

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

        # Important:
        # If API succeeds and finds current tags,
        # replace the old list.
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

    # Remove internal helper field.
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
        for report
        in out[
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
        f"{categorized} "
        "publications currently have "
        "a Red Blood Journal category"
    )


if __name__ == "__main__":
    main()
