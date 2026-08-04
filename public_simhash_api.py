"""External HTTP API for public URL SimHash generation."""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from simhash_matcher.public_simhash import public_simhash


class PublicSimhashRequest(BaseModel):
    url: str = Field(..., description="One http/https URL to render with Playwright")


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


@app.post("/public_simhash")
async def create_public_simhash(
    payload: PublicSimhashRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Process one URL."""
    await verify_api_key(x_api_key)
    return await public_simhash(payload.url)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("public_simhash_api:app", host="0.0.0.0", port=8000, reload=False)
