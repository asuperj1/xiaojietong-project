# -*- coding: utf-8 -*-
"""B32 语音转文字 —— 魔数/时长/后端协议/错误码。

分两层：

- **纯离线**：音频体检（魔数、WAV 时长）与 HttpRemoteBackend 的协议行为
  （注入假 post，不真的联网）；
- **走路由**：需要真实 DB 取用户，沿用 conftest 的 `client` 夹具
  （环境不可用时自动 skip，不会假装通过）。

核心验收：**失败必须给明确错误码**，不许回空字符串冒充成功。
"""
from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import asr
from app.services.asr import AsrFailure, AsrResult, AsrUnavailable, NullBackend, sniff_audio
from app.services.asr.http_remote import HttpRemoteBackend
from app.services.asr.probe import make_wav, wav_duration_seconds

URL = "http://asr.internal:9000/transcribe"


@pytest.fixture(autouse=True)
def _reset_backend():
    """每个用例前后都还原后端缓存，避免互相污染。"""
    asr.reset_backend()
    yield
    asr.reset_backend()


# ------------------------------------------------------------ 音频体检 ----


@pytest.mark.parametrize("head,expected", [
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", ".wav"),
    (b"ID3\x04\x00\x00", ".mp3"),
    (b"\xff\xfb\x90\x00", ".mp3"),
    (b"\x00\x00\x00\x20ftypM4A ", ".m4a"),
    (b"OggS\x00\x02", ".ogg"),
    (b"#!AMR\x0a", ".amr"),
    (b"\x1a\x45\xdf\xa3\x01\x00", ".webm"),
])
def test_sniff_accepts_supported_audio(head, expected):
    probed = sniff_audio(head)
    assert probed is not None and probed[0] == expected


@pytest.mark.parametrize("head", [
    b"", b"not-audio", b"<html><body>", b"\x89PNG\r\n\x1a\n", b"%PDF-1.7",
])
def test_sniff_rejects_non_audio(head):
    """伪装成音频的任意文件必须被挡下（不信 Content-Type）。"""
    assert sniff_audio(head) is None


def test_wav_duration_is_exact():
    data = make_wav(4.0)
    assert wav_duration_seconds(data) == pytest.approx(4.0, abs=0.01)


def test_wav_duration_returns_none_for_other_formats():
    assert wav_duration_seconds(b"ID3\x04\x00\x00" + b"\x00" * 100) is None
    assert wav_duration_seconds(b"") is None


def test_wav_duration_returns_none_for_truncated_wav():
    data = make_wav(3.0)[:20]                    # 只有头，没有数据块
    assert wav_duration_seconds(data) is None


# ------------------------------------------------- HttpRemoteBackend ----


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload, ensure_ascii=False) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _fake_post(response=None, exc=None):
    calls: list[dict] = []

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if exc is not None:
            raise exc
        return response

    _post.calls = calls  # type: ignore[attr-defined]
    return _post


def test_http_backend_unavailable_without_url():
    ok, why = HttpRemoteBackend("").availability()
    assert ok is False and "XJT_ASR_HTTP_URL" in why


def test_http_backend_rejects_non_http_url():
    ok, why = HttpRemoteBackend("ftp://x/asr").availability()
    assert ok is False and "http" in why


def test_http_backend_success_parses_contract():
    post = _fake_post(FakeResponse(200, {"text": "图书馆几点关门", "language": "zh", "duration_ms": 4200}))
    backend = HttpRemoteBackend(URL, post=post)
    result = backend.transcribe(make_wav(4), filename="a.wav", language="zh")
    assert result.text == "图书馆几点关门"
    assert result.duration_ms == 4200
    assert result.backend == "http"
    assert post.calls[0]["url"] == URL


def test_http_backend_5xx_is_failure_not_unavailable():
    """上游报错 = 转写失败（5003），不是服务不可用（5002）——两者要分得清。"""
    backend = HttpRemoteBackend(URL, post=_fake_post(FakeResponse(503, text="busy")))
    with pytest.raises(AsrFailure):
        backend.transcribe(b"RIFF")


def test_http_backend_connect_error_is_unavailable():
    backend = HttpRemoteBackend(URL, post=_fake_post(exc=OSError("connection refused")))
    with pytest.raises(AsrUnavailable):
        backend.transcribe(b"RIFF")


def test_http_backend_missing_text_field_is_failure():
    backend = HttpRemoteBackend(URL, post=_fake_post(FakeResponse(200, {"result": "x"})))
    with pytest.raises(AsrFailure) as exc:
        backend.transcribe(b"RIFF")
    assert "text" in str(exc.value)


def test_http_backend_non_json_is_failure():
    backend = HttpRemoteBackend(URL, post=_fake_post(FakeResponse(200, None, text="<html>")))
    with pytest.raises(AsrFailure):
        backend.transcribe(b"RIFF")


# ---------------------------------------------------------- 路由层 ----


class FakeBackend:
    """受控后端：想成功就成功，想失败就按类型抛。"""

    name = "fake"

    def __init__(self, text: str = "今天图书馆几点关门", *, available: bool = True,
                 why: str = "", fail: str = "") -> None:
        self.text = text
        self.available = available
        self.why = why
        self.fail = fail

    def availability(self):
        return (self.available, self.why)

    def transcribe(self, audio, *, filename="", language="zh", prompt=""):
        if self.fail == "unavailable":
            raise AsrUnavailable("连不上 ASR 服务")
        if self.fail == "failure":
            raise AsrFailure("上游返回 500")
        return AsrResult(text=self.text, language=language, duration_ms=4200, backend=self.name)


def _post(client, hdr, data: bytes, *, name: str = "a.wav", ctype: str = "audio/wav", **form):
    return client.post(
        "/api/v1/voice/transcribe",
        headers=hdr,
        files={"file": (name, data, ctype)},
        data=form or {"language": "zh"},
    )


def test_route_requires_auth(client):
    resp = client.post("/api/v1/voice/transcribe",
                       files={"file": ("a.wav", make_wav(4), "audio/wav")})
    assert resp.json()["code"] == 2001


def test_route_rejects_unknown_format(client, hdr_a):
    resp = _post(client, hdr_a, b"<html>not audio</html>", name="x.html", ctype="text/html")
    body = resp.json()
    assert body["code"] == 1001 and "格式" in body["message"]


def test_route_rejects_oversized_file(client, hdr_a, monkeypatch):
    monkeypatch.setattr(settings, "asr_max_bytes", 1024)
    resp = _post(client, hdr_a, make_wav(1)[:4096] or b"\x00" * 4096)
    body = resp.json()
    assert body["code"] == 1001 and "超过" in body["message"]


def test_route_rejects_too_short_wav(client, hdr_a):
    resp = _post(client, hdr_a, make_wav(1.5))
    body = resp.json()
    assert body["code"] == 1001 and "太短" in body["message"]


def test_route_rejects_too_long_wav(client, hdr_a):
    resp = _post(client, hdr_a, make_wav(12))
    body = resp.json()
    assert body["code"] == 1001 and "太长" in body["message"]


def test_route_without_backend_returns_5002(client, hdr_a):
    """未配置后端 → 5002 且文案说清该配什么；**绝不是 code=0 + 空文本**。"""
    asr.set_backend(NullBackend())
    resp = _post(client, hdr_a, make_wav(5))
    body = resp.json()
    assert body["code"] == 5002
    assert "不可用" in body["message"] and "XJT_ASR_BACKEND" in body["message"]


def test_route_success_with_backend(client, hdr_a):
    asr.set_backend(FakeBackend())
    resp = _post(client, hdr_a, make_wav(5))
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["text"] == "今天图书馆几点关门"
    assert body["data"]["duration_ms"] == 4200
    assert body["data"]["format"] == "wav"
    assert body["data"]["backend"] == "fake"


def test_route_backend_unavailable_midcall_is_5002(client, hdr_a):
    asr.set_backend(FakeBackend(available=False, why="未配置 XJT_ASR_HTTP_URL"))
    body = _post(client, hdr_a, make_wav(5)).json()
    assert body["code"] == 5002


def test_route_backend_failure_is_5003(client, hdr_a):
    """后端可用但本次转写失败 → 5003（与 5002 区分开）。"""
    asr.set_backend(FakeBackend(fail="failure"))
    body = _post(client, hdr_a, make_wav(5)).json()
    assert body["code"] == 5003 and "失败" in body["message"]


def test_route_backend_raises_unavailable_is_5002(client, hdr_a):
    asr.set_backend(FakeBackend(fail="unavailable"))
    body = _post(client, hdr_a, make_wav(5)).json()
    assert body["code"] == 5002


def test_get_backend_follows_config(client, monkeypatch):
    """配置驱动：none → NullBackend；http → HttpRemoteBackend。"""
    monkeypatch.setattr(settings, "asr_backend", "none")
    assert isinstance(asr.get_backend(), NullBackend)

    asr.reset_backend()
    monkeypatch.setattr(settings, "asr_backend", "http")
    monkeypatch.setattr(settings, "asr_http_url", URL)
    backend = asr.get_backend()
    assert isinstance(backend, HttpRemoteBackend) and backend.url == URL
