#!/usr/bin/env python3

import json
import re
import urllib.request
import xml.etree.ElementTree as ET

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITEMAP_URL = "https://redblood.win/sitemap.xml"
ARCHIVE_API_URL = "https://redblood.win/api/v1/archive"
OUTPUT = Path("reports.json")

# The sitemap is still useful for the full historical archive, but Substack can
# delay updating it. Pull the newest posts directly from Substack's archive API
# as a second discovery source so new reports are not blocked by sitemap lag.
ARCHIVE_PAGE_SIZE = 12
ARCHIVE_MAX_POSTS = 72

# Always recheck this many newest reports every hourly run.
LATEST_TO_ENRICH = 16

# Gradually import tags/categories for older reports.
BACKFILL_BATCH = 10


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


def normalize_url(value):
    return str(value or "").strip().rstrip("/")


def first_nonempty(mapping, keys, default=""):
    if not isinstance(mapping, dict):
        return default

    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value

    return default


def archive_post_url(post):
    """
    Return a canonical /p/ URL from a Substack archive API object.
    The endpoint's exact field names have changed over time, so accept
    several common variants and fall back to the slug.
    """
    candidate = first_nonempty(
        post,
        (
            "canonical_url",
            "canonicalUrl",
            "url",
            "post_url",
            "postUrl",
        ),
        "",
    )

    if isinstance(candidate, str) and "/p/" in candidate:
        return normalize_url(candidate)

    slug = first_nonempty(
        post,
        (
            "slug",
            "post_slug",
            "postSlug",
        ),
        "",
    )

    if isinstance(slug, str) and slug.strip():
        return f"https://redblood.win/p/{slug.strip().strip('/')}"

    return ""


def archive_post_lastmod(post):
    value = first_nonempty(
        post,
        (
            "post_date",
            "postDate",
            "published_at",
            "publishedAt",
            "publication_date",
            "publicationDate",
            "date",
        ),
        "",
    )

    return str(value or "").strip()


def archive_post_title(post):
    value = first_nonempty(
        post,
        (
            "title",
            "headline",
            "name",
        ),
        "",
    )
    return str(value or "").strip()


def archive_post_subtitle(post):
    value = first_nonempty(
        post,
        (
            "subtitle",
            "description",
            "social_title",
            "socialTitle",
            "search_engine_description",
            "searchEngineDescription",
        ),
        "",
    )
    return str(value or "").strip()


def archive_post_image(post):
    value = first_nonempty(
        post,
        (
            "cover_image",
            "coverImage",
            "image",
            "social_image",
            "socialImage",
        ),
        "",
    )
    return str(value or "").strip()


def get_archive_api_posts():
    """
    Fetch the newest posts from Substack's public archive endpoint.

    This is intentionally a *supplement* to the sitemap, not a replacement.
    If the API is unavailable, the updater continues with the sitemap.
    """
    posts = []
    offset = 0

    while len(posts) < ARCHIVE_MAX_POSTS:
        limit = min(
            ARCHIVE_PAGE_SIZE,
            ARCHIVE_MAX_POSTS - len(posts),
        )

        api_url = (
            f"{ARCHIVE_API_URL}"
            f"?sort=new&search=&offset={offset}&limit={limit}"
        )

        try:
            raw = fetch(api_url)
            data = json.loads(
                raw.decode(
                    "utf-8",
                    errors="ignore",
                )
            )
        except Exception as error:
            print(
                "Archive API unavailable at "
                f"offset {offset}: {error}"
            )
            break

        # Some versions return a bare list; tolerate a wrapped response too.
        if isinstance(data, dict):
            batch = (
                data.get("posts")
                or data.get("items")
                or data.get("results")
                or []
            )
        else:
            batch = data

        if not isinstance(batch, list) or not batch:
            break

        valid = [
            item
            for item in batch
            if isinstance(item, dict)
        ]

        posts.extend(valid)
        offset += len(batch)

        if len(batch) < limit:
            break

    print(
        f"Archive API discovered {len(posts)} "
        "recent publication records"
    )

    return posts


def build_report_from_url(
    url,
    lastmod,
    previous=None,
    api_post=None,
):
    previous = previous or {}
    api_post = api_post or {}

    normalized = normalize_url(url)

    slug = (
        urlparse(normalized)
        .path
        .split("/p/", 1)[1]
    )

    rid = extract_id(slug)

    api_title = archive_post_title(api_post)
    api_subtitle = archive_post_subtitle(api_post)
    api_image = archive_post_image(api_post)
    api_tags = extract_tags_from_api(api_post)

    title = (
        api_title
        or previous.get("title")
        or title_from_slug(slug, rid)
    )

    tags = (
        api_tags
        or previous.get("tags", [])
    )

    category = previous.get(
        "category",
        "Unclassified",
    )

    # New API-only reports should get a category immediately when possible.
    if api_title or api_tags:
        category = classify_report(
            title,
            tags,
        )

    return {
        "id": rid,
        "title": title,
        "subtitle": (
            api_subtitle
            or previous.get("subtitle", "")
        ),
        "url": normalized,
        "image": (
            api_image
            or previous.get("image", "")
        ),
        "category": category,
        "tags": tags,
        "page": previous.get("page", 0),
        "lastmod": (
            str(lastmod or "").strip()
            or previous.get("lastmod", "")
        ),
        "_previous_lastmod": previous.get(
            "_previous_lastmod",
            previous.get(
                "lastmod",
                "",
            ),
        ),
    }


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
            normalize_url(
                report.get(
                    "url",
                    ""
                )
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

    # Build a merged discovery map:
    #   1. existing reports (safety baseline)
    #   2. sitemap (full archive)
    #   3. Substack archive API (newest reports, even if sitemap is late)
    discovered = {}

    for existing_url, previous in existing_reports.items():
        if "/p/" not in existing_url:
            continue

        discovered[existing_url] = build_report_from_url(
            existing_url,
            previous.get("lastmod", ""),
            previous=previous,
        )

    # Full historical source.
    try:
        root = ET.fromstring(
            fetch(SITEMAP_URL)
        )

        ns = {
            "sm":
            "http://www.sitemaps.org/"
            "schemas/sitemap/0.9"
        }

        sitemap_count = 0

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

            url = normalize_url(
                loc.text
            )

            if "/p/" not in url:
                continue

            sitemap_lastmod = (
                lm.text.strip()

                if (
                    lm is not None
                    and lm.text
                )

                else ""
            )

            previous = existing_reports.get(
                url,
                {},
            )

            discovered[url] = build_report_from_url(
                url,
                sitemap_lastmod,
                previous=previous,
            )

            sitemap_count += 1

        print(
            f"Sitemap discovered {sitemap_count} "
            "publication records"
        )

    except Exception as error:
        print(
            "WARNING: Could not read sitemap; "
            "preserving existing reports and "
            f"continuing with archive API: {error}"
        )

    # Freshness source. This is what protects the site from sitemap lag.
    api_posts = get_archive_api_posts()

    api_added = 0
    api_updated = 0

    for post in api_posts:
        url = archive_post_url(
            post
        )

        if not url or "/p/" not in url:
            continue

        url = normalize_url(url)

        previous = (
            discovered.get(url)
            or existing_reports.get(url)
            or {}
        )

        was_known = url in discovered

        api_lastmod = archive_post_lastmod(
            post
        )

        discovered[url] = build_report_from_url(
            url,
            api_lastmod,
            previous=previous,
            api_post=post,
        )

        if was_known:
            api_updated += 1
        else:
            api_added += 1

    print(
        "Archive API merged "
        f"{api_updated} known reports and added "
        f"{api_added} reports not present in the sitemap"
    )

    out = list(
        discovered.values()
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
