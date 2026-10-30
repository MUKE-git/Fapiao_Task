"""T3-7 controlled transport tests; all mail, DNS and HTTP results are fixed."""
from __future__ import annotations

import io
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mail_link_downloader import (
    LinkCandidate, _Response, discover_links, download_links, fetch_link,
)


def public_dns(host, port, **kwargs):
    return [(0, 0, 0, "", ("8.8.8.8", port))]


def mail(*, plain="", html=""):
    msg = EmailMessage()
    msg["Subject"] = "示例发票"
    msg.set_content(plain or "正文")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


class LinkDownloadTests(unittest.TestCase):
    def test_discover_explicit_links_and_keep_distinct_jflx(self):
        msg = mail(plain="发票下载 https://e.example/get?jflx=xml\\&sign=secret。\n"
                         "退订 https://e.example/unsubscribe",
                   html='<a href="https://e.example/get?jflx=pdf&amp;sign=x">发票 PDF 文件下载</a>'
                        '<a href="https://e.example/get?jflx=ofd&amp;sign=y">发票 OFD 文件下载</a>')
        links = discover_links(msg)
        self.assertEqual(len(links), 2)
        self.assertIn("jflx=xml&sign=secret", links[0].url)
        self.assertEqual(["XML", "PDF"],
                         [link.label.rsplit("（", 1)[-1].rstrip("）") for link in links])
        self.assertTrue(all("secret" not in link.label for link in links))

    def test_download_pdf_and_do_not_expose_signed_url(self):
        seen = []
        def requester(url, ip, budget):
            seen.append((url, ip, budget))
            return _Response(200, {"content-type": "application/pdf",
                                   "content-disposition": 'attachment; filename="invoice.pdf"'},
                             b"%PDF-1.4\n1 0 obj\n")
        files, failures = download_links(mail(plain="https://e.example/get?jflx=pdf&sign=secret"),
                                         resolver=public_dns, requester=requester)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "invoice.pdf")
        self.assertEqual(files[0].file_type, "PDF")
        self.assertEqual(files[0].content[:5], b"%PDF-")
        self.assertEqual(failures, [])
        self.assertEqual(seen[0][1], "8.8.8.8")
        self.assertNotIn("secret", repr(files))

    def test_redirect_to_private_address_is_blocked(self):
        def resolver(host, port, **kwargs):
            address = "127.0.0.1" if host == "private.example" else "8.8.8.8"
            return [(0, 0, 0, "", (address, port))]
        calls = []
        def requester(url, ip, budget):
            calls.append(url)
            return _Response(302, {"location": "https://private.example/get?sign=secret"}, b"")
        files, failures = download_links(mail(plain="https://e.example/get?jflx=xml&sign=secret"),
                                         resolver=resolver, requester=requester)
        self.assertFalse(files)
        self.assertEqual(failures[0].reason, "LINK_NONPUBLIC_TARGET")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", repr(failures))

    def test_http_requires_explicit_exception(self):
        candidate = LinkCandidate("http://e.example/a.pdf", "正文链接 e.example")
        with self.assertRaisesRegex(ValueError, "LINK_HTTPS_REQUIRED"):
            fetch_link(candidate, resolver=public_dns)

    def test_html_error_page_and_size_limit(self):
        msg = mail(plain="https://e.example/a.pdf")
        html = lambda *_: _Response(200, {"content-type": "text/html"}, b"<html>login</html>")
        files, failures = download_links(msg, resolver=public_dns, requester=html)
        self.assertFalse(files)
        self.assertEqual(failures[0].reason, "LINK_HTML_RESPONSE")
        big = lambda *_: _Response(200, {"content-type": "application/pdf"}, b"%PDF-" + b"x" * 100)
        files, failures = download_links(msg, resolver=public_dns, requester=big,
                                         max_total_bytes=20)
        self.assertEqual(failures[0].reason, "LINK_SIZE_LIMIT")

    def test_explicit_ofd_links_are_ignored_before_network(self):
        msg = mail(plain="https://e.example/a.ofd https://e.example/get?jflx=ofd")
        def forbidden(*_args, **_kwargs):
            raise AssertionError("明确的 OFD 链接不应访问")
        files, failures = download_links(msg, resolver=forbidden, requester=forbidden)
        self.assertEqual(files, [])
        self.assertEqual(failures, [])

    def test_generic_link_returning_ofd_is_identified_for_later_ignore(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("OFD.xml", "<ofd/>")
        response = lambda *_: _Response(200, {"content-type": "application/ofd"}, out.getvalue())
        msg = mail(html='<a href="https://e.example/get">下载发票</a>')
        files, failures = download_links(msg, resolver=public_dns, requester=response)
        self.assertEqual(files[0].file_type, "OFD")
        self.assertEqual(failures, [])

    def test_one_failed_link_does_not_block_the_other(self):
        msg = mail(plain="https://e.example/a.pdf https://e.example/b.xml")
        def requester(url, *_):
            if url.endswith("a.pdf"):
                return _Response(403, {}, b"")
            return _Response(200, {"content-type": "application/xml"}, b"<EInvoice/>")
        files, failures = download_links(msg, resolver=public_dns, requester=requester)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].file_type, "XML")
        self.assertEqual(failures[0].reason, "LINK_HTTP_403")

    def test_all_dns_addresses_must_be_public(self):
        def mixed_dns(host, port, **kwargs):
            return [(0, 0, 0, "", ("8.8.8.8", port)),
                    (0, 0, 0, "", ("10.0.0.2", port))]
        with self.assertRaisesRegex(ValueError, "LINK_NONPUBLIC_TARGET"):
            fetch_link(LinkCandidate("https://e.example/a.pdf", "正文链接 e.example"),
                       resolver=mixed_dns)

    def test_redirect_budget_is_bounded(self):
        def requester(url, ip, budget):
            return _Response(302, {"location": "/a.pdf"}, b"")
        files, failures = download_links(mail(plain="https://e.example/a.pdf"),
                                         resolver=public_dns, requester=requester)
        self.assertFalse(files)
        self.assertEqual(failures[0].reason, "LINK_REDIRECT_LIMIT")


if __name__ == "__main__":
    unittest.main()
