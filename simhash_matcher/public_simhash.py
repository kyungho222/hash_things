"""SimHash generation and MariaDB exact duplicate checker."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from simhash import Simhash

logger = logging.getLogger(__name__)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...]) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def make_simhash(subject: str | None, content: str | None) -> int | None:
    """Create a 128-bit SimHash from the crawler's subject and content fields."""
    missing = [name for name, value in (("subject", subject), ("content", content)) if not value]
    if missing:
        for field in missing:
            logger.warning("simhash 생성에 필요한 payload 중 %s 누락", field)
        return None
    return Simhash(_clean(subject).split(), f=128).value ^ Simhash(_clean(content).split(), f=128).value


def make_simhash_from_text(parsed_text: str | None) -> int | None:
    """Create a 128-bit SimHash from one public page parsing region."""
    if not parsed_text or not parsed_text.strip():
        logger.warning("simhash 생성에 필요한 payload 중 parsed_text 누락")
        return None
    return Simhash(_clean(parsed_text).split(), f=128).value


def format_simhash(value: int) -> str:
    return f"{value & ((1 << 128) - 1):032x}"


def hamming_distance(left: int, right: int) -> int:
    """Return the Hamming distance between two 128-bit SimHash values."""
    return (left ^ right).bit_count()


def _row_hash(row: Any) -> str | None:
    if isinstance(row, dict):
        value = row.get("hash")
    else:
        value = row[0] if row else None
    return str(value).lower() if value is not None else None


def find_simhash_match(
    connection: Connection,
    simhash: int,
    *,
    table: str,
    max_hamming_distance: int = 0,
) -> int | None:
    """Return the closest allowed distance, or None when no hash matches.

    A zero threshold keeps the original exact-only behavior. A positive threshold
    scans stored hashes in Python, so use it for validation first and introduce an
    indexed candidate strategy before enabling it on a very large table.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("table must be a safe SQL identifier")
    if max_hamming_distance < 0 or max_hamming_distance > 128:
        raise ValueError("max_hamming_distance must be between 0 and 128")
    cursor = connection.cursor()
    try:
        target = format_simhash(simhash)
        cursor.execute(f"SELECT 1 FROM {table} WHERE hash = %s LIMIT 1", (target,))
        if cursor.fetchone() is not None:
            return 0
        if max_hamming_distance == 0:
            return None
        cursor.execute(f"SELECT hash FROM {table} WHERE hash IS NOT NULL", ())
        distances = []
        for row in cursor.fetchall():
            stored = _row_hash(row)
            if stored and re.fullmatch(r"[0-9a-f]{32}", stored):
                distances.append(hamming_distance(simhash, int(stored, 16)))
        eligible = [distance for distance in distances if distance <= max_hamming_distance]
        return min(eligible) if eligible else None
    except Exception as error:
        if "unknown column" in str(error).lower() and "hash" in str(error).lower():
            logger.warning("simhash comparison skipped because hash column is missing")
            return None
        raise
    finally:
        cursor.close()


def has_simhash_match(connection: Connection, simhash: int, *, table: str, max_hamming_distance: int = 0) -> bool:
    return find_simhash_match(connection, simhash, table=table, max_hamming_distance=max_hamming_distance) is not None


def has_hash(connection: Connection, subject: str | None, content: str | None, *, table: str, max_hamming_distance: int = 0) -> bool:
    value = make_simhash(subject, content)
    return False if value is None else has_simhash_match(connection, value, table=table, max_hamming_distance=max_hamming_distance)


def check_hash(
    connection: Connection,
    subject: str | None,
    content: str | None,
    *,
    table: str,
    max_hamming_distance: int = 0,
) -> dict[str, bool | int | str | None]:
    """Create SimHash and return exact/similar duplicate status."""
    value = make_simhash(subject, content)
    if value is None:
        return {"duplicate": False, "save": False, "hash": None, "hamming_distance": None}
    distance = find_simhash_match(connection, value, table=table, max_hamming_distance=max_hamming_distance)
    duplicate = distance is not None
    return {
        "duplicate": duplicate,
        "save": not duplicate,
        "hash": format_simhash(value),
        "hamming_distance": distance,
    }


async def public_simhash(url: str | None) -> dict[str, Any]:
    """Render one URL, extract subject/content, and create a 128-bit SimHash."""
    if not url or not re.match(r"^https?://", url, re.I):
        return {"url": url, "simhash": None, "skipped": True, "skip_reason": "invalid_url"}
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="ko-KR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(800)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(500)
                extracted = await page.evaluate(r"""() => {
  const text = el => (el?.innerText || '').replace(/\s+/g, ' ').trim();
  const contentSelectors = ['#contents', '.board_detail_wrap', '.board-view', '.board_view', '.view-content', '.view_content', '.detail_con', 'article', '.sub_contents', '#content', '.contents', '.content', 'main'];
  const root = contentSelectors.map(selector => document.querySelector(selector)).find(Boolean) || document.body;
  const rows = [...root.querySelectorAll('tr')];
  const boardSignals = [
    root.matches('[class*="bbs"], [class*="board"], [class*="view"]'),
    Boolean(root.querySelector('.p-table__subject_text, .detail_tit, .board-title, .board_view_title, .p-table, table')),
    rows.some(row => ['\uC81C\uBAA9', '\uB0B4\uC6A9', '\uC791\uC131\uC790', '\uCCA8\uBD80'].includes(text(row.querySelector('th')))),
  ].filter(Boolean).length;
  const pageType = boardSignals >= 2 ? 'board' : 'webpage';
  const tableValue = label => {
    for (const row of rows) {
      const header = [...row.querySelectorAll('th')].find(node => text(node) === label);
      const value = header && row.querySelector('td');
      if (value && text(value)) return text(value);
    }
    return '';
  };
  const metadata = Object.fromEntries(rows.flatMap(row => {
    const label = text(row.querySelector('th'));
    const value = text(row.querySelector('td'));
    return label && value && !['\uC81C\uBAA9', '\uB0B4\uC6A9'].includes(label) ? [[label, value]] : [];
  }));
  const detailInfo = text(root.querySelector('.detail_info, .board-info, .board_info, [class*="metadata"]'));
  if (detailInfo) metadata.detail_info = detailInfo;
  const clone = root.cloneNode(true);
  clone.querySelectorAll(['script','style','noscript','header','#header','nav','#side','aside','footer','#footer','.floating','.floating_quick','.share_panel','.share','.banner','.site_panel','.btn_area','.board_nav','[class*="gnb"]','[class*="lnb"]','[class*="breadcrumb"]','[class*="prevnext"]','[class*="paging"]'].join(',')).forEach(el => el.remove());
  const boardCandidates = [
    [text(root.querySelector('.p-table__subject_text')), '.p-table__subject_text'],
    [text(root.querySelector('.detail_tit')), '.detail_tit'],
    [tableValue('\uC81C\uBAA9'), 'table:th[title]'],
    [text(root.querySelector('.board-title, .board_view_title')), '.board-title'],
  ];
  const commonCandidates = [
    [text(root.querySelector('h1, .page-title, .page_tit, h2')), 'content-heading'],
    [document.querySelector('meta[property="og:title"]')?.content || '', 'meta:og:title'],
    [document.title, 'document:title'],
  ];
  const weakBoardTitle = value => /\uC0C1\uC138\uBCF4\uAE30|\uC6B0\uB9AC\s*\uB3D9\s*\uC18C\uC2DD/.test(value) || /-\s*\uC131\uBD81\uAD6C\uCCAD$/.test(value);
  const selected = (pageType === 'board' ? [...boardCandidates, ...commonCandidates] : commonCandidates).find(([value, source]) => value && (pageType !== 'board' || !weakBoardTitle(value) || ['.p-table__subject_text', '.detail_tit', 'table:th[title]'].includes(source))) || ['', 'none'];
  const subject = selected[0].trim();
  const bodyNode = root.querySelector('td[title="\uB0B4\uC6A9"], .p-table__content, .board-content, .board_content, .view-content, .view_content');
  const tableContent = tableValue('\uB0B4\uC6A9');
  const content = tableContent || (bodyNode ? text(bodyNode) : text(clone));
  const contentSource = tableContent ? 'table:th[content]' : bodyNode ? '.content-node' : 'content-root';
  const hashText = [subject, content, ...Object.entries(metadata).flat()].filter(Boolean).join(' ').replace(/\uC870\uD68C\uC218\s*:\s*\d+/g, '').replace(/\s+/g, ' ').trim();
  const rootSelector = root.id ? '#' + root.id : root.tagName.toLowerCase() + (root.className ? '.' + String(root.className).trim().split(/\s+/).join('.') : '');
  return JSON.stringify({subject, content, metadata, hash_text: hashText, page_type: pageType, title_source: selected[1], content_source: contentSource, root_selector: rootSelector});
}""")
                try:
                    extracted = json.loads(extracted) if isinstance(extracted, str) else None
                except json.JSONDecodeError:
                    extracted = None
                if not isinstance(extracted, dict):
                    logger.warning("public SimHash 추출 결과가 비어 있습니다: %s", page.url)
                    return {"url": page.url, "simhash": None, "skipped": True, "skip_reason": "extraction_empty"}
                value = make_simhash_from_text(extracted.get("hash_text"))
                return {
                    "url": page.url,
                    "simhash": format_simhash(value) if value is not None else None,
                    "skipped": value is None,
                    "extracted": {
                        "subject": extracted.get("subject", ""),
                        "content": extracted.get("content", ""),
                        "content_length": len(extracted.get("content", "")),
                        "metadata": extracted.get("metadata", {}),
                        "page_type": extracted.get("page_type", "webpage"),
                        "title_source": extracted.get("title_source", ""),
                        "content_source": extracted.get("content_source", ""),
                        "root_selector": extracted.get("root_selector", ""),
                        "hash_input_length": len(extracted.get("hash_text", "")),
                    },
                }
            finally:
                await context.close()
                await browser.close()
    except Exception as error:
        logger.warning("public SimHash 처리 건너뜀: %s", error)
        return {"url": url, "simhash": None, "skipped": True, "skip_reason": f"{type(error).__name__}: {str(error)[:160]}"}


def load_local_env() -> None:
    """Load unset environment values from the service's optional .env file."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()


class PublicSimhashRequest(BaseModel):
    """Crawler identity and the URL to parse and compare."""

    url: str = Field(..., description="One http/https URL to render with Playwright")
    dbname: str = Field(..., description="MariaDB database name for this crawl")
    chatbotid: str = Field(..., description="Chatbot UUID used to select its LEARN_LIST table")


def _database_settings(dbname: str, chatbotid: str) -> tuple[dict[str, Any], str] | None:
    """Build the database and LEARN_LIST table from the request identity."""
    database = str(dbname or "").strip()
    compact_chatbot_id = re.sub(r"-", "", str(chatbotid or "").strip()).lower()
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError("invalid_dbname")
    if not re.fullmatch(r"[0-9a-f]{32}", compact_chatbot_id):
        raise ValueError("invalid_chatbotid")
    required = {
        "host": os.getenv("SIMHASH_DB_HOST", "").strip(),
        "user": os.getenv("SIMHASH_DB_USER", "").strip(),
        "password": os.getenv("SIMHASH_DB_PASSWORD", ""),
        "database": database,
    }
    if not all(required[key] for key in ("host", "user", "database")):
        return None
    table = f"ASADAL_{compact_chatbot_id[-12:]}_LEARN_LIST"
    return ({**required, "port": int(os.getenv("SIMHASH_DB_PORT", "3306")), "charset": "utf8mb4"}, table)


def _check_database_exact(simhash: str, dbname: str, chatbotid: str) -> tuple[bool | None, str | None]:
    """Check this chatbot's fixed hash column; None means the DB could not be checked."""
    configured = _database_settings(dbname, chatbotid)
    if configured is None:
        return None, "database_not_configured"
    settings, table = configured
    try:
        import pymysql

        connection = pymysql.connect(**settings)
        try:
            distance = find_simhash_match(connection, int(simhash, 16), table=table, max_hamming_distance=0)
            return distance is not None, None
        finally:
            connection.close()
    except Exception as error:
        logger.warning("public SimHash DB comparison skipped: %s", error)
        return None, f"database_check_failed: {type(error).__name__}"


PUBLIC_SIMHASH_CONCURRENCY = 10
_PUBLIC_SIMHASH_INFLIGHT: dict[tuple[str, str, str], asyncio.Task[dict[str, Any]]] = {}
_PUBLIC_SIMHASH_SEMAPHORE: asyncio.Semaphore | None = None


def _public_simhash_semaphore() -> asyncio.Semaphore:
    global _PUBLIC_SIMHASH_SEMAPHORE
    if _PUBLIC_SIMHASH_SEMAPHORE is None:
        _PUBLIC_SIMHASH_SEMAPHORE = asyncio.Semaphore(PUBLIC_SIMHASH_CONCURRENCY)
    return _PUBLIC_SIMHASH_SEMAPHORE


async def _resolve_public_simhash(url: str, dbname: str, chatbotid: str) -> dict[str, Any]:
    """Render, hash, and exact-compare one URL as one awaited result."""
    async with _public_simhash_semaphore():
        parsed = await public_simhash(url)
    simhash = parsed.get("simhash")
    if parsed.get("skipped") or not isinstance(simhash, str):
        return {
            "url": parsed.get("url") or url,
            "simhash": simhash,
            "duplicate": False,
            "save": False,
            "skipped": True,
            "skip_reason": parsed.get("skip_reason", "simhash_generation_failed"),
        }
    duplicate, database_reason = await asyncio.to_thread(_check_database_exact, simhash, dbname, chatbotid)
    if duplicate is None:
        return {
            "url": parsed.get("url") or url,
            "simhash": simhash,
            "duplicate": False,
            "save": False,
            "skipped": True,
            "skip_reason": database_reason,
        }
    return {
        "url": parsed.get("url") or url,
        "simhash": simhash,
        "duplicate": duplicate,
        "save": not duplicate,
        "skipped": False,
    }

app = FastAPI(
    title="Public SimHash API",
    version="2.1.0",
    description="Render one URL, create SimHash, and perform an exact MariaDB hash comparison.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "public_simhash", "mode": "synchronous", "max_concurrency": PUBLIC_SIMHASH_CONCURRENCY, "inflight": len(_PUBLIC_SIMHASH_INFLIGHT)}


async def _shared_public_simhash_result(url: str, dbname: str, chatbotid: str) -> dict[str, Any]:
    """Reuse only an unfinished task for the same URL, then await its result."""
    task_key = (url, dbname, chatbotid)
    task = _PUBLIC_SIMHASH_INFLIGHT.get(task_key)
    if task is None or task.done():
        task = asyncio.create_task(_resolve_public_simhash(url, dbname, chatbotid), name=f"public-simhash:{url[:80]}")
        _PUBLIC_SIMHASH_INFLIGHT[task_key] = task

        def _remove_finished_task(finished_task: asyncio.Task[dict[str, Any]]) -> None:
            if _PUBLIC_SIMHASH_INFLIGHT.get(task_key) is finished_task:
                _PUBLIC_SIMHASH_INFLIGHT.pop(task_key, None)

        task.add_done_callback(_remove_finished_task)
    return await asyncio.shield(task)


@app.post("/public_simhash")
async def create_public_simhash(payload: PublicSimhashRequest) -> dict[str, Any]:
    """Wait for the URL's shared task and return its final duplicate decision."""
    return await _shared_public_simhash_result(payload.url, payload.dbname, payload.chatbotid)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("simhash_matcher.public_simhash:app", host="0.0.0.0", port=8000, reload=False)
