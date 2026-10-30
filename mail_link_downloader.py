"""T3-7: discover and fetch explicit voucher links from a mail body.

Downloaded bytes are returned to the common attachment collector. This module
never allocates file_seq or writes a file, so attachments and links share one
MD5 and numbering path. Public network addresses are checked and pinned for
each request and redirect. Full signed URLs are never placed in results.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
from html import unescape
from html.parser import HTMLParser
import http.client
import ipaddress
from io import BytesIO
import re
import socket
import ssl
from urllib.parse import parse_qs, urljoin, urlsplit
import zipfile


MAX_LINKS_PER_MAIL = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MAIL_BYTES = 24 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 7
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_EXT = {".pdf", ".xml", ".ofd", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".zip"}
_NAME = re.compile(r"filename\*?\s*=\s*(?:UTF-8''|\")?([^\";]+)", re.I)


@dataclass(frozen=True)
class LinkCandidate:
    url: str  # private transport input; never serialize or log this value
    label: str  # redacted user-facing source label


@dataclass(frozen=True)
class DownloadedFile:
    name: str
    content: bytes
    source_label: str
    file_type: str


@dataclass(frozen=True)
class LinkFailure:
    source_label: str
    reason: str


@dataclass(frozen=True)
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href", "")
            self._text = []

    def handle_data(self, data):
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            self.items.append((self._href, "".join(self._text)))
            self._href = ""
            self._text = []


def _part_text(part: Message) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError, ValueError, AttributeError):
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")


def _normalize_url(url: str) -> str:
    return unescape(url.strip().replace("\\&", "&")).rstrip(".,;，。；")


def _hint(url: str, anchor_text: str = "", context: str = "") -> str:
    try:
        split = urlsplit(url)
    except ValueError:
        return ""
    path = split.path.lower()
    query = parse_qs(split.query.lower())
    jflx = [value for value in query.get("jflx", []) if value in {"xml", "pdf", "ofd"}]
    if jflx:
        return jflx[0].upper()
    extension = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    if extension in _EXT:
        return extension[1:].upper()
    text = anchor_text or context
    if "下载" in text and any(key in text for key in ("发票", "票据", "凭证")):
        return "票据"
    return ""


def _label(url: str, hint: str) -> str:
    try:
        host = urlsplit(url).hostname or "未知站点"
    except ValueError:
        host = "未知站点"
    # Host and format are enough for a user to locate the original mail;
    # query and path may themselves contain a signature.
    return f"正文链接 {host}（{hint or '票据'}）"


def discover_links(msg: Message) -> list[LinkCandidate]:
    """Return explicit download candidates in MIME part order, deduplicated by URL."""
    items: list[LinkCandidate] = []
    seen: set[str] = set()
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type().lower()
        if kind not in {"text/plain", "text/html"}:
            continue
        body = _part_text(part)
        pairs: list[tuple[str, str, str]] = []
        if kind == "text/html":
            parser = _Anchors()
            parser.feed(body)
            pairs.extend((href, text, "") for href, text in parser.items)
        else:
            for match in _URL.finditer(body):
                pairs.append((match.group(), "", body[max(0, match.start()-48):match.start()]))
        for raw, anchor, context in pairs:
            url = _normalize_url(raw)
            hint = _hint(url, anchor, context)
            if not hint or hint == "OFD" or url in seen:
                continue
            seen.add(url)
            items.append(LinkCandidate(url, _label(url, hint)))
    return items


def _resolve_public(host: str, port: int, resolver) -> str:
    try:
        records = resolver(host, port, type=socket.SOCK_STREAM)
        ips = {entry[4][0] for entry in records}
    except (OSError, IndexError, TypeError) as exc:
        raise ValueError("LINK_DNS_ERROR") from exc
    if not ips:
        raise ValueError("LINK_DNS_ERROR")
    try:
        if any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("LINK_NONPUBLIC_TARGET")
    except ValueError as exc:
        if str(exc) == "LINK_NONPUBLIC_TARGET":
            raise
        raise ValueError("LINK_DNS_ERROR") from exc
    return sorted(ips)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str):
        super().__init__(host, port, timeout=CONNECT_TIMEOUT,
                         context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self):
        raw = socket.create_connection((self._pinned_ip, self.port),
                                       self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _request_once(url: str, ip: str, max_bytes: int) -> _Response:
    split = urlsplit(url)
    if split.scheme == "https":
        connection = _PinnedHTTPSConnection(split.hostname, split.port or 443, ip)
    else:
        # HTTP is only used for an explicitly allowed host. It still pins the
        # checked address, while the request's Host header remains the domain.
        connection = http.client.HTTPConnection(ip, split.port or 80,
                                                timeout=CONNECT_TIMEOUT)
    target = split.path or "/"
    if split.query:
        target += "?" + split.query
    try:
        connection.request("GET", target, headers={
            "Host": split.netloc, "Accept-Encoding": "identity",
            "User-Agent": "fapiao3.0/3", "Accept": "application/pdf,application/xml,application/ofd,image/*,application/zip",
        })
        response = connection.getresponse()
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError("LINK_SIZE_LIMIT")
        return _Response(response.status,
                         {key.lower(): value for key, value in response.getheaders()},
                         content)
    finally:
        connection.close()


def _checked_url(url: str, allow_http_hosts: frozenset[str]) -> tuple[str, int]:
    if len(url) > 2048 or any(ord(ch) < 32 for ch in url):
        raise ValueError("LINK_INVALID_URL")
    try:
        split = urlsplit(url)
        host = (split.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("LINK_INVALID_URL") from exc
    if split.username or split.password or not host or split.scheme not in {"https", "http"}:
        raise ValueError("LINK_INVALID_URL")
    if split.scheme == "http" and host not in allow_http_hosts:
        raise ValueError("LINK_HTTPS_REQUIRED")
    try:
        port = split.port or (443 if split.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("LINK_INVALID_URL") from exc
    return host, port


def _file_type(body: bytes, content_type: str) -> tuple[str, str]:
    if content_type.lower().split(";", 1)[0].strip() in {"text/html", "application/xhtml+xml"}:
        raise ValueError("LINK_HTML_RESPONSE")
    if body.startswith(b"%PDF-"):
        return "PDF", ".pdf"
    if body.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        try:
            with zipfile.ZipFile(BytesIO(body)) as archive:
                names = archive.namelist()
                if any(name.lower() == "ofd.xml" for name in names):
                    return "OFD", ".ofd"
                # Do not inflate entries here: the shared attachment chain
                # applies its own per-entry and aggregate extraction limits.
                return "ZIP", ".zip"
        except (zipfile.BadZipFile, OSError):
            pass
        raise ValueError("LINK_BAD_ZIP_OR_OFD")
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "图片", ".png"
    if body.startswith(b"\xff\xd8\xff"):
        return "图片", ".jpg"
    if body.startswith(b"BM"):
        return "图片", ".bmp"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "图片", ".webp"
    try:
        stripped = body.decode("utf-8-sig").lstrip()
    except UnicodeError:
        stripped = ""
    if stripped.startswith("<") and not stripped.lower().startswith(
            ("<!doctype html", "<html")):
        return "XML", ".xml"
    raise ValueError("LINK_UNSUPPORTED_RESPONSE")


def _safe_name(header: str, index: int, extension: str) -> str:
    match = _NAME.search(header)
    name = match.group(1).strip() if match else ""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
    if not name or not name.lower().endswith(extension):
        name = f"link_{index:02d}{extension}"
    return name[:120]


def fetch_link(candidate: LinkCandidate, *, index: int = 1,
               resolver=socket.getaddrinfo, requester=_request_once,
               allow_http_hosts: frozenset[str] = frozenset(),
               max_bytes: int = MAX_FILE_BYTES) -> DownloadedFile:
    """Fetch with address validation on each hop and strict byte/type limits.

    The resolver and requester parameters are for controlled tests. Production
    uses the pinned TLS requester above. Exceptions carry only reason codes.
    """
    url = candidate.url
    for _ in range(MAX_REDIRECTS + 1):
        host, port = _checked_url(url, allow_http_hosts)
        ip = _resolve_public(host, port, resolver)
        try:
            response = requester(url, ip, max_bytes)
        except ValueError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise ValueError("LINK_NETWORK_ERROR") from exc
        if len(response.body) > max_bytes:
            raise ValueError("LINK_SIZE_LIMIT")
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "")
            if not location:
                raise ValueError("LINK_BAD_REDIRECT")
            url = urljoin(url, location)
            continue
        if response.status != 200:
            raise ValueError(f"LINK_HTTP_{response.status}")
        file_type, extension = _file_type(response.body,
                                          response.headers.get("content-type", ""))
        name = _safe_name(response.headers.get("content-disposition", ""),
                          index, extension)
        return DownloadedFile(name, response.body, candidate.label, file_type)
    raise ValueError("LINK_REDIRECT_LIMIT")


def download_links(msg: Message, *, resolver=socket.getaddrinfo,
                   requester=_request_once,
                   allow_http_hosts: frozenset[str] = frozenset(),
                   max_links: int = MAX_LINKS_PER_MAIL,
                   max_total_bytes: int = MAX_MAIL_BYTES,
                   ) -> tuple[list[DownloadedFile], list[LinkFailure]]:
    """Continue other candidates after one fails; return no signed URL text."""
    files: list[DownloadedFile] = []
    failures: list[LinkFailure] = []
    candidates = discover_links(msg)
    for candidate in candidates[max_links:]:
        failures.append(LinkFailure(candidate.label, "LINK_COUNT_LIMIT"))
    total = 0
    for index, candidate in enumerate(candidates[:max_links], 1):
        try:
            item = fetch_link(candidate, index=index, resolver=resolver,
                              requester=requester,
                              allow_http_hosts=allow_http_hosts,
                              max_bytes=min(MAX_FILE_BYTES, max_total_bytes - total))
            total += len(item.content)
            files.append(item)
        except ValueError as exc:
            failures.append(LinkFailure(candidate.label, str(exc)))
    return files, failures
