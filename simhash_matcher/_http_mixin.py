from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple
import asyncio, logging, re, time
from urllib.parse import urlparse, urlunparse
import httpx
from playwright.async_api import Page

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
    from simhash import Simhash as _Simhash
    _SIMHASH_AVAILABLE = True
except ImportError:
    _BeautifulSoup = None  # type: ignore[assignment]
    _Simhash = None  # type: ignore[assignment]
    _SIMHASH_AVAILABLE = False


from config import Config
from web_crawl.url_normalizer import URLNormalizer

if TYPE_CHECKING:
    from web_crawl.policy.redirect_policy import RedirectPolicy
    from web_crawl.http_manager import HttpManager
    from web_crawl.link_extractors.javascript_extractor import JavaScriptExtractor
    from web_crawl.link_extractors.event_handler_extractor import EventHandlerExtractor
    from web_crawl.link_extractors.iframe_extractor import IframeExtractor
    from web_crawl.domain_config import CrawlConfig

logger = logging.getLogger(__name__)


def _compute_simhash(html: str, url: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Legacy crawler fingerprint: 128-bit title/body SimHash XOR tuple."""
    del url
    if not html or not _SIMHASH_AVAILABLE:
        return None
    assert _BeautifulSoup is not None and _Simhash is not None
    try:
        try:
            soup = _BeautifulSoup(html, "lxml")
        except Exception:
            soup = _BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        def compact(tag) -> str:
            return " ".join(tag.get_text(" ", strip=True).split())

        title_tag = soup.find("title")
        title_text = compact(title_tag) if title_tag else ""
        og_title = soup.find("meta", property="og:title")
        if og_title:
            value = str(og_title.get("content", "") or "").strip()
            if value and value != title_text:
                title_text = f"{value} {title_text}".strip()
        context = []
        for selector in ("h1", "h2", ".breadcrumb", ".location", ".path", ".page-title", ".page_tit", ".active", ".current", ".selected", ".on"):
            for tag in soup.select(selector):
                value = compact(tag)
                if value and len(value) <= 120 and value not in context:
                    context.append(value)
                if len(context) >= 8:
                    break
            if len(context) >= 8:
                break
        if context:
            title_text = f"{title_text} {' '.join(context)}".strip()

        for tag in soup(["nav", "footer", "aside"]):
            tag.decompose()
        for tag in list(soup.find_all(True)):
            tokens = [str(tag.get("id") or ""), *(tag.get("class") or [])]
            if any(re.match(r"^(nav|footer|gnb|lnb|snb|sidebar|breadcrumb|skipnav|top-menu|topmenu)([-_]|$)", token, re.I) for token in tokens):
                tag.decompose()
        body_text = compact(soup)
        body_text = re.sub(r"https?://\S+", "", body_text)
        body_text = re.sub(r"(?<!\w)/[a-zA-Z0-9][a-zA-Z0-9/_.-]*/[a-zA-Z0-9._-]+\?[^\s]{3,}", "", body_text)
        body_text = " ".join(body_text.split())
        if not body_text:
            return None
        title_hash = int(_Simhash(title_text.split(), f=128).value or 0) if title_text else 0
        body_hash = int(_Simhash(body_text.split(), f=128).value or 0)
        return title_hash, body_hash ^ title_hash
    except Exception as error:
        logger.warning("[SimHash] 계산 실패: %s", error)
        return None

class _HttpMixin:
    HTTP_BODY_MAX_BYTES = 10 * 1024 * 1024

    # 하위 클래스(CrawlHandler)에서 제공하는 속성 선언 (IDE 정적 분석 지원)
    redirect_policy: RedirectPolicy
    http_mgr: HttpManager
    js_extractor: JavaScriptExtractor
    event_handler_extractor: EventHandlerExtractor
    iframe_extractor: IframeExtractor
    domain_config: CrawlConfig
    visited: Set[str]
    base_domain: str
    stats: Dict[str, int]
    domain_failures: Dict[str, int]
    domain_last_request: Dict[str, float]
    domain_circuit_breaker: Dict[str, dict]
    domain_rate_locks: Dict[str, asyncio.Lock]
    domain_request_semaphores: Dict[str, asyncio.Semaphore]
    domain_user_agents: Dict[str, str]
    _stop_event: asyncio.Event

    def _is_valid_url(self, url: str) -> bool: ...
    def _extract_urls_from_json(self, data: object, base_domain: str, collected: list) -> None: ...
    async def _extract_button_links(self, page: Page, current_url: str) -> List[str]: ...
    def _pagination_get_domain_concurrency(self, domain: str) -> int: ...


    async def _close_domain_popup(self, page: Page, url: str) -> None:
        """도메인별 팝업 처리 (실패 무시, 짧은 타임아웃)."""
        return

    def _register_redirect(self, source_url: str, target_url: str) -> None:
        self.redirect_policy.register_redirect(source_url, target_url)

    def _is_html_content_type(self, content_type: str) -> bool:
        if not content_type:
            return False
        value = content_type.lower().split(";")[0].strip()
        return value in ("text/html", "application/xhtml+xml")

    def _has_attachment_disposition(self, content_disposition: str) -> bool:
        if not content_disposition:
            return False
        lower = content_disposition.lower()
        return "attachment" in lower or "filename=" in lower

    def _has_binary_magic_bytes(self, content_bytes: bytes) -> bool:
        if not content_bytes:
            return False
        sample = content_bytes[:16]
        signatures = (
            b"%PDF-",
            b"PK\x03\x04",
            b"PK\x05\x06",
            b"PK\x07\x08",
            b"\x1f\x8b\x08",  # gzip
            b"Rar!\x1a\x07",
            b"7z\xbc\xaf\x27\x1c",
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"GIF87a",
            b"GIF89a",
            b"\x00\x00\x00\x18ftyp",
            b"\x00\x00\x00 ftyp",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",  # OLE/Office
        )
        return any(sample.startswith(sig) for sig in signatures)

    def _looks_like_html(self, content_bytes: bytes) -> bool:
        if not content_bytes:
            return False
        sample = content_bytes[:1024].lstrip()
        try:
            text = sample.decode("utf-8", errors="ignore").lower()
        except Exception:
            return False
        # XML/RSS/Atom 선언으로 시작하면 HTML이 아님
        if text.startswith("<?xml"):
            return False
        if (
            text.startswith("<rss")
            or text.startswith("<feed")
            or text.startswith("<atom")
        ):
            return False
        if text.startswith("<!doctype html"):
            return True
        return "<html" in text or "<head" in text or "<body" in text

    def _looks_like_xml(self, text: str) -> bool:
        """응답 본문이 XML/RSS/Atom인지 판별 (Content-Type이 text/html이어도 본문 기준)."""
        if not text:
            return False
        sample = text[:512].lstrip().lower()
        if sample.startswith("<?xml"):
            return True
        if (
            sample.startswith("<rss")
            or sample.startswith("<feed")
            or sample.startswith("<atom")
        ):
            return True
        return False

    def _is_non_html_response_headers(
        self, content_type: str, content_disposition: str
    ) -> bool:
        if self._has_attachment_disposition(content_disposition):
            return True
        if content_type and not self._is_html_content_type(content_type):
            return True
        return False

    def _content_length_exceeds_limit(self, response: httpx.Response) -> bool:
        raw_length = response.headers.get("content-length")
        if not raw_length:
            return False
        try:
            return int(raw_length) > self.HTTP_BODY_MAX_BYTES
        except ValueError:
            return False

    async def _read_response_bytes_capped(
        self, response: httpx.Response
    ) -> Optional[bytes]:
        chunks: List[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.HTTP_BODY_MAX_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def _decode_response_bytes(
        self, response: httpx.Response, content_bytes: bytes
    ) -> str:
        encoding = response.encoding or "utf-8"
        try:
            return content_bytes.decode(encoding, errors="replace")
        except LookupError:
            return content_bytes.decode("utf-8", errors="replace")

    async def _fingerprint_html(self, html: str, url: Optional[str] = None) -> Optional[Tuple[int, int]]:
        """HTML SimHash (title_hash, body_hash) 튜플. BFS 내 중복 감지용.
        asyncio.to_thread로 CPU 바운드 작업을 별도 스레드에서 실행 (이벤트 루프 블로킹 방지).
        """
        if url:
            try:
                classifier = getattr(self, "page_classifier", None)
                if classifier is not None:
                    page_type, _confidence = classifier.classify(url)
                    if page_type == "post":
                        return None
            except Exception:
                pass
        return await asyncio.to_thread(_compute_simhash, html, url)

    def _is_download_response(
        self, content_type: str, content_disposition: str, content_bytes: bytes
    ) -> bool:
        if self._is_non_html_response_headers(content_type, content_disposition):
            return True
        if content_type and self._is_html_content_type(content_type):
            return False
        if self._has_binary_magic_bytes(content_bytes):
            return True
        if content_bytes and not self._looks_like_html(content_bytes):
            return True
        return False

    @staticmethod
    def _host_without_www(netloc: str) -> str:
        host = netloc.lower().split("@")[-1].split(":")[0]
        return host[4:] if host.startswith("www.") else host

    def _build_browser_like_headers(self, url: str, domain: str) -> Dict[str, str]:
        """Build navigation-like headers for public sites that reject bare deep-link GETs."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        referer = None
        referer_map = getattr(self, "url_referers", None)
        if isinstance(referer_map, dict):
            referer = (
                referer_map.get(URLNormalizer.identity_key(url))
                or referer_map.get(URLNormalizer.normalize(url))
                or referer_map.get(url)
            )
        if not referer:
            referer = origin + "/"
        return {
            "User-Agent": self.domain_user_agents.get(
                domain, Config.get_random_user_agent()
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": referer,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }

    @staticmethod
    def _is_main_like_path(path: str) -> bool:
        normalized = (path or "/").strip().lower()
        if normalized in ("", "/"):
            return True
        last = normalized.rstrip("/").rsplit("/", 1)[-1]
        return bool(
            re.match(
                r"^(index|main|home|default)(\.(do|html?|php|asp|aspx|jsp))?$",
                last,
            )
        )

    @staticmethod
    def _is_redirected_error_page(original_url: str, response: httpx.Response) -> bool:
        """Detect server error landing pages reached via redirect.

        Some public sites redirect contextless deep-link requests to a shared
        error endpoint (for example /err/intro.html) and return HTTP 400 there.
        This is not proof the original URL is bad, so callers should avoid
        retry storms and hand it to browser/suspect verification instead.
        """
        final_url = str(response.url)
        try:
            original_parsed = urlparse(original_url)
            final_parsed = urlparse(final_url)
            original_identity = (
                original_parsed.scheme.lower(),
                original_parsed.netloc.lower(),
                original_parsed.path.rstrip("/") or "/",
            )
            final_identity = (
                final_parsed.scheme.lower(),
                final_parsed.netloc.lower(),
                final_parsed.path.rstrip("/") or "/",
            )
            if original_identity == final_identity:
                return False

            final_path = (final_parsed.path or "").lower()
            return final_path.startswith("/err/") or final_path.startswith("/error/")
        except Exception:
            return False

    def _is_www_soft_redirect(self, original_url: str, final_url: str) -> bool:
        """Detect www/non-www fallback redirects that collapse a deep path to a main page."""
        try:
            original = urlparse(original_url)
            final = urlparse(final_url)
            if not original.netloc or not final.netloc:
                return False

            original_host = original.netloc.lower().split(":")[0]
            final_host = final.netloc.lower().split(":")[0]
            if original_host == final_host:
                return False
            if self._host_without_www(original.netloc) != self._host_without_www(final.netloc):
                return False

            original_path = original.path or "/"
            final_path = final.path or "/"
            if self._is_main_like_path(original_path):
                return False
            if original_path.rstrip("/") == final_path.rstrip("/"):
                return False

            original_depth = len([p for p in original_path.split("/") if p])
            final_depth = len([p for p in final_path.split("/") if p])
            return self._is_main_like_path(final_path) or final_depth < original_depth
        except Exception:
            return False

    def _swap_www_host(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                return None
            if parsed.netloc.lower().startswith("www."):
                netloc = parsed.netloc[4:]
            else:
                netloc = f"www.{parsed.netloc}"
            return urlunparse(parsed._replace(netloc=netloc))
        except Exception:
            return None

    async def _retry_www_soft_redirect_httpx(
        self, original_url: str, final_url: str, headers: Dict[str, str]
    ) -> Optional[httpx.Response]:
        if not self._is_www_soft_redirect(original_url, final_url):
            return None

        alternate_url = self._swap_www_host(original_url)
        if not alternate_url or alternate_url == original_url:
            return None

        try:
            logger.info(
                f"[httpx][soft-redirect 의심] {original_url} → {final_url} | "
                f"반대 host 재시도: {alternate_url}"
            )
            retry_response = await self.http_mgr.client.get(alternate_url, headers=headers)
            retry_response.raise_for_status()
            retry_final_url = URLNormalizer.normalize(str(retry_response.url))
            if self._is_www_soft_redirect(alternate_url, retry_final_url):
                logger.info(
                    f"[httpx][soft-redirect 재시도 실패] {alternate_url} → {retry_final_url}"
                )
                return None
            logger.info(
                f"[httpx][soft-redirect 복구] {original_url} → {retry_final_url}"
            )
            return retry_response
        except Exception as e:
            logger.info(f"[httpx][soft-redirect 재시도 실패] {alternate_url} - {e}")
            return None

    def _is_soft_404(self, html: str) -> bool:
        if not html:
            return False  # 빈 응답은 봇 차단/SPA → soft-404 아님, Playwright fallback 대상
        # script/style/noscript 태그 제거 후 체크 (JS alert 등 오탐 방지)
        html_stripped = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html)
        html_stripped = re.sub(r'(?is)<!--.*?-->', ' ', html_stripped)
        html_lower = html_stripped.lower()

        # 링크가 10개 이상이면 정상 페이지 → 접근오류 구문 오탐 방지
        link_count_early = html_lower.count("<a ")

        # 404 계열: 링크 수 무관하게 체크 (실제 404 페이지도 nav 링크는 있음)
        hard_404_phrases = (
            "404 not found",
            "page not found",
            "error 404",
            "404 error",
            "요청하신 페이지를 찾을 수 없습니다",
            "페이지를 찾을 수 없습니다",
            "이 페이지가 없습니다",
            "해당 페이지가 없습니다",
            "존재하지 않는 페이지",
            "해당 페이지는 존재하지 않습니다",
            "문서를 찾을 수 없습니다",
        )
        for phrase in hard_404_phrases:
            if phrase in html_lower:
                logger.info(f"[Soft-404] 매칭 구문: '{phrase}'")
                return True

        # 접근오류 계열: 링크가 10개 이상이면 정상 페이지로 판단 → 스킵
        # (JS alert/hidden div 등에 문구가 있어도 실제 페이지는 정상)
        if link_count_early < 10:
            access_error_phrases = (
                "정상적인 접근이 아닙니다",
                "올바른 접근이 아닙니다",
                "잘못된 접근입니다",
                "유효하지 않은 접근",
                "잘못된 요청입니다",
                "허용되지 않는 접근",
                "비정상적인 접근",
                "요청이 차단되었습니다",
                "접속요청은",
                "웹보안 정책",
                "차단되었거나 요청이 유효하지",
            )
            for phrase in access_error_phrases:
                if phrase in html_lower:
                    logger.info(f"[Soft-404] 매칭 구문: '{phrase}'")
                    return True

        # ✅ 성능 개선: 복잡한 정규식 제거 (197KB HTML에서 3분+ 걸림)
        # 간단한 체크로 대체: 링크 개수만 확인
        link_count = html_lower.count("<a ")
        if link_count < 3:
            # 링크가 3개 미만이면 soft-404 가능성 체크
            if len(html) < 5000 and any(
                kw in html_lower
                for kw in ("404", "not found", "error", "찾을 수", "없습니다")
            ):
                return True

        # 기존 복잡한 정규식은 주석 처리 (catastrophic backtracking 문제)
        # cleaned = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\\1>", " ", html)
        # cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
        # cleaned = re.sub(r"\s+", " ", cleaned).strip()

        # if len(cleaned) < 20:
        #     return True

        # 기존 로직도 주석 처리 (cleaned 변수 없음)
        # if len(cleaned) < 60 and "<a" not in html_lower and "<img" not in html_lower:
        #     if any(
        #         kw in html_lower
        #         for kw in (
        #             "404",
        #             "not found",
        #             "error",
        #             "찾을 수",
        #             "없습니다",
        #             "존재하지",
        #         )
        #     ):
        #         return True

        return False

    async def _fetch_with_httpx(self, url: str) -> Tuple[Optional[str], str, int, int]:
        """httpx로 URL 가져오기 (안티 블로킹, 재시도)."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        max_retries = Config.HTTP_MAX_RETRIES

        # Dict 크기 제한: 최대 10,000개 도메인
        _MAX_DOMAIN_CACHE = 10_000
        if len(self.domain_rate_locks) > _MAX_DOMAIN_CACHE:
            # 가장 오래된 항목 일부 제거 (domain_last_request 기준 정렬)
            oldest = sorted(self.domain_last_request.items(), key=lambda x: x[1])[:1000]
            for old_domain, _ in oldest:
                lock = self.domain_rate_locks.get(old_domain)
                if lock and lock.locked():
                    continue  # 사용 중인 Lock은 삭제하지 않음 (직렬화 보장)
                self.domain_rate_locks.pop(old_domain, None)
                self.domain_request_semaphores.pop(old_domain, None)
                self.domain_last_request.pop(old_domain, None)
                self.domain_failures.pop(old_domain, 0)

        # 도메인별 rate limit Lock (없으면 생성)
        self.domain_rate_locks.setdefault(domain, asyncio.Lock())
        self.domain_request_semaphores.setdefault(
            domain,
            asyncio.Semaphore(max(1, int(getattr(Config, "HTTP_DOMAIN_CONCURRENCY", 3)))),
        )

        # 비표준 헤더 도메인은 httpx 스킵 → http.client 직행
        if domain in getattr(self, "domain_http_client_fallback", set()):
            return await asyncio.to_thread(self._http_client_get, url, domain)

        _rate_limit_wait = 0.0  # 429 대기 시간 (Lock 밖에서 sleep)
        _url_failed = False  # URL 단위 실패 플래그 (재시도마다 중복 카운트 방지)
        headers: Dict[str, str] = self._build_browser_like_headers(url, domain)

        for attempt in range(max_retries):
            # 이전 반복에서 429를 받았으면 Lock 밖에서 대기
            if _rate_limit_wait > 0:
                await asyncio.sleep(_rate_limit_wait)
                _rate_limit_wait = 0.0

            # 같은 도메인의 요청 수는 공통 상한만큼 허용한다.
            # rate lock은 요청 전체가 아니라 요청 간격 계산/갱신에만 사용한다.
            async with self.domain_request_semaphores[domain]:
                async with self.domain_rate_locks[domain]:
                    time_since_last = time.time() - self.domain_last_request.get(domain, 0.0)
                    wait = Config.REQUEST_DELAY_MIN - time_since_last
                    if wait > 0:
                        import random
                        # 지터 추가: ±20% 랜덤 변동으로 봇 감지 우회
                        jitter = random.uniform(-wait * 0.2, wait * 0.2)
                        await asyncio.sleep(max(0.1, wait + jitter))
                    self.domain_last_request[domain] = time.time()

                try:
                    # 도메인별 UA 고정 (세션 단위)
                    if domain not in self.domain_user_agents:
                        self.domain_user_agents[domain] = Config.get_random_user_agent()
                    user_agent = self.domain_user_agents[domain]
                    headers["User-Agent"] = user_agent
                    headers["Referer"] = self._build_browser_like_headers(url, domain)["Referer"]

                    async with self.http_mgr.client.stream("GET", url, headers=headers) as response:
                        status_code = response.status_code

                        if status_code in [404, 410]:
                            logger.info(f"[{status_code}] {url}")
                            return None, "", 0, status_code

                        response.raise_for_status()
                        self.domain_failures[domain] = 0

                        final_url = URLNormalizer.normalize(str(response.url))
                        soft_redirect_detected = self._is_www_soft_redirect(url, final_url)
                        if soft_redirect_detected:
                            retry_response = await self._retry_www_soft_redirect_httpx(
                                url, final_url, headers
                            )
                            if retry_response is None:
                                logger.info(
                                    f"[httpx][soft-redirect 차단] 메인/대표 페이지 저장 방지: "
                                    f"{url} → {final_url}"
                                )
                                return None, "", 0, 404
                            response = retry_response
                            status_code = response.status_code
                            final_url = URLNormalizer.normalize(str(response.url))

                        # ✅ 리다이렉트로 base_domain과 다른 netloc에 도착하면 원본 URL로 대체
                        # (예: www.kma.go.kr/kma → testweather.kma.go.kr/kma 루프 방지)
                        _req_netloc = urlparse(url).netloc.replace("www.", "")
                        _final_netloc_check = urlparse(final_url).netloc.replace("www.", "")
                        if _req_netloc and _final_netloc_check and _req_netloc != _final_netloc_check:
                            _base_domain_bare = self.base_domain.replace("www.", "")
                            if _req_netloc == _base_domain_bare and _final_netloc_check != _base_domain_bare:
                                logger.info(
                                    f"[httpx][리다이렉트 도메인 변경] {url} → {final_url} "
                                    f"(base_domain 밖, 원본 URL로 대체)"
                                )
                                final_url = URLNormalizer.normalize(url)

                        if not self._is_valid_url(final_url):
                            return None, "", 0, 200

                        content_type = response.headers.get("content-type", "")
                        content_disposition = response.headers.get("content-disposition", "")
                        if self._is_non_html_response_headers(content_type, content_disposition):
                            logger.info(f"[httpx] 다운로드/비HTML 헤더 스킵: {url}")
                            return None, "", 0, 415
                        if self._content_length_exceeds_limit(response):
                            logger.info(
                                f"[httpx] 본문 크기 초과 스킵: {url} "
                                f"(limit={self.HTTP_BODY_MAX_BYTES} bytes)"
                            )
                            return None, "", 0, 413

                        content_bytes = await self._read_response_bytes_capped(response)
                        if content_bytes is None:
                            logger.info(
                                f"[httpx] 본문 읽기 중 크기 초과 스킵: {url} "
                                f"(limit={self.HTTP_BODY_MAX_BYTES} bytes)"
                            )
                            return None, "", 0, 413

                    if self._is_download_response(
                        content_type, content_disposition, content_bytes
                    ):
                        logger.info(f"[httpx] 다운로드/비HTML 스킵: {url}")
                        return None, "", 0, 415

                    html = self._decode_response_bytes(response, content_bytes)
                    if self._looks_like_xml(html):
                        logger.info(f"[httpx] XML/RSS 응답 스킵: {url}")
                        return None, "", 0, 415
                    if self._is_soft_404(html):
                        logger.info(f"[httpx] Soft-404 감지: {url}")
                        return None, "", 0, 404
                    link_count = html.count("<a ")
                    return final_url, html, link_count, status_code

                except httpx.HTTPStatusError as e:
                    error_status_code = e.response.status_code
                    if error_status_code in [404, 410]:
                        return None, "", 0, error_status_code
                    if error_status_code == 400 and self._is_redirected_error_page(url, e.response):
                        logger.info(
                            f"[httpx][오류페이지 리다이렉트] {url} → {e.response.url} "
                            "(400, httpx retry 없이 브라우저 검증 대상으로 이관)"
                        )
                        return None, "", 0, -2
                    if error_status_code == 429:
                        self.stats["rate_limited"] += 1
                        retry_after = e.response.headers.get("Retry-After", "60")
                        try:
                            _rate_limit_wait = float(retry_after)
                        except ValueError:
                            # HTTP 날짜 형식(RFC 7231) 처리
                            try:
                                from email.utils import parsedate_to_datetime
                                import datetime
                                retry_dt = parsedate_to_datetime(retry_after)
                                _rate_limit_wait = max(0.0, (retry_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                            except Exception:
                                _rate_limit_wait = 60.0
                        _url_failed = True
                        # Lock 해제 후 다음 iteration 시작 시 sleep
                        continue
                    elif error_status_code in [400, 503]:
                        # 400: 일부 사이트(gangdong.go.kr 등)가 일시적으로 400 반환
                        # 503: 서버 일시 불가
                        _url_failed = True
                        backoff = min(
                            Config.HTTP_BACKOFF_BASE**attempt, Config.HTTP_MAX_BACKOFF
                        )
                        _rate_limit_wait = backoff  # Lock 밖에서 sleep (기존 _rate_limit_wait 메커니즘 재사용)
                        continue
                    else:
                        return None, "", 0, error_status_code

                except httpx.TooManyRedirects:
                    # 리다이렉트 루프 감지 → trailing slash 없으면 붙여서 재시도
                    _parsed_url = urlparse(url)
                    if _parsed_url.path and not _parsed_url.path.endswith("/"):
                        _url_with_slash = url + "/"
                        logger.info(f"[httpx][리다이렉트 루프] trailing slash 추가 재시도: {url} → {_url_with_slash}")
                        try:
                            _resp2 = await self.http_mgr.client.get(_url_with_slash, headers=headers)
                            _resp2.raise_for_status()
                            _final2 = URLNormalizer.normalize(str(_resp2.url))
                            _html2 = _resp2.text
                            _lc2 = _html2.count("<a ")
                            return _final2, _html2, _lc2, _resp2.status_code
                        except Exception:
                            pass
                    logger.info(f"[httpx][리다이렉트 루프] {url}")
                    return None, "", 0, -1

                except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
                    _url_failed = True
                    if attempt < max_retries - 1:
                        backoff = min(
                            Config.HTTP_BACKOFF_BASE**attempt, Config.HTTP_MAX_BACKOFF
                        )
                        jitter = backoff * 0.2
                        _rate_limit_wait = max(0.1, backoff + (jitter * (0.5 - time.time() % 1)))
                        logger.warning(f"[httpx] 재시도 {attempt + 1}/{max_retries}: {url}")
                    else:
                        break

                except httpx.RemoteProtocolError as e:
                    # continuation line / illegal header line: 서버가 RFC 비표준 HTTP 헤더를 보냄 → http.client fallback
                    _e_lower = str(e).lower()
                    if "continuation line" in _e_lower or "illegal header line" in _e_lower:
                        logger.warning(f"[httpx] 비표준 헤더, http.client fallback: {url} - {e}")
                        result = await asyncio.to_thread(self._http_client_get, url, domain)
                        if result[0] is not None:
                            # 성공 시 도메인 등록 → 이후 같은 도메인은 httpx 스킵
                            getattr(self, "domain_http_client_fallback", set()).add(domain)
                        return result
                    # 그 외 RemoteProtocolError: 서버가 연결을 중간에 끊음 → 재시도
                    _url_failed = True
                    if attempt < max_retries - 1:
                        backoff = min(
                            Config.HTTP_BACKOFF_BASE**attempt, Config.HTTP_MAX_BACKOFF
                        )
                        logger.warning(f"[httpx] 연결 끊김, {backoff:.1f}s 후 재시도: {url} - {e}")
                        _rate_limit_wait = backoff
                    else:
                        logger.error(f"[httpx] 연결 끊김 (재시도 소진): {url} - {e}")
                        break

                except Exception as e:
                    _url_failed = True
                    logger.error(f"[httpx] 예상치 못한 오류: {url} - {e}")
                    break

        # ✅ URL 단위로 1회만 카운트 (재시도마다 중복 카운트 방지)
        if _url_failed:
            self.domain_failures[domain] += 1
        return None, "", 0, 0

    def _http_client_get(self, url: str, domain: str):
        """비표준 HTTP 헤더 도메인용 동기 fallback (asyncio.to_thread로 호출)."""
        import http.client as _http_client
        import ssl as _ssl
        import urllib.parse as _urlparse_mod
        _conn = None
        try:
            _parsed = _urlparse_mod.urlparse(url)
            _ssl_context = None
            if _parsed.scheme == "https":
                # httpx 기본 클라이언트는 기관 사이트의 누락/비표준 인증서 체인을
                # 허용하도록 구성되어 있다. RFC 비표준 헤더 때문에 http.client로
                # 내려오는 fallback도 같은 SSL 허용 정책을 써야 정적탐색이 끊기지 않는다.
                _ssl_context = _ssl._create_unverified_context()
                try:
                    _ssl_context.set_ciphers("DEFAULT:@SECLEVEL=0")
                except Exception:
                    pass
            _conn = (
                _http_client.HTTPSConnection(_parsed.netloc, timeout=15, context=_ssl_context)
                if _parsed.scheme == "https"
                else _http_client.HTTPConnection(_parsed.netloc, timeout=15)
            )
            _path = _parsed.path or "/"
            if _parsed.query:
                _path += "?" + _parsed.query
            _headers = self._build_browser_like_headers(url, domain)
            # 비표준 헤더 때문에 http.client가 정상 헤더 파싱에 실패하면
            # Content-Length/Transfer-Encoding을 모르는 상태가 되어 서버 close까지
            # read()가 대기할 수 있다. KFI처럼 keep-alive로 버티는 서버는 timeout이
            # 나므로 fallback에서는 명시적으로 close를 요구한다.
            _headers["Connection"] = "close"
            _conn.request(
                "GET",
                _path,
                headers=_headers,
            )
            _resp = _conn.getresponse()
            if _resp.status in [404, 410]:
                return None, "", 0, _resp.status
            _raw = _resp.read()
            _encoding = "utf-8"
            _ct = _resp.getheader("Content-Type", "")
            if "charset=" in _ct:
                _encoding = _ct.split("charset=")[-1].strip().split(";")[0].strip()
            # 헤더 파싱 실패 시 chunked framing(예: b"1187b\r\n<html...")이
            # body에 그대로 섞일 수 있어 HTML 파싱 전에 best-effort로 제거한다.
            try:
                if re.match(rb"^[0-9a-fA-F]+\r?\n", _raw[:32] or b""):
                    _chunks = []
                    _pos = 0
                    while _pos < len(_raw):
                        _line_end = _raw.find(b"\n", _pos)
                        if _line_end < 0:
                            break
                        _size_line = _raw[_pos:_line_end].strip().split(b";", 1)[0]
                        try:
                            _size = int(_size_line, 16)
                        except ValueError:
                            _chunks = []
                            break
                        _pos = _line_end + 1
                        if _size == 0:
                            break
                        _chunks.append(_raw[_pos:_pos + _size])
                        _pos += _size
                        if _raw[_pos:_pos + 2] == b"\r\n":
                            _pos += 2
                        elif _raw[_pos:_pos + 1] == b"\n":
                            _pos += 1
                    if _chunks:
                        _raw = b"".join(_chunks)
            except Exception:
                pass
            html = _raw.decode(_encoding, errors="replace")
            _final_url = URLNormalizer.normalize(url)
            link_count = html.count("<a ")
            logger.info(f"[http.client] {url}")
            return _final_url, html, link_count, _resp.status
        except Exception as e:
            logger.warning(f"[http.client 실패] {url} - {e}")
            return None, "", 0, 0
        finally:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass
