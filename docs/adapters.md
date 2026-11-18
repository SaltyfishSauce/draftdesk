# Adapters

Each layer is one abstract class. An adapter is a class that implements it and an entry in `registry.py`. Options under the matching section of `config.yaml` are passed to the adapter's constructor as keyword arguments.

## Verification status

| Adapter | How it is tested | Verify before production |
|---|---|---|
| `mock` (all three) | End to end, used by the demo and most tests | n/a |
| `graph` | Simulated HTTP: token caching, fetch mapping, reply draft, send | App permissions and admin consent; shared-mailbox access; how your tenant renders the reply body |
| `gmail` | Simulated HTTP: fetch, MIME parsing, threaded draft | OAuth consent screen and scopes; the search query for your inbox layout |
| `imap` | MIME building and parsing only; no IMAP/SMTP server in the test suite | Drafts folder name (varies: `Drafts`, `[Gmail]/Drafts`, `INBOX.Drafts`); app-password login; SMTP port |
| `bigcommerce` | Simulated HTTP, including empty (204) and missing (404) responses | Token scope; shipment fields for your carrier integrations |
| `shopify` | Simulated HTTP against the expected GraphQL response shape | API version string; that the token has `read_orders`; orders older than 60 days need `read_all_orders` |
| `woocommerce` | Simulated HTTP | Which tracking plugin you use; the adapter reads the "Shipment Tracking" metadata key |
| `anthropic` | Fake client: request shape, refusal and truncation handling | A real API key; set `fallbacks: false` on Bedrock, Vertex AI or Foundry |
| `openai_compatible` | Simulated HTTP: success, HTTP error, truncated output | Whether your server supports `response_format`; set `json_mode: false` if not |

None of the external-service adapters has been run against a live account in this repository. Treat the first run in your environment as an integration test: start with `review.mode: dashboard` and a test mailbox.

## Mailbox: `EmailProvider`

```python
class EmailProvider(ABC):
    name: str

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundEmail], str | None]: ...
    def create_reply_draft(self, target: ReplyTarget, body: str) -> str: ...
    def send_reply(self, target: ReplyTarget, body: str) -> None: ...
```

- The cursor is an opaque string the adapter owns. Returning a message twice is safe.
- `ReplyTarget.to_addr` is the resolved customer address. Always send to it rather than to the original sender, because contact-form emails come from the platform.
- Fill `InboundEmail.headers` with `Auto-Submitted`, `Precedence` and `List-Unsubscribe` when available; the filters use them.

### Microsoft 365 (`graph`)

| Option | Notes |
|---|---|
| `tenant_id`, `client_id` | From the Entra app registration |
| `auth` | `client_credentials` (default) or `refresh_token` |
| `client_secret` | Required for `client_credentials` |
| `refresh_token` | Required for `refresh_token` auth |
| `mailbox` | Required for `client_credentials`; omit to use `/me` with delegated auth |

Permissions: `Mail.ReadWrite`, plus `Mail.Send` only in dashboard mode. With application permissions, restrict the app to the support mailbox using an Exchange application access policy; otherwise it can read every mailbox in the tenant.

### Gmail (`gmail`)

Options: `client_id`, `client_secret`, `refresh_token`, `address`, optional `query`. Scopes: `gmail.readonly` and `gmail.compose`.

### IMAP + SMTP (`imap`)

Options: `imap_host`, `smtp_host`, `username`, `password`, optional `address`, `imap_port` (993), `smtp_port` (587, or 465 for implicit TLS), `inbox`, `drafts_folder`.

## Store: `CommerceProvider`

```python
class CommerceProvider(ABC):
    name: str

    def lookup(self, email: str, order_number: str | None = None) -> list[Order]: ...
```

- Return recent orders for the email, newest first.
- If `order_number` is given and is not among them, fetch and append that order as well.
- Always set `Order.customer_email`. The pipeline uses it to drop orders that do not belong to the sender.
- Use read-only credentials.

| Adapter | Options |
|---|---|
| `bigcommerce` | `store_hash`, `access_token` |
| `shopify` | `shop_domain`, `access_token`, optional `api_version` |
| `woocommerce` | `site_url`, `consumer_key`, `consumer_secret` |
| `none` | no options; replies rely on the knowledge base only |

## Model: `LLMProvider`

```python
class LLMProvider(ABC):
    name: str

    def draft(self, request: DraftRequest) -> DraftResult: ...
```

Call `prompt.build_prompt(request)` to get `(system, user)` and `prompt.parse_result(text, model)` to turn the response into a `DraftResult`. Raise `LLMError` when no usable draft comes back.

| Adapter | Options |
|---|---|
| `anthropic` | `model` (default `claude-opus-5-5`), `effort`, `max_tokens`, `fallbacks`. Credentials come from `ANTHROPIC_API_KEY`. |
| `openai_compatible` | `model`, `base_url`, `api_key`, `api_key_header` (`api-key` for Azure), `json_mode`, `extra_params`, `query_params` (e.g. Azure's `api-version`) |

## Writing a new adapter

A Magento store adapter, for example:

```python
# my_adapters/magento.py
import httpx
from draftdesk.commerce.base import CommerceProvider
from draftdesk.models import Order

class MagentoProvider(CommerceProvider):
    name = "magento"

    def __init__(self, base_url: str, token: str):
        self._http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})

    def lookup(self, email, order_number=None):
        ...  # call the API, return [Order(...)]
```

Register it before building the pipeline:

```python
from draftdesk.registry import register
register("commerce", "magento", "my_adapters.magento:MagentoProvider")
```

Then use `provider: magento` in the config. Accept an optional `client: httpx.Client` in the constructor, as the built-in adapters do, so tests can inject `httpx.MockTransport`.
