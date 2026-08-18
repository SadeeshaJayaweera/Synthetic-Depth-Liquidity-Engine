# Massive.com API Specifications & Data Contracts

> **Note on SDK Rebranding & Direct HTTP Fallback Strategy**:
> Massive.com was previously known as Polygon.io (rebranded October 2025). The official client on PyPI is published as `massive` (version 2.x), though some legacy codebases and environments still reference `polygon-api-client`.
>
> To ensure maximum reliability across environments and package repositories, **Synthetic Depth Engine** implements a **dual-engine client architecture**:
> 1. **Primary**: Uses `massive.RESTClient` / `massive.WebSocketClient` if installed.
> 2. **Fallback**: If the official package is missing, version-conflicted, or ambiguous on PyPI, `MassiveRESTClient` seamlessly falls back to direct REST requests via **`httpx`** against `https://api.massive.com` with `Authorization: Bearer <API_KEY>` or query parameter authentication.
> 
> Direct `httpx` calls guarantee zero downtime, transparent pagination over `next_url`, and complete independence from client library packaging churn.

---

## 1. Authentication & Base URLs

### REST API
- **Base URL**: `https://api.massive.com`
- **Authentication Methods**:
  1. Header (Recommended): `Authorization: Bearer {YOUR_API_KEY}`
  2. Query Parameter: `?apiKey={YOUR_API_KEY}`
- **Rate Limits & Headers**:
  - Responses include `X-Request-Id` and rate limiting status headers.

### WebSocket API
- **Endpoints**:
  - Real-Time Stocks: `wss://socket.massive.com/stocks`
  - Delayed Stocks (15 min): `wss://delayed.massive.com/stocks`
- **Authentication Handshake**:
  Immediately upon connection, send JSON message:
  ```json
  {"action": "auth", "params": "<YOUR_API_KEY>"}
  ```
  Server responds with:
  ```json
  [{"ev": "status", "status": "auth_success", "message": "authenticated"}]
  ```
- **Subscription Syntax**:
  ```json
  {"action": "subscribe", "params": "T.AAPL,Q.AAPL"}
  ```
  Use `T.*` for all trades or `Q.*` for all quotes.

---

## 2. Historical Tick Data: Trades

### Endpoint
`GET /v3/trades/{stockTicker}`

### Query Parameters
| Parameter | Type | Required | Description |
|:---|:---|:---|:---|
| `stockTicker` | string | Yes | Case-sensitive ticker symbol (e.g., `AAPL`) |
| `timestamp` | string | No | `YYYY-MM-DD` or nanosecond Unix timestamp |
| `timestamp.gte`, `timestamp.gt` | string | No | Lower bound timestamp filter |
| `timestamp.lte`, `timestamp.lt` | string | No | Upper bound timestamp filter |
| `order` | string | No | Sort order: `asc` or `desc` (default `asc`) |
| `limit` | integer | No | Max results per page (default 1000, max 50000) |
| `sort` | string | No | Sort field (`timestamp`) |

### Response Schema
```json
{
  "status": "OK",
  "request_id": "a47d1beb8c11b6ae897ab76cdbbf35a3",
  "next_url": "https://api.massive.com/v3/trades/AAPL?cursor=...",
  "results": [
    {
      "id": "1063",
      "price": 171.55,
      "size": 100,
      "decimal_size": "100.0",
      "exchange": 11,
      "conditions": [12, 41],
      "correction": 0,
      "tape": 3,
      "participant_timestamp": 1517562000015577000,
      "sip_timestamp": 1517562000016036600,
      "trf_id": null,
      "trf_timestamp": null,
      "sequence_number": 1063
    }
  ]
}
```

### Field Definitions (REST Trades)
| Field | Type | Description |
|:---|:---|:---|
| `id` | string | Trade ID, unique per ticker/exchange/TRF combination. |
| `price` | float (number) | Trade execution price in USD per whole share. |
| `size` | float (number) | Trade volume (number of shares). |
| `decimal_size` | string | Precise fractional share size represented as string. |
| `exchange` | integer | Identifier of exchange venue where trade occurred. |
| `conditions` | array[int] | Trade condition codes (e.g., regular sale, odd lot, intermarket sweep). |
| `correction` | integer | Trade correction indicator (0 = regular, >0 = correction). |
| `tape` | integer | Tape ID: `1` = Tape A (NYSE), `2` = Tape B (NYSE Arca/American), `3` = Tape C (NASDAQ). |
| `participant_timestamp` | integer | Nanoseconds Unix timestamp when trade executed at the exchange. |
| `sip_timestamp` | integer | Nanoseconds Unix timestamp when SIP received and published the trade. |
| `trf_id` | integer/null | ID for Trade Reporting Facility (for off-exchange/dark pool trades). |
| `trf_timestamp` | integer/null | Nanoseconds Unix timestamp from TRF. |
| `sequence_number` | integer | Monotonically increasing sequence number per ticker per trading day. |

---

## 3. Historical Tick Data: NBBO Quotes

### Endpoint
`GET /v3/quotes/{stockTicker}`

### Query Parameters
| Parameter | Type | Required | Description |
|:---|:---|:---|:---|
| `stockTicker` | string | Yes | Case-sensitive ticker symbol (e.g., `AAPL`) |
| `timestamp` | string | No | `YYYY-MM-DD` or nanosecond Unix timestamp |
| `timestamp.gte`, `timestamp.gt` | string | No | Lower bound timestamp filter |
| `timestamp.lte`, `timestamp.lt` | string | No | Upper bound timestamp filter |
| `order` | string | No | Sort order: `asc` or `desc` (default `asc`) |
| `limit` | integer | No | Max results per page (default 1000, max 50000) |
| `sort` | string | No | Sort field (`timestamp`) |

### Response Schema
```json
{
  "status": "OK",
  "request_id": "a47d1beb8c11b6ae897ab76cdbbf35a3",
  "next_url": "https://api.massive.com/v3/quotes/AAPL?cursor=...",
  "results": [
    {
      "bid_price": 171.50,
      "bid_size": 15,
      "bid_exchange": 11,
      "ask_price": 171.55,
      "ask_size": 20,
      "ask_exchange": 12,
      "conditions": [1],
      "indicators": [604],
      "tape": 3,
      "participant_timestamp": 1517562000065321200,
      "sip_timestamp": 1517562000065700400,
      "trf_timestamp": null,
      "sequence_number": 2060
    }
  ]
}
```

### Field Definitions (REST Quotes)
| Field | Type | Description |
|:---|:---|:---|
| `bid_price` | float (number) | Prevailing National Best Bid price. |
| `bid_size` | integer/float | Bid size in round lots (or whole shares depending on market config). |
| `bid_exchange` | integer | Exchange ID contributing the best bid. |
| `ask_price` | float (number) | Prevailing National Best Offer (Ask) price. |
| `ask_size` | integer/float | Ask size in round lots (or whole shares). |
| `ask_exchange` | integer | Exchange ID contributing the best ask. |
| `conditions` | array[int] | Quote condition codes (e.g., regular, fast trading, slow quote). |
| `indicators` | array[int] | Quote indicators (e.g., Short Sale Restriction in effect). |
| `tape` | integer | `1` = Tape A, `2` = Tape B, `3` = Tape C. |
| `participant_timestamp` | integer | Nanoseconds Unix timestamp from exchange participant. |
| `sip_timestamp` | integer | Nanoseconds Unix timestamp from SIP. |
| `trf_timestamp` | integer/null | Nanoseconds Unix timestamp from TRF. |
| `sequence_number` | integer | Daily sequence number for quote updates. |

---

## 4. Real-Time Streaming: WebSocket Feeds

### Trade Stream (`T.*` / `T.<symbol>`)
- **Event Type (`ev`)**: `"T"`
- **Message Payload**:
```json
{
  "ev": "T",
  "sym": "AAPL",
  "x": 4,
  "i": "12345678",
  "z": 3,
  "p": 171.55,
  "s": 100,
  "ds": "100.0",
  "c": [0, 12],
  "t": 1708245600123,
  "pt": 1708245600115,
  "q": 3681328,
  "trfi": null,
  "trft": null
}
```
- **Field Mappings**:
  - `sym`: Stock Ticker
  - `x`: Exchange ID
  - `i`: Trade ID
  - `z`: Tape (1=A, 2=B, 3=C)
  - `p`: Price
  - `s`: Size (integer)
  - `ds`: Fractional/decimal size string
  - `c`: Array of condition codes
  - `t`: SIP Timestamp in **milliseconds** Unix epoch
  - `pt`: Participant timestamp in **milliseconds** Unix epoch (`pt <= t`)
  - `q`: Daily sequence number
  - `trfi`: TRF ID
  - `trft`: TRF timestamp in milliseconds

### Quote Stream (`Q.*` / `Q.<symbol>`)
- **Event Type (`ev`)**: `"Q"`
- **Message Payload**:
```json
{
  "ev": "Q",
  "sym": "AAPL",
  "bx": 4,
  "bp": 171.50,
  "bs": 15,
  "ax": 7,
  "ap": 171.55,
  "as": 20,
  "c": 0,
  "i": [604],
  "t": 1708245600123,
  "q": 50385480,
  "z": 3
}
```
- **Field Mappings**:
  - `sym`: Stock Ticker
  - `bx`: Bid Exchange ID
  - `bp`: Bid Price
  - `bs`: Bid Size (round lots)
  - `ax`: Ask Exchange ID
  - `ap`: Ask Price
  - `as`: Ask Size (round lots)
  - `c`: Condition code integer
  - `i`: Array of indicator codes
  - `t`: SIP Timestamp in **milliseconds** Unix epoch
  - `q`: Daily sequence number
  - `z`: Tape (1=A, 2=B, 3=C)

---

## 5. Aggregates & Smoke Test Endpoint

### Previous Day Aggregate Bar
`GET /v2/aggs/ticker/{stocksTicker}/prev`
- **Query Parameter**: `adjusted=true` (splits-adjusted)
- **Response**:
```json
{
  "status": "OK",
  "ticker": "AAPL",
  "queryCount": 1,
  "resultsCount": 1,
  "adjusted": true,
  "results": [
    {
      "T": "AAPL",
      "o": 182.15,
      "h": 184.20,
      "l": 181.80,
      "c": 183.50,
      "v": 54203100,
      "vw": 183.1205,
      "t": 1708117200000,
      "n": 612450
    }
  ]
}
```
- **Fields**:
  - `o`: Open price
  - `h`: High price
  - `l`: Low price
  - `c`: Close price
  - `v`: Trading volume
  - `vw`: Volume-weighted average price (VWAP)
  - `t`: Millisecond timestamp for bar start
  - `n`: Number of transactions in the aggregate window

---

## 6. Microstructure Analytics Schema Mapping

In downstream engine modules, tick feeds are ingested into DuckDB with normalized column names:

| Analytical Attribute | REST Trade Field | REST Quote Field | WS Trade Field | WS Quote Field | DuckDB Storage Type |
|:---|:---|:---|:---|:---|:---|
| Timestamp (ns) | `sip_timestamp` | `sip_timestamp` | `t * 1_000_000` | `t * 1_000_000` | `BIGINT` |
| Participant Time (ns) | `participant_timestamp` | `participant_timestamp` | `pt * 1_000_000` | - | `BIGINT` |
| Ticker | Path parameter | Path parameter | `sym` | `sym` | `VARCHAR` |
| Price | `price` | - | `p` | - | `DOUBLE` |
| Size | `size` | - | `s` | - | `DOUBLE` |
| Bid Price | - | `bid_price` | - | `bp` | `DOUBLE` |
| Bid Size | - | `bid_size` | - | `bs` | `DOUBLE` |
| Ask Price | - | `ask_price` | - | `ap` | `DOUBLE` |
| Ask Size | - | `ask_size` | - | `as` | `DOUBLE` |
| Exchange ID | `exchange` | `bid_exchange`/`ask_exchange` | `x` | `bx`/`ax` | `INTEGER` |
| Conditions | `conditions` | `conditions` | `c` | `c` | `INTEGER[]` |
| Sequence Number | `sequence_number` | `sequence_number` | `q` | `q` | `BIGINT` |
