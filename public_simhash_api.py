"""External HTTP API for public URL SimHash generation."""
from __future__ import annotations

import asyncio
import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from simhash_matcher.public_simhash import public_simhash


class PublicSimhashRequest(BaseModel):
    url: str = Field(..., description="One http/https URL to render with Playwright")


class PublicSimhashBatchRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=50, description="URLs to process independently (maximum 50)")


app = FastAPI(
    title="Public SimHash API",
    version="1.1.0",
    description="Render URLs, extract post data, and return 128-bit SimHash values.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


async def verify_api_key(x_api_key: str | None) -> None:
    api_token = os.getenv("PUBLIC_SIMHASH_API_TOKEN", "")
    if not api_token:
        raise HTTPException(status_code=503, detail="PUBLIC_SIMHASH_API_TOKEN is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, api_token):
        raise HTTPException(status_code=401, detail="invalid API token")


MAX_CONCURRENCY = 5


@app.post("/public_simhash")
async def create_public_simhash(
    payload: PublicSimhashRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Process one URL."""
    await verify_api_key(x_api_key)
    return await public_simhash(payload.url)


@app.post("/public_simhash/batch")
async def create_public_simhash_batch(
    payload: PublicSimhashBatchRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Process URLs independently in parallel and preserve input order."""
    await verify_api_key(x_api_key)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def process(url: str) -> dict[str, Any]:
        async with semaphore:
            return await public_simhash(url)

    return {"results": await asyncio.gather(*(process(url) for url in payload.urls))}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("public_simhash_api:app", host="0.0.0.0", port=8000, reload=False)
