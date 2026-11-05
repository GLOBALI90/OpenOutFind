# Diesel Buyer Research Pilot

This is an isolated, non-sending pilot inside the existing OpenOutFind repository. It does not change the OpenOutFind CLI, the existing collection workflow, the old `daily-automation` repository, or any sending workflow.

## What this pilot does

- Starts from public company or industry-directory URLs supplied to the manual GitHub Actions run.
- Reads only public HTML pages on the same company host and checks `robots.txt` before each page.
- Looks for explicit diesel/gasoil wording and separately records likely fuel-consuming sectors.
- Extracts public business emails and phone numbers.
- Marks WhatsApp only when the page contains an explicit `wa.me` or WhatsApp click-to-chat link. A phone number alone is never labelled WhatsApp.
- Keeps source URLs, evidence terms, a conservative qualification score, and a stable company key.
- Upserts into the separate Google Sheets tab `Diesel Buyers`, keyed by `lead_key`, so repeated runs update rather than duplicate a company.
- Never sends email or WhatsApp messages.

## Important qualification rule

A company with explicit diesel-related wording is labelled `direct_diesel_evidence`. A company in mining, transport, shipping, construction, power generation, manufacturing, or agriculture without explicit diesel evidence is labelled `sector_candidate_needs_confirmation`. The latter is a research lead, not a confirmed diesel buyer. The score is a prioritisation heuristic, not a probability of purchase.

Email validation is format-only in this pilot; it does not prove that a mailbox exists. WhatsApp is marked only from an explicit public click-to-chat link.

## Run manually

1. Open **Actions → Diesel buyer research — pilot → Run workflow**.
2. Choose China or Africa.
3. Paste public company or industry-directory URLs, one per line.
4. Choose the page limit and run.
5. Review the `Diesel Buyers` tab and the JSONL artifact.

Required repository secrets:
- `GOOGLE_SHEETS_API_URL`
- `GOOGLE_SHEETS_API_TOKEN`

These are the same Google Sheets bridge credentials already referenced by the existing manual OpenOutFind workflow. Do not paste tokens into source files, issues, or chat.

## Deliberate first-stage limits

This pilot does not scrape Google Maps, LinkedIn, Facebook, search-engine result pages, or sites requiring login. It does not yet perform broad automatic company discovery; the first run validates evidence extraction and the Sheets upsert with a small controlled URL list. After this passes, add permitted country-specific directories and source adapters, then test data quality before enabling a schedule.

No daily target is guaranteed. A small number of well-evidenced buyers is more valuable than a large list of unverified companies.
