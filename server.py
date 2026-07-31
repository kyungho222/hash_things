from datetime import datetime
from html import unescape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import json
import re
import unicodedata

from simhash_matcher.simhash_matcher import format_simhash, make_simhash

MEMORY_DB: list[dict] = []
NEXT_ID = 1


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
    def out(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        global NEXT_ID
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        try:
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