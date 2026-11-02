"""
网页接口测试 / Web interface tests.

接口按一个面向外部的服务来写：非法输入要有明确的错误码，而不是让请求挂住；
页面带 CSP；服务端返回的每一个字段都能被 JSON 序列化。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from arbiter.dialogue.server import MAX_BODY, Handler


@pytest.fixture(scope="module")
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def post(base: str, path: str, body: bytes, ctype: str = "application/json"):
    req = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def chat(base: str, **payload):
    return post(base, "/api/chat", json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def test_the_page_is_served_with_a_content_security_policy(base):
    with urllib.request.urlopen(base + "/", timeout=10) as res:
        assert res.status == 200
        assert "default-src 'none'" in res.headers["Content-Security-Policy"]
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        page = res.read().decode("utf-8")
    # 页面不加载任何外部资源 / no external resources
    assert "http://" not in page.replace("http://127.0.0.1", "") and "https://" not in page
    # 服务端文字只用 textContent 写入 / server text goes through textContent only
    assert ".innerHTML" not in page


def test_malformed_json_is_a_400(base):
    assert post(base, "/api/chat", b"{not json")[0] == 400
    assert post(base, "/api/chat", b"[1,2,3]")[0] == 400


def test_oversized_bodies_are_refused(base):
    status, _ = post(base, "/api/chat", b"{" + b" " * (MAX_BODY + 10) + b"}")
    assert status == 413


def test_invalid_session_ids_are_refused(base):
    assert chat(base, session="../../etc/passwd", text="你好")[0] == 400


def test_a_full_turn_round_trips(base):
    status, data = chat(base, session="t1", text="给房东转4500", lang="zh")
    assert status == 200 and data["reply"]["kind"] == "envelope"
    digest = data["reply"]["detail"]["digest"]
    assert data["reply"]["detail"]["voucher"]["digest"] == digest
    status, data = chat(base, session="t1", text="确认", digest=digest)
    assert status == 200 and data["reply"]["kind"] == "result"
    assert data["kernel"]["verdict"] == "allow" and data["kernel"]["chain_ok"]


def test_a_malformed_digest_counts_as_a_stale_button(base):
    chat(base, session="t2", text="给房东转4500")
    status, data = chat(base, session="t2", text="确认", digest="not-a-digest")
    assert status == 200 and data["reply"]["kind"] != "result"


def test_script_payloads_come_back_as_plain_text(base):
    status, data = chat(base, session="t3", text="<img src=x onerror=alert(1)>")
    assert status == 200 and data["reply"]["text"]


def test_reset_returns_a_greeting_in_the_requested_language(base):
    status, data = post(base, "/api/reset", json.dumps({"session": "t4", "lang": "en"}).encode())
    assert status == 200 and data["greeting"].startswith("Hello")
