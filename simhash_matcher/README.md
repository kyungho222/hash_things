# Public SimHash API

## Purpose

Send one URL to the HTTP API. Playwright renders the page, extracts subject, content, and metadata, then returns a 128-bit SimHash.

## Install

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Run

Set an API token before exposing the service.

```powershell
$env:PUBLIC_SIMHASH_API_TOKEN = "a-long-random-secret"
uvicorn public_simhash_api:app --host 0.0.0.0 --port 8000
```

- Health: `GET /health`
- Create SimHash: `POST /public_simhash`
- API docs: `http://server-address:8000/docs`

## Request

```bash
curl -X POST "http://server-address:8000/public_simhash"   -H "Content-Type: application/json"   -H "X-API-Key: API_TOKEN"   -d '{"url":"https://example.com/post/123"}'
```

```json
{"url": "https://example.com/post/123"}
```

A missing API token returns `503`; an invalid token returns `401`. Render or extraction failures return `skipped: true` with `skip_reason`.

## Similar duplicate threshold

`check_hash()` accepts `max_hamming_distance`. The default is `0` for exact-only comparison.

```python
result = check_hash(
    connection, subject, content,
    table="ASADAL_ce77dc5e9fd4_LEARN_LIST",
    max_hamming_distance=19,
)
```

The response adds `hamming_distance`. A positive threshold scans non-null `hash` values from the table in Python, so choose the threshold from real data and use an indexed candidate strategy before enabling it on a large table.


## Batch request

Use `POST /public_simhash/batch` when a crawler has multiple URLs. Each URL is processed independently and responses retain the same order as `urls`.

```json
{
  "urls": [
    "https://example.com/post/1",
    "https://example.com/post/2"
  ]
}
```

The response is `{ "results": [ ... ] }`. Parallelism is fixed at `5`. The batch request accepts up to 50 URLs.


## External call test

After starting the API, replace `server-address` and `API_TOKEN` with the deployed server address and configured token.

```bash
curl -X POST "http://server-address:8000/public_simhash/batch" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: API_TOKEN" \
  -d '{"urls":["https://example.com/post/1","https://example.com/post/2"]}'
```

A successful response returns HTTP `200` and a `results` array. Test the service first with `GET /health`.
