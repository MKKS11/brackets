# Email scheduling helper

This helper schedules a 5:25am Eastern Time delivery run that renders and sends queued issues via a provider such as SES/SendGrid/Mailgun. It decorates all links with UTM parameters, injects a 1x1 tracking pixel, records sends in `tools/email/data/email_sends.json`, and ingests webhook or polled events into `tools/email/data/email_events.json`.

## Running the worker

```
node tools/email/worker.js
```

Environment variables:

- `EMAIL_PROVIDER` (`ses`|`sendgrid`|`mailgun`|`smtp`), `EMAIL_SES_URL`, `EMAIL_API_URL`, `EMAIL_API_KEY`, and SMTP credentials to reach a reliable provider.
- `EMAIL_FROM` sender address and `TRACKING_PIXEL_BASE` pixel host.
- `UTM_SOURCE`, `UTM_MEDIUM`, `UTM_CAMPAIGN` for default UTM labels; `EMAIL_WEBHOOK_PORT` for webhook ingestion.
- `EMAIL_EVENT_POLL_INTERVAL` to control the polling interval (default 5 minutes).

Queue issues in `tools/email/data/issue_queue.json` with:

```
[
  {
    "id": "issue-123",
    "subject": "Daily brief",
    "html": "<p>Hello reader</p>",
    "recipients": ["person@example.com"],
    "utm": {"utm_campaign": "daily"},
    "send_at": "2025-11-20T10:30:00Z"
  }
]
```

Webhook events should POST JSON to `/email/events` with `issue_id`, `message_id`, `recipient`, and `type` (e.g., `open`, `click`, `bounce`). Polling mirrors existing message IDs in queued issues via the optional `message_ids` array.
