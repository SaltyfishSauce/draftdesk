# Security and privacy

A support inbox receives text from anyone on the internet, and this system passes that text to a language model that also sees order data. This page describes the risks that follow and what the design does about each.

## Threats and controls

| Risk | Control |
|---|---|
| **The model sends something wrong or harmful** | Nothing is sent without a person clicking Approve. There is no auto-send option to misconfigure. |
| **Prompt injection** in a customer email ("ignore your instructions...") | The model has no tools and makes one call per email, so there is nothing for an injected instruction to trigger. The email is delimited and described to the model as content, not instructions. A pattern check flags likely attempts, and a flagged draft cannot be sent from the queue with one click. |
| **Disclosure of another customer's order** by quoting their order number | Order details are given to the model only when the order's email matches the sender. A mismatch drops the order and raises a flag. |
| **Reply redirected to an attacker** by putting `Email: victim@...` in the body | The body's Email field and the Reply-To header are trusted only for senders listed in `store.form_senders`. |
| **Invented facts**: fake tracking numbers, order numbers, links | Code compares the draft against the looked-up data and knowledge base and flags anything that does not appear there. |
| **Phishing links copied from the customer's email into the reply** | Same link check: only hosts found in the knowledge base or order tracking URLs pass. |
| **Double send** from a double click or two reviewers | The draft is claimed with an atomic status change before sending. |
| **Stored XSS** via email content in the dashboard | All content is rendered through Jinja2 autoescaping; there is a test for it. |
| **CSRF** against the send button | Every POST form carries a per-process token that is verified server-side. |
| **Leaked credentials** | Secrets are referenced as `${ENV_VAR}` in config and never written to the YAML file, the database or logs. `.env` and `config/config.yaml` are git-ignored. |

The guardrails are pattern-based and will miss things. They reduce how much the reviewer has to catch; they do not replace the reviewer.

## Least privilege

- **Store API:** read-only order scope. DraftDesk never writes to the store.
- **Mailbox:** in `mailbox` review mode the app needs read and draft permissions only, no send permission. With Microsoft Graph application permissions, add an application access policy so the app can reach only the support mailbox.
- **Model:** receives the knowledge base, the email, and up to `max_orders` orders with items and tracking. Payment details and postal addresses are never passed to the model or stored.

## Data handling

- Customer emails and order summaries are stored in the SQLite file at `storage_path` so that drafts can be reviewed and audited. Protect that file as you would the mailbox itself, and set a retention policy that suits your obligations (for example, a scheduled job that deletes rows older than 90 days).
- Email content and order summaries are sent to the model provider you configure. Check that provider's data retention and training terms against your privacy policy. To keep data in your own environment, point the `openai_compatible` adapter at a self-hosted model.
- If you serve customers in jurisdictions with data-protection law (GDPR, CCPA and similar), the model provider is a sub-processor and should be listed as one.

## Running the dashboard

- It binds to `127.0.0.1` by default.
- Set `DRAFTDESK_DASHBOARD_PASSWORD` whenever it is reachable from another machine; the server logs a warning if you do not.
- Basic authentication sends the password with each request, so serve it over HTTPS behind a reverse proxy, or better, behind your organisation's SSO proxy or VPN.
- The username entered at login is recorded as the actor in the audit trail. It is not verified, so treat the audit trail as a record of intent rather than proof of identity unless an SSO proxy supplies the name.
