from datetime import datetime
import asyncio
from html import unescape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import json
import os
import re
import unicodedata

from simhash_matcher.public_simhash import format_simhash, make_simhash, public_simhash

def load_local_env() -> None:
    """Load only missing variables from the dashboard root .env file."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_path):
        return
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()
MEMORY_DB: list[dict] = []
NEXT_ID = 1
F1_UUID_TAIL = "1062bd0194ea"


def clean(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", unescape(without_tags))).strip()


def parse_url(url: str) -> dict:
    raw = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read().decode("utf-8", "replace")
    scope = raw[raw.find("epform bbs gosi view"):] if "epform bbs gosi view" in raw else raw
    rows = re.findall(r"<tr>(.*?)</tr>", scope, re.S | re.I)
    values = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if cells:
            values.append(clean(cells[0]))

    if len(values) >= 7:
        subject, content = values[4], values[6]
    else:
        subject_match = re.search(r"<(?:h1|h2)[^>]*>(.*?)</(?:h1|h2)>", scope, re.S | re.I)
        page_title = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        body_match = re.search(r"<(?:article|main)[^>]*>(.*?)</(?:article|main)>", scope, re.S | re.I)
        subject = clean(subject_match.group(1)) if subject_match else clean(page_title.group(1)) if page_title else ""
        content = clean(body_match.group(1)) if body_match else ""

    simhash_value = make_simhash(subject, content)
    if simhash_value is None:
        raise ValueError("파싱한 subject 또는 content가 비어 있습니다.")

    date_match = re.search(r"(20\d{2}[-./]\d{1,2}[-./]\d{1,2})", scope)
    return {
        "url": url,
        "subject": subject,
        "content": content,
        "hash": format_simhash(simhash_value),
        "registered_date": date_match.group(1) if date_match else None,
    }

async def parse_public_url(url: str) -> dict:
    """Use the shared Playwright parser for dashboard memory DB actions."""
    result = await public_simhash(url)
    if result.get("skipped") or not result.get("simhash"):
        raise ValueError(result.get("skip_reason") or "public SimHash extraction failed")
    extracted = result.get("extracted") or {}
    return {
        "url": result.get("url") or url,
        "subject": extracted.get("subject", ""),
        "content": extracted.get("content", ""),
        "hash": result["simhash"],
        "registered_date": (extracted.get("metadata") or {}).get("registered_date"),
        "extracted": extracted,
    }


def f1_bridge(payload: dict) -> dict:
    action, db_name = str(payload.get("action") or ""), str(payload.get("db_name") or "").strip()
    token = os.getenv("F1_DEV_DB_BRIDGE_API_TOKEN", "").strip()
    if not db_name: raise ValueError("db_name을 입력하세요.")
    if not token: raise ValueError("F1_DEV_DB_BRIDGE_API_TOKEN 환경 변수가 설정되지 않았습니다.")
    if action == "connection": query, params = "SELECT 1 AS connected", []
    elif action == "hash":
        tail, value = F1_UUID_TAIL, str(payload.get("hash") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{32}", value): raise ValueError("32자리 SimHash를 입력하세요.")
        query, params = f"SELECT EXISTS (SELECT 1 FROM ASADAL_{tail}_LEARN_LIST WHERE hash = %s) AS is_exists", [value]
    else: raise ValueError("지원하지 않는 DB 테스트입니다.")
    request = Request(os.getenv("F1_DEV_DB_BRIDGE_URL", "https://api-aipro.chatbaram.com/api-aipro/f1_dev/Ai_Pro_filecrawler/backend/db-bridge/query"), data=json.dumps({"db_name":db_name,"engine":"mariadb","query":query,"params":params}).encode(), headers={"Content-Type":"application/json","X-F1-Dev-DB-Bridge-Token":token}, method="POST")
    with urlopen(request, timeout=30) as response: return json.loads(response.read().decode("utf-8"))

def public_record(record: dict) -> dict:
    """Return dashboard response data without parsed subject/content."""
    return {
        key: record.get(key)
        for key in ("id", "url", "hash", "saved_at")
        if key in record
    }


def public_parsed(parsed: dict) -> dict:
    """Return parse metadata without source text fields."""
    return {
        key: parsed.get(key)
        for key in ("url", "hash")
    }

def check_hash(parsed: dict) -> dict:
    matches = [record for record in MEMORY_DB if record["hash"] == parsed["hash"]]
    duplicate = bool(matches)
    return {
        "duplicate": duplicate,
        "save": not duplicate,
        "hash": parsed["hash"],
        "parsed": public_parsed(parsed),
        "matches": [public_record(record) for record in matches],
    }


class Handler(SimpleHTTPRequestHandler):
    def guess_type(self, path: str) -> str:
        extension = os.path.splitext(path)[1].lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(extension, super().guess_type(path))

    def out(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            if self.path != "/api/f1-db/test":
                return self.out({"error": "not found"}, 404)
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return self.out(f1_bridge(payload))
        except Exception as error:
            return self.out({"error": str(error)}, 400)
    def do_GET(self) -> None:
        global NEXT_ID
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        try:
            if parsed_url.path == "/api/public-simhash":
                url = query.get("url", [""])[0]
                return self.out(asyncio.run(public_simhash(url)))
            if parsed_url.path in ("/api/public-store", "/api/public-check"):
                url = query.get("url", [""])[0]
                source = asyncio.run(parse_public_url(url))
                result = check_hash(source)
                result["extracted"] = source["extracted"]
                if parsed_url.path == "/api/public-check":
                    return self.out(result)
                if result["duplicate"]:
                    result["saved"] = None
                    return self.out(result)
                saved = {
                    **source,
                    "id": NEXT_ID,
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                NEXT_ID += 1
                MEMORY_DB.append(saved)
                result["saved"] = public_record(saved)
                return self.out(result)
            if parsed_url.path == "/api/records":
                return self.out({"records": [public_record(record) for record in MEMORY_DB]})
            if parsed_url.path == "/api/clear":
                MEMORY_DB.clear()
                NEXT_ID = 1
                return self.out({"cleared": True})
            if parsed_url.path == "/api/delete":
                record_id = int(query.get("id", ["0"])[0])
                before = len(MEMORY_DB)
                MEMORY_DB[:] = [record for record in MEMORY_DB if record["id"] != record_id]
                return self.out({"deleted": len(MEMORY_DB) != before})
            if parsed_url.path not in ("/api/store", "/api/check"):
                return super().do_GET()

            url = query.get("url", [""])[0]
            if urlparse(url).scheme not in ("http", "https"):
                raise ValueError("http/https URL을 입력하세요.")

            source = parse_url(url)
            result = check_hash(source)
            if parsed_url.path == "/api/check":
                return self.out(result)

            if result["duplicate"]:
                result["saved"] = None
                return self.out(result)

            saved = {
                **source,
                "id": NEXT_ID,
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            NEXT_ID += 1
            MEMORY_DB.append(saved)
            result["saved"] = public_record(saved)
            return self.out(result)
        except Exception as error:
            return self.out({"error": str(error)}, 400)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 4173), Handler).serve_forever()

