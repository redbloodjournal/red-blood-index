RED BLOOD JOURNAL — STAGE 1 AUTOMATIC TRANSLATION

UPLOAD ONLY THESE TWO NEW FILES, preserving their folders:

scripts/update_translations.py
.github/workflows/daily-translations.yml

Your existing files remain in place:
most-clicked.json
translations.json
es/
fa/
zh-cn/

WHAT HAPPENS EACH DAY
1. Read most-clicked.json.
2. Skip report numbers already listed in translations.json.
3. Pick the highest-clicked untranslated numbered report.
4. Fetch the English report.
5. Translate it into Spanish, Persian, and Simplified Chinese.
6. Validate all three translations.
7. If all three pass, create:
   /es/REPORT-ID/index.html
   /fa/REPORT-ID/index.html
   /zh-cn/REPORT-ID/index.html
8. Rebuild each language landing page.
9. Update translations.json.
10. Commit everything automatically.

If any translation fails validation, NOTHING is published for that run.

ONE REQUIRED SECRET
GitHub repository
→ Settings
→ Secrets and variables
→ Actions
→ New repository secret

Name:
OPENAI_API_KEY

Value:
your OpenAI API key

Never put the API key directly in a repository file.

FIRST TEST
After uploading the two files and adding the secret:

GitHub
→ Actions
→ Daily multilingual report
→ Run workflow

With the current files, #1607 should be skipped because it is already translated.
If most-clicked.json has not changed, #1357 should be the next report selected.

SCHEDULE
23 10 * * *
GitHub cron is UTC. This is one run per day.

STAGE 1 LIMITATION
most-clicked.json is still static.
So Stage 1 moves downward through the existing ranking one report per day.
Stage 2 will later update the click ranking automatically from GA4.
