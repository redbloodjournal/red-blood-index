#!/usr/bin/env python3
"""
Red Blood Journal semantic metadata enricher.

Purpose
-------
Run the existing update_reports.py first, then enrich metadata.json with:
  - topics: compact conceptual labels
  - searchTerms: human-language aliases/questions visitors may type

This is deterministic and requires no API key.

Usage
-----
    python update_reports_with_search.py

Optional environment variables
------------------------------
    RBJ_SKIP_UPDATE=1
        Skip update_reports.py and only enrich metadata.json.

    RBJ_METADATA=/path/to/metadata.json
        Override metadata.json location.

    RBJ_REPORTS=/path/to/reports.json
        Optional reports.json location. Used as a fallback source.

The script preserves existing hand-written topics/searchTerms and only adds
new generated terms.
"""

from __future__ import annotations

import json
import os
import re
import runpy
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

BASE = Path(__file__).resolve().parent
METADATA_PATH = Path(os.environ.get("RBJ_METADATA", BASE / "metadata.json"))
REPORTS_PATH = Path(os.environ.get("RBJ_REPORTS", BASE / "reports.json"))
UPDATER_PATH = BASE / "scripts" / "update_reports.py"

STOP_WORDS = {
    "a","an","and","are","as","at","be","been","being","but","by","can","could",
    "did","do","does","for","from","had","has","have","he","her","hers","him",
    "his","how","i","if","in","into","is","it","its","may","more","most","not",
    "of","on","or","our","ours","she","so","than","that","the","their","theirs",
    "them","they","this","those","to","too","us","was","we","were","what","when",
    "where","which","who","why","will","with","would","you","your","yours"
}

# Concept aliases are intentionally written in reader language, not just taxonomy language.
CONCEPTS = {
    "purpose of life": {
        "triggers": [
            "purpose of life","meaning of life","university of life","school of life",
            "soul","spirit","spiritual being","human experience","inner growth",
            "graduation","curriculum","life lesson","consciousness","after death",
            "driver","vehicle","acceptance","realization"
        ],
        "topics": ["Purpose of Life", "University of Life", "Soul & Consciousness"],
        "aliases": [
            "purpose of life","meaning of life","why are we here","why am I here",
            "what is the meaning of life","what is the purpose of existence",
            "why are humans here","why did spirit become human",
            "spiritual beings having a human experience","life is a school",
            "school for the soul","university of life","life lessons",
            "soul growth","spiritual growth","graduation of the soul",
            "what happens after death","who am I really","inner self"
        ]
    },
    "iran": {
        "triggers": ["iran","iranian","tehran","khamenei","irgc","persian","hormuz","pezeshkian","mojtaba"],
        "topics": ["Iran"],
        "aliases": [
            "iran","iranian government","islamic republic","iran politics","iran regime",
            "tehran","iran crisis","iran war","iran economy","iran society"
        ]
    },
    "surveillance": {
        "triggers": ["surveillance","privacy","camera","license plate","flock","tracking","panopticon","monitoring"],
        "topics": ["Surveillance & Privacy"],
        "aliases": [
            "surveillance","mass surveillance","government surveillance","privacy",
            "tracking citizens","license plate readers","digital monitoring",
            "who is watching us","privacy rights"
        ]
    },
    "ai": {
        "triggers": ["ai","artificial intelligence","algorithm","automation","chatgpt","machine learning"],
        "topics": ["Artificial Intelligence"],
        "aliases": [
            "artificial intelligence","ai","algorithms","automation","machine intelligence",
            "ai control","ai and society","ai privacy","future of ai"
        ]
    },
    "money": {
        "triggers": ["money","bank","banking","tax","taxes","inflation","economy","economic","market","income","retirement","cost"],
        "topics": ["Money & Economics"],
        "aliases": [
            "money","economics","economy","banking","inflation","taxes","cost of living",
            "financial power","income","markets","retirement","personal finance"
        ]
    },
    "war": {
        "triggers": ["war","military","strike","nuclear","pentagon","weapon","battlefield","invasion","missile"],
        "topics": ["War & Military Power"],
        "aliases": [
            "war","military","geopolitics","nuclear war","military strike","weapons",
            "foreign policy","conflict","battlefield","war powers"
        ]
    },
    "media": {
        "triggers": ["media","propaganda","narrative","censorship","journalism","press","information"],
        "topics": ["Media & Narratives"],
        "aliases": [
            "media","propaganda","censorship","narrative control","information warfare",
            "journalism","press freedom","who controls the narrative"
        ]
    },
    "government power": {
        "triggers": ["government","state","power","control","authority","regime","congress","president","court","cia","fbi","intelligence"],
        "topics": ["Government & Power"],
        "aliases": [
            "government power","state power","political control","institutional power",
            "intelligence agencies","government accountability","abuse of power",
            "who really controls government"
        ]
    },
    "freedom": {
        "triggers": ["freedom","liberty","sovereignty","rights","constitutional","constitution","speech","thought"],
        "topics": ["Freedom & Sovereignty"],
        "aliases": [
            "freedom","liberty","individual sovereignty","freedom of speech",
            "freedom of thought","constitutional rights","personal sovereignty",
            "civil liberties"
        ]
    },
    "health": {
        "triggers": ["health","medicine","medical","doctor","disease","covid","pharma","pharmaceutical","vaccine","treatment"],
        "topics": ["Health & Medicine"],
        "aliases": [
            "health","medicine","medical system","doctors","public health","pharmaceuticals",
            "covid","treatment","healing","health policy"
        ]
    },
    "religion": {
        "triggers": ["religion","religious","quran","jesus","god","faith","islam","christian","spiritual"],
        "topics": ["Religion & Spirituality"],
        "aliases": [
            "religion","faith","god","spirituality","quran","islam","christianity",
            "religious belief","spiritual belief","religion and power"
        ]
    },
    "psychology": {
        "triggers": ["fear","behavior","psychology","mind","belief","identity","ego","relationship","family"],
        "topics": ["Psychology & Human Behavior"],
        "aliases": [
            "psychology","human behavior","fear","belief","identity","mind","ego",
            "relationships","why people behave this way","human nature"
        ]
    },
}

CATEGORY_ALIASES = {
    "Politics & Geopolitics": [
        "politics","geopolitics","foreign policy","international relations",
        "government","war and diplomacy","global power"
    ],
    "Power, Intelligence & Media": [
        "power","intelligence","cia","surveillance","censorship","media",
        "propaganda","hidden systems","narrative control"
    ],
    "Money, Economics & Work": [
        "money","economics","work","jobs","banking","inflation","taxes",
        "financial system","cost of living"
    ],
    "Technology, AI & Privacy": [
        "technology","artificial intelligence","ai","privacy","surveillance",
        "software","automation","digital rights"
    ],
    "Health, Medicine & Science": [
        "health","medicine","science","public health","pharmaceuticals",
        "medical research","disease","treatment"
    ],
    "Spirituality & Consciousness": [
        "spirituality","consciousness","soul","spirit","purpose of life",
        "meaning of life","university of life","inner growth","life lessons",
        "why are we here","human experience"
    ],
    "Society, Psychology & Life": [
        "society","psychology","human behavior","life","family","fear",
        "culture","relationships","education"
    ],
    "History, Culture & Religion": [
        "history","culture","religion","civilization","faith","identity",
        "historical events","religious history"
    ],
    "Investigations & Special Reports": [
        "investigation","special report","deep dive","evidence","questions",
        "unusual cases","follow the evidence"
    ]
}

QUESTION_TEMPLATES = {
    "Politics & Geopolitics": ["what is happening in {x}", "why is {x} happening"],
    "Technology, AI & Privacy": ["how does {x} affect privacy", "what does {x} mean for technology"],
    "Health, Medicine & Science": ["what does {x} mean for health"],
    "Spirituality & Consciousness": ["what does {x} teach us", "what is the deeper meaning of {x}"],
    "Society, Psychology & Life": ["why do people {x}", "what does {x} reveal about human behavior"],
}

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(clean_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(clean_text(v) for v in value.values())
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def norm(text: str) -> str:
    text = clean_text(text).lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return text

def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        item = clean_text(item).strip(" ,;:-")
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

def phrase_candidates(title: str, subtitle: str) -> List[str]:
    """Generate useful lexical phrases from title/subtitle without producing junk."""
    source = f"{title} {subtitle}".strip()
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", source)
    useful = [t for t in tokens if t.lower() not in STOP_WORDS and len(t) > 2]
    phrases: List[str] = []

    # Important single terms
    phrases.extend(useful[:18])

    # Adjacent 2- and 3-word phrases from the original token stream.
    lower_tokens = [t for t in tokens]
    for n in (2, 3):
        for i in range(len(lower_tokens) - n + 1):
            gram = lower_tokens[i:i+n]
            if all(w.lower() in STOP_WORDS for w in gram):
                continue
            phrase = " ".join(gram)
            if len(phrase) >= 7:
                phrases.append(phrase)
    return unique_keep_order(phrases)[:28]

def report_text(record: Dict[str, Any], fallback: Dict[str, Any] | None = None) -> str:
    fallback = fallback or {}
    parts = [
        record.get("id"), record.get("title"), record.get("subtitle"),
        record.get("category"), record.get("tags"), record.get("description"),
        fallback.get("id"), fallback.get("title"), fallback.get("subtitle"),
        fallback.get("category"), fallback.get("tags"), fallback.get("description")
    ]
    return norm(" ".join(clean_text(p) for p in parts if p))

def generate_semantics(record: Dict[str, Any], fallback: Dict[str, Any] | None = None):
    fallback = fallback or {}
    title = clean_text(record.get("title") or fallback.get("title"))
    subtitle = clean_text(record.get("subtitle") or fallback.get("subtitle"))
    category = clean_text(record.get("category") or fallback.get("category"))
    tags = record.get("tags") or fallback.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]

    haystack = report_text(record, fallback)

    topics: List[str] = []
    search_terms: List[str] = []

    # Existing human-curated values are always preserved first.
    existing_topics = record.get("topics", [])
    existing_terms = record.get("searchTerms", [])
    if isinstance(existing_topics, str):
        existing_topics = [existing_topics]
    if isinstance(existing_terms, str):
        existing_terms = [existing_terms]

    topics.extend(existing_topics)
    search_terms.extend(existing_terms)

    # Exact metadata itself remains searchable.
    search_terms.extend([title, subtitle, category])
    search_terms.extend(clean_text(t) for t in tags)

    # Category-level semantic expansion.
    search_terms.extend(CATEGORY_ALIASES.get(category, []))

    # Concept-level expansion.
    for spec in CONCEPTS.values():
        if any(norm(trigger) in haystack for trigger in spec["triggers"]):
            topics.extend(spec["topics"])
            search_terms.extend(spec["aliases"])

    # Phrase harvesting helps names, events, organizations and unusual terms.
    search_terms.extend(phrase_candidates(title, subtitle))

    # Add a small number of natural-language question forms using a compact subject.
    subject_terms = [
        t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", f"{title} {subtitle}")
        if t.lower() not in STOP_WORDS and len(t) > 2
    ]
    subject = " ".join(subject_terms[:4]).strip()
    if subject and category in QUESTION_TEMPLATES:
        for template in QUESTION_TEMPLATES[category]:
            search_terms.append(template.format(x=subject.lower()))

    # Keep payload compact enough for hundreds/thousands of reports.
    topics = unique_keep_order(topics)[:12]
    search_terms = unique_keep_order(search_terms)[:80]

    return topics, search_terms

def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)

def atomic_write_json(path: Path, data: Any):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)

def run_existing_updater():
    if os.environ.get("RBJ_SKIP_UPDATE") == "1":
        print("RBJ_SKIP_UPDATE=1: skipping update_reports.py")
        return
    if not UPDATER_PATH.exists():
        print(f"WARNING: {UPDATER_PATH.name} not found; enriching metadata only.", file=sys.stderr)
        return
    print(f"Running existing updater: {UPDATER_PATH.name}")
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(UPDATER_PATH)]
        runpy.run_path(str(UPDATER_PATH), run_name="__main__")
    finally:
        sys.argv = old_argv

def main():
    run_existing_updater()

    metadata = load_json(METADATA_PATH, {})
    reports = load_json(REPORTS_PATH, [])

    if not isinstance(metadata, dict):
        raise SystemExit(f"{METADATA_PATH} must contain a JSON object keyed by report URL.")
    if not isinstance(reports, list):
        reports = []

    # URLs are identifiers, not prose. Do not pass them through clean_text(),
    # because clean_text intentionally removes URLs from searchable prose.
    reports_by_url = {
        str(r.get("url")).strip(): r
        for r in reports
        if isinstance(r, dict) and str(r.get("url") or "").strip()
    }

    changed = 0
    enriched = 0

    for url, record in list(metadata.items()):
        if not isinstance(record, dict):
            continue

        fallback = reports_by_url.get(clean_text(url), {})
        before_topics = record.get("topics", [])
        before_terms = record.get("searchTerms", [])

        topics, search_terms = generate_semantics(record, fallback)
        record["topics"] = topics
        record["searchTerms"] = search_terms
        enriched += 1

        if before_topics != topics or before_terms != search_terms:
            changed += 1

    # If reports.json contains a report absent from metadata.json, create a minimal
    # metadata entry so it is semantically searchable immediately.
    created = 0
    for url, report in reports_by_url.items():
        if url in metadata:
            continue
        record = {
            key: report[key]
            for key in ("id","title","subtitle","category","tags","page")
            if key in report
        }
        topics, search_terms = generate_semantics(record, report)
        record["topics"] = topics
        record["searchTerms"] = search_terms
        metadata[url] = record
        created += 1
        changed += 1

    atomic_write_json(METADATA_PATH, metadata)

    print(f"Semantic metadata complete.")
    print(f"  records enriched: {enriched}")
    print(f"  new metadata records: {created}")
    print(f"  records changed: {changed}")
    print(f"  wrote: {METADATA_PATH}")

if __name__ == "__main__":
    main()
