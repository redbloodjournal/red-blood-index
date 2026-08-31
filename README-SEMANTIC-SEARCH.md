# Red Blood Journal — Automatic Semantic Search Metadata

This package makes every future report easier to find by meaning.

## What changes

Your existing `update_reports.py` stays untouched.

Instead of running:

```bash
python3 update_reports.py
```

run:

```bash
python3 update_reports_with_search.py
```

The wrapper:

1. runs the existing `update_reports.py`;
2. opens `metadata.json`;
3. generates semantic `topics` and `searchTerms` for every report;
4. preserves any search terms you manually added;
5. creates metadata for any report present in `reports.json` but missing from `metadata.json`;
6. writes `metadata.json` atomically.

No API key is required.

## Recommended cron

If your current cron is:

```cron
17 9 * * * cd /PATH/TO/red-blood-index && python3 update_reports.py
```

change only the filename:

```cron
17 9 * * * cd /PATH/TO/red-blood-index && python3 update_reports_with_search.py
```

Keep your actual existing path and time.

## What gets generated

A Spirituality & Consciousness report may receive metadata like:

```json
{
  "topics": [
    "Purpose of Life",
    "University of Life",
    "Soul & Consciousness"
  ],
  "searchTerms": [
    "purpose of life",
    "meaning of life",
    "why are we here",
    "why am I here",
    "school for the soul",
    "spiritual beings having a human experience",
    "life lessons",
    "soul growth",
    "what happens after death"
  ]
}
```

The updated RedBloodJournal.com search can read these fields automatically.

## One-time backfill

To enrich the entire existing archive without running the normal updater:

### Windows

```cmd
set RBJ_SKIP_UPDATE=1
python update_reports_with_search.py
```

### Linux / cPanel

```bash
RBJ_SKIP_UPDATE=1 python3 update_reports_with_search.py
```

This backfills `topics` and `searchTerms` for all existing metadata entries.

## Files

Place `update_reports_with_search.py` in the same directory as:

- `update_reports.py`
- `reports.json`
- `metadata.json`

Then use the wrapper for all future scheduled updates.
