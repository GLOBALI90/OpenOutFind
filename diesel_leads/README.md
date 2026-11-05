# Diesel Buyer Research and Outreach Pilot

This workflow lives inside the existing OpenOutFind repository and uses the existing Google Sheets `Leads` tab.

## Collection
- Starts from public company or industry-directory URLs supplied to a manual GitHub Actions run.
- Reads the existing Leads sheet first and deduplicates companies by website domain.
- Crawls only public HTML on the same host, obeys robots.txt, and keeps evidence/source URLs.
- Records direct diesel/gasoil wording separately from sector-only candidates. A mine, fleet, port, or factory is not labelled a confirmed diesel buyer unless its public pages contain direct diesel-related evidence.
- Extracts public business emails and ordinary phone numbers. WhatsApp is marked only from an explicit public click-to-chat link.

## Outreach
- Email drafts are stored in the same Leads row as `email_outreach_subject`, `email_outreach_body`, `email_outreach_recipient`, and `email_outreach_status=draft_ready`.
- Live email is available only when the manual run is explicitly set to `outreach_mode=send`. It requires `GMAIL_USERNAME` and `GMAIL_APP_PASSWORD` repository secrets.
- The live-send gate allows only records with `buyer_type=direct_diesel_evidence`, a public business email, no `email_opt_out` / `do_not_contact` flag, one email per run, and at most three successful emails per UTC day.
- A `sending` marker is written before SMTP. Interrupted/uncertain sends are marked `send_unknown` and are not automatically retried, to reduce duplicate contact risk.
- WhatsApp is not sent merely because a phone number exists. A separate `whatsapp_opt_in=true` field must be recorded on the lead before a WhatsApp sender may be enabled. The initial pilot stores WhatsApp evidence only.

## Run
1. Open Actions → Diesel buyer research and outreach → Run workflow.
2. Choose China first, then Africa in later runs.
3. Add a small list of public company URLs, one per line.
4. Start with `outreach_mode=draft` to inspect generated messages in Leads.
5. For live email, add the two Gmail secrets to this repository and select `outreach_mode=send`.

Required existing secrets:
- `GOOGLE_SHEETS_API_URL`
- `GOOGLE_SHEETS_API_TOKEN`

Additional secrets required for live email:
- `GMAIL_USERNAME`
- `GMAIL_APP_PASSWORD`

No credentials or tokens should be pasted into source code or chat. The initial pilot remains manual; it does not schedule runs or guarantee 30 new buyers per day. Broad automatic discovery, Google Maps, LinkedIn, and Facebook collection are not part of this first-stage pilot.
