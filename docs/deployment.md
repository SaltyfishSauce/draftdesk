# Deployment

DraftDesk is one Python process and one SQLite file. Anything that can keep a process running will host it.

## Option 1: a small server or an always-on PC

```bash
pip install ".[anthropic]"
```

```bash
draftdesk serve -c config/config.yaml
```

This runs the dashboard on `127.0.0.1:8000` and polls the inbox in the background. Keep it alive with systemd, supervisor, or a Windows scheduled task set to run at startup.

## Option 2: Docker Compose

```bash
docker compose up -d --build
```

The compose file mounts `config/` and `knowledge/` read-only, keeps the database in a named volume, and publishes the dashboard on localhost only. The Dockerfile and compose file are provided as a starting point and have not been built in CI.

## Option 3: scheduled job, no dashboard

With `review.mode: mailbox`, drafts land in the mailbox's Drafts folder and no web server is needed. Run this from cron, a GitHub Actions schedule, or a cloud scheduler every few minutes:

```bash
draftdesk run-once -c config/config.yaml
```

It exits with status 1 if any email failed to draft, so the scheduler's own alerting can pick up problems. The database file must persist between runs, because it holds the polling cursor and the record of processed messages.

## Remote access to the dashboard

1. Set `DRAFTDESK_DASHBOARD_PASSWORD`.
2. Put a TLS-terminating reverse proxy (Caddy, nginx, a cloud load balancer) in front.
3. Prefer restricting access by VPN or an identity-aware proxy over exposing it to the internet.

## Operations

| Task | How |
|---|---|
| Health check | `GET /healthz` returns queue counts |
| Back up | Copy the SQLite file at `storage_path` |
| Reprocess after an outage | Nothing to do; the cursor did not advance past unprocessed mail |
| Retry failed drafts | Open the Errors tab and click "Try drafting again" |
| Rotate a credential | Update the environment variable and restart |
| Change policies | Edit files in `knowledge/` and restart |

## Cost

The only usage-based cost is the model. One email is one model call whose input is the knowledge base plus the email and order data, and whose output is a short reply. With a few pages of policies that is on the order of a few thousand input tokens and a few hundred output tokens per email; multiply by your provider's current prices and your daily volume. Providers with prompt caching bill the repeated knowledge-base portion at a reduced rate.

## Rollout suggestion

1. Run `draftdesk demo` to see the workflow.
2. Connect a test mailbox and the real store with read-only credentials. Send yourself the ten most common customer questions.
3. Fix what the drafts get wrong by improving `knowledge/`, not the code.
4. Switch to the real mailbox. For the first two weeks, open every draft rather than using one-click send, and keep a note of edits; they show what the knowledge base is missing.
