#!/usr/bin/env python3
import json, re, urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from pathlib import Path

SITEMAP_URL="https://redblood.win/sitemap.xml"
OUTPUT=Path("reports.json")

def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":"RedBloodJournalArchiveBot/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return r.read()

def extract_id(slug):
    first=slug.split("-",1)[0]
    if re.fullmatch(r"\d+",first): return first
    if re.fullmatch(r"[A-Za-z]+\d+",first): return first.upper()
    return ""

def title_from_slug(slug,rid):
    base=slug[len(rid)+1:] if rid and slug.lower().startswith(rid.lower()+"-") else slug
    return base.replace("-"," ").strip().title()

def main():
    root=ET.fromstring(fetch(SITEMAP_URL))
    ns={"sm":"http://www.sitemaps.org/schemas/sitemap/0.9"}
    out=[]; seen=set()
    for node in root.findall("sm:url",ns):
        loc=node.find("sm:loc",ns)
        lm=node.find("sm:lastmod",ns)
        if loc is None or not loc.text: continue
        url=loc.text.strip()
        if "/p/" not in url or url in seen: continue
        seen.add(url)
        slug=urlparse(url).path.split("/p/",1)[1]
        rid=extract_id(slug)
        out.append({
            "id":rid,
            "title":title_from_slug(slug,rid),
            "subtitle":"",
            "url":url,
            "category":"Unclassified",
            "tags":[],
            "page":0,
            "lastmod":lm.text.strip() if lm is not None and lm.text else ""
        })
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Wrote {len(out)} publications to {OUTPUT}")

if __name__=="__main__":
    main()
