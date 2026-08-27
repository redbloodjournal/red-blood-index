#!/usr/bin/env python3

import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from email.utils import parsedate_to_datetime

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITEMAP_URL = "https://redblood.win/sitemap.xml"
FEED_URL = "https://redblood.win/feed"
ARCHIVE_API_URL = "https://redblood.win/api/v1/archive"
OUTPUT = Path("reports.json")

# The sitemap is still useful for the full historical archive, but Substack can
# delay updating it. Pull the newest posts directly from Substack's archive API
# as a second discovery source so new reports are not blocked by sitemap lag.
ARCHIVE_PAGE_SIZE = 12
ARCHIVE_MAX_POSTS = 72

# We deliberately keep individual article/API requests low because Substack
# rate-limits bursts with HTTP 429. RSS/API data is used first; only a few
# posts are enriched by direct article requests per hourly run.
LATEST_TO_ENRICH = 12
MAX_METADATA_FETCHES = 4

# Gradually import tags/categories for older reports without hammering Substack.
BACKFILL_BATCH = 2
REQUEST_PAUSE_SECONDS = 1.25


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


def fetch(url, retries=2):
    """
    Fetch a public Red Blood Journal/Substack resource.

    A small 429 retry protects hourly GitHub runs from transient rate limits,
    while the rest of the script keeps the total number of direct requests low.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RedBloodJournalArchiveBot/3.0",
            "Accept": "application/rss+xml,application/xml,application/json,text/html,*/*",
        },
    )

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()

        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt >= retries:
                raise

            retry_after = error.headers.get("Retry-After", "")
            try:
                delay = max(float(retry_after), 2.0)
            except Exception:
                delay = 2.0 * (attempt + 1)

            print(
                f"HTTP 429 for {url}; "
                f"waiting {delay:.1f}s before retry"
            )
            time.sleep(delay)


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


def rss_text(node, tag):
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def rss_date_to_iso(value):
    value = str(value or "").strip()
    if not value:
        return ""

    try:
        dt = parsedate_to_datetime(value)
        return dt.isoformat()
    except Exception:
        return value


def first_image_from_html(value):
    value = str(value or "")
    match = re.search(
        r'<img[^>]+src=["\\\']([^"\\\']+)["\\\']',
        value,
        flags=re.I,
    )
    return match.group(1).strip() if match else ""


def get_rss_posts():
    """
    Read the publication RSS feed.

    RSS is the primary freshness source because it normally reflects newly
    published posts before the large sitemap catches up.
    """
    try:
        root = ET.fromstring(fetch(FEED_URL))
    except Exception as error:
        print(f"RSS feed unavailable: {error}")
        return []

    channel = root.find("channel")
    if channel is None:
        print("RSS feed did not contain a channel")
        return []

    posts = []

    media_ns = "{http://search.yahoo.com/mrss/}"
    content_ns = "{http://purl.org/rss/1.0/modules/content/}"

    for item in channel.findall("item"):
        url = normalize_url(rss_text(item, "link"))
        if not url or "/p/" not in url:
            continue

        title = rss_text(item, "title")
        subtitle = rss_text(item, "description")
        published = rss_date_to_iso(
            rss_text(item, "pubDate")
        )

        image = ""

        enclosure = item.find("enclosure")
        if enclosure is not None:
            enclosure_type = str(
                enclosure.attrib.get("type", "")
            ).lower()
            if enclosure_type.startswith("image/"):
                image = str(
                    enclosure.attrib.get("url", "")
                ).strip()

        if not image:
            media = item.find(f"{media_ns}content")
            if media is not None:
                image = str(
                    media.attrib.get("url", "")
                ).strip()

        if not image:
            thumb = item.find(f"{media_ns}thumbnail")
            if thumb is not None:
                image = str(
                    thumb.attrib.get("url", "")
                ).strip()

        if not image:
            encoded = item.find(f"{content_ns}encoded")
            if encoded is not None and encoded.text:
                image = first_image_from_html(
                    encoded.text
                )

        if not image:
            image = first_image_from_html(
                subtitle
            )

        posts.append(
            {
                "url": url,
                "title": title,
                "subtitle": re.sub(
                    r"<[^>]+>",
                    " ",
                    subtitle,
                ).strip(),
                "image": image,
                "lastmod": published,
            }
        )

    print(
        f"RSS feed discovered {len(posts)} "
        "recent publication records"
    )

    return posts


def build_report_from_rss(post, previous=None):
    previous = previous or {}

    url = normalize_url(
        post.get("url", "")
    )

    slug = (
        urlparse(url)
        .path
        .split("/p/", 1)[1]
    )

    rid = extract_id(slug)

    title = (
        post.get("title")
        or previous.get("title")
        or title_from_slug(slug, rid)
    )

    tags = previous.get("tags", [])

    return {
        "id": rid,
        "title": title,
        "subtitle": (
            post.get("subtitle")
            or previous.get("subtitle", "")
        ),
        "url": url,
        "image": (
            post.get("image")
            or previous.get("image", "")
        ),
        "category": classify_report(
            title,
            tags,
        ),
        "tags": tags,
        "page": previous.get("page", 0),
        "lastmod": (
            post.get("lastmod")
            or previous.get("lastmod", "")
        ),
        "_previous_lastmod": previous.get(
            "_previous_lastmod",
            previous.get("lastmod", ""),
        ),
    }


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

    # RSS is merged LAST so the newest publication data wins over a stale
    # sitemap/archive record for the same URL.
    rss_posts = get_rss_posts()

    rss_added = 0
    rss_updated = 0

    for post in rss_posts:
        url = normalize_url(
            post.get("url", "")
        )

        if not url or "/p/" not in url:
            continue

        previous = (
            discovered.get(url)
            or existing_reports.get(url)
            or {}
        )

        was_known = url in discovered

        discovered[url] = build_report_from_rss(
            post,
            previous=previous,
        )

        if was_known:
            rss_updated += 1
        else:
            rss_added += 1

    print(
        "RSS feed merged "
        f"{rss_updated} known reports and added "
        f"{rss_added} reports not present in other sources"
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

    refresh_urls = []
    refresh_seen = set()

    def queue_refresh(report):
        url = report.get("url", "")
        if (
            url
            and url not in refresh_seen
            and len(refresh_urls) < MAX_METADATA_FETCHES
        ):
            refresh_seen.add(url)
            refresh_urls.append(url)

    # Highest priority: newest posts that still lack a cover or usable title.
    for report in out[:LATEST_TO_ENRICH]:
        if (
            not report.get("image")
            or not report.get("title")
        ):
            queue_refresh(report)

    # Next: genuinely new/changed newest posts. RSS/API normally already gives
    # us enough data to display these, so only use remaining request budget.
    for report in out[:LATEST_TO_ENRICH]:
        if (
            report.get("lastmod", "")
            != report.get("_previous_lastmod", "")
        ):
            queue_refresh(report)

    # Finally: tiny background backfill for older records.
    backfill_count = 0
    for report in out[LATEST_TO_ENRICH:]:
        if (
            backfill_count >= BACKFILL_BATCH
            or len(refresh_urls) >= MAX_METADATA_FETCHES
        ):
            break

        if (
            not report.get("image")
            or not report.get("tags")
        ):
            queue_refresh(report)
            backfill_count += 1

    refresh_urls = set(refresh_urls)

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

        time.sleep(
            REQUEST_PAUSE_SECONDS
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

    if out:
        newest = out[0]
        print(
            "Newest merged report: "
            f"#{newest.get('id', '')} "
            f"{newest.get('title', '')} "
            f"({newest.get('lastmod', '')})"
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
