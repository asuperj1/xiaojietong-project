# -*- coding: utf-8 -*-
"""C30 本地 Whisper HTTP 服务端 —— 契约 / 鉴权 / 限额 / 错误码。

分两层，**都不需要 DB、不需要装 whisper**：

- **离线**：`TestClient` + 注入假后端，覆盖鉴权、大小、格式、时长、错误码；
- **真回环**：真起 uvicorn（随机端口），用**真的** `HttpRemoteBackend` +
  真的 `httpx.post` 打过去 —— 这是唯一能证明"客户端与本文档描述的服务端契约
  确实对得上"的测试（字段名、表单字段、响应键、状态码全链路真过一遍）。

⚠️ **反向对照**（本仓库反复踩过的坑："验证工具自己也要是对的"）：
回环测试必须能**在契约被破坏时失败**，否则它就是"看绿灯的仪式"。因此额外加两条
反向用例 —— 字段名换成 `audio`、响应里不含 `text` —— **断言它们失败**。
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time

import httpx
import pytest
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.services.asr import AsrFailure, AsrResult, AsrUnavailable
from app.services.asr.http_remote import HttpRemoteBackend
from app.services.asr.probe import make_wav
from app.services.asr.server import _TooLarge, _read_limited, create_app

ENDPOINT = "/transcribe"

# 真 WAV，避免被魔数检查拦下（4 秒静音；测试里不关心内容）
WAV = make_wav(4.0)


class FakeBackend:
    """受控后端：想成功就成功，想失败按类型抛，并记录收到的参数。"""

    name = "fake"

    def __init__(self, text: str = "今天图书馆几点关门", *, available: bool = True,
                 why: str = "", fail: str = "") -> None:
        self.text = text
        self.available = available
        self.why = why
        self.fail = fail
        self.calls: list[dict] = []

    def availability(self):
        return (self.available, self.why)

    def transcribe(self, audio, *, filename="", language="zh", prompt=""):
        self.calls.append({"size": len(audio), "filename": filename,
                           "language": language, "prompt": prompt})
        if self.fail == "unavailable":
            raise AsrUnavailable("faster-whisper 未安装")
        if self.fail == "failure":
            raise AsrFailure("音频解码失败")
        return AsrResult(text=self.text, language=language, duration_ms=4200,
                         backend="fake(engine)")


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def client(backend) -> TestClient:
    return TestClient(create_app(backend=backend))


def _upload(client: TestClient, data: bytes = WAV, *, name: str = "a.wav",
            ctype: str = "audio/wav", **form):
    return client.post(ENDPOINT, files={"file": (name, data, ctype)},
                       data=form or {"language": "zh"})


# ============================================================ 正常路径 ====


def test_transcribe_returns_contract_fields(client):
    """响应必须含 text / language / duration_ms —— 客户端只认这三个。"""
    body = _upload(client).json()
    assert body["text"] == "今天图书馆几点关门"
    assert body["language"] == "zh"
    assert body["duration_ms"] == 4200


def test_language_and_prompt_are_forwarded(client, backend):
    _upload(client, language="en", prompt="校捷通 图书馆")
    assert backend.calls[0]["language"] == "en"
    assert backend.calls[0]["prompt"] == "校捷通 图书馆"


def test_filename_ext_is_passed_by_magic_not_content_type(client, backend):
    """扩展名取自**文件头**：Content-Type 谎报也不影响传给 whisper 的后缀。"""
    _upload(client, name="voice.dat", ctype="application/octet-stream")
    assert backend.calls[0]["filename"] == "audio.wav"


def test_empty_text_is_success_not_error(client, backend):
    """后端真的没听出内容 → 200 + 空 text（**不是** 500，也不假装识别成功）。"""
    backend.text = ""
    resp = _upload(client)
    assert resp.status_code == 200 and resp.json()["text"] == ""


# ============================================================ 输入校验 ====


def test_missing_file_field_is_rejected(client):
    """缺 file 字段 → 422（校验层拦下，不落到后端）。"""
    assert client.post(ENDPOINT, data={"language": "zh"}).status_code == 422


def test_empty_audio_is_rejected(client, backend):
    resp = _upload(client, b"")
    assert resp.status_code == 400 and "空" in resp.json()["detail"]
    assert backend.calls == []                     # 没到后端


def test_non_audio_is_rejected_with_supported_hint(client, backend):
    """伪装成音频的 HTML 必须按**魔数**挡下，并告知支持哪些格式。"""
    resp = _upload(client, b"<html>not audio</html>", name="x.wav", ctype="audio/wav")
    assert resp.status_code == 400
    assert "wav" in resp.json()["detail"]
    assert backend.calls == []


def test_oversized_upload_is_rejected_without_transcribing():
    """超限 → 413；且**不能**把整包读进内存后才判断（分片读 + 提前看 size）。"""
    backend = FakeBackend()
    c = TestClient(create_app(backend=backend, max_bytes=1024))
    resp = _upload(c, WAV * 10)                    # 约 640KB，远超 1KB
    assert resp.status_code == 413
    assert backend.calls == []


def test_overlong_wav_is_rejected():
    """WAV 时长可精确计算 → 超限直接挡下（挡住"传一整个小时录音"）。"""
    backend = FakeBackend()
    c = TestClient(create_app(backend=backend, max_seconds=2.0))
    resp = _upload(c, make_wav(5.0))
    assert resp.status_code == 413 and backend.calls == []


# ============================================================ 错误分类 ====


def test_backend_unavailable_maps_to_503():
    """后端不可用（没装 whisper / 模型加载失败）→ 503，而不是 200 空结果。"""
    c = TestClient(create_app(backend=FakeBackend(fail="unavailable")))
    resp = _upload(c)
    assert resp.status_code == 503 and "不可用" in resp.json()["detail"]


def test_backend_failure_maps_to_500():
    """本次转写失败（音频损坏）→ 500。**与 503 分开**，便于运维定位。"""
    c = TestClient(create_app(backend=FakeBackend(fail="failure")))
    resp = _upload(c)
    assert resp.status_code == 500 and "转写失败" in resp.json()["detail"]


# ============================================================ 鉴权 ====


def test_token_disabled_by_default(client):
    """未配 token 时不做鉴权（内网自建服务的常见用法）。"""
    assert _upload(client).status_code == 200


@pytest.mark.parametrize("hdr,expected", [
    ("", 401),                                     # 完全没带
    ("Bearer wrong", 401),                         # 带错了
    ("Bearer s3cret", 200),                        # 正确
])
def test_token_enforced_when_configured(hdr, expected):
    c = TestClient(create_app(backend=FakeBackend(), token="s3cret"))
    resp = c.post(ENDPOINT, files={"file": ("a.wav", WAV, "audio/wav")},
                  data={"language": "zh"}, headers={"Authorization": hdr} if hdr else {})
    assert resp.status_code == expected


# ============================================================ 健康检查 ====


def test_health_ok_when_backend_available():
    c = TestClient(create_app(backend=FakeBackend()))
    resp = c.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["backend"] == "fake"


def test_health_reports_reason_when_unavailable():
    """不可用要**说明原因**，运维据此知道该装什么/改哪个配置项。"""
    c = TestClient(create_app(backend=FakeBackend(available=False, why="faster-whisper 未安装")))
    resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False and "未安装" in resp.json()["detail"]


# ==================================================== 真回环（契约） ====


@contextlib.contextmanager
def _live_server(app: FastAPI):
    """把 ASGI app 真起成一个 HTTP 服务（随机空闲端口），退出时干净关掉。"""
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn 未能在 20s 内启动")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_real_round_trip_client_against_server():
    """**核心验收**：真的 HTTP 往返 —— 真的 `HttpRemoteBackend`（真的 httpx.post）
    打到真的 uvicorn 上。字段名、表单字段、响应键任何一处不匹配，本用例就会红。
    """
    backend = FakeBackend(text="图书馆今天开到九点")
    with _live_server(create_app(backend=backend)) as base:
        client = HttpRemoteBackend(f"{base}{ENDPOINT}", timeout=5.0)
        result = client.transcribe(WAV, filename="a.wav", language="zh", prompt="校捷通")
        assert result.text == "图书馆今天开到九点"
        assert result.language == "zh"
        assert result.duration_ms == 4200
        assert result.backend == "http"
        assert result.raw["engine"] == "fake(engine)"      # 额外字段也回来了
        assert backend.calls[0]["prompt"] == "校捷通"        # form 字段真的到了


def test_real_round_trip_with_token():
    """配了 token 时，主应用侧 `XJT_ASR_HTTP_API_KEY` 必须能通过。"""
    backend = FakeBackend(text="ok")
    app = create_app(backend=backend, token="s3cret")
    with _live_server(app) as base:
        client = HttpRemoteBackend(f"{base}{ENDPOINT}", api_key="s3cret", timeout=5.0)
        assert client.transcribe(WAV, filename="a.wav").text == "ok"


def test_real_round_trip_wrong_token_is_failure():
    """token 错了 → 401 → 客户端归为**转写失败 5003**（不是 5002 不可用）。"""
    app = create_app(backend=FakeBackend(), token="s3cret")
    with _live_server(app) as base:
        client = HttpRemoteBackend(f"{base}{ENDPOINT}", api_key="wrong", timeout=5.0)
        with pytest.raises(AsrFailure):
            client.transcribe(WAV, filename="a.wav")


def test_reverse_control_wrong_field_name_fails():
    """**反向对照 1**：字段名不是 `file` 就必须失败。

    证明上一条回环用例不是"看绿灯的仪式"—— 服务端确实在按 `file` 取文件。
    这里复用客户端自带的 `field_name` 开关把字段名改成 `audio`，
    服务端认不出来 → 422 → 客户端报 `AsrFailure`。
    """
    backend = FakeBackend()
    with _live_server(create_app(backend=backend)) as base:
        client = HttpRemoteBackend(f"{base}{ENDPOINT}", field_name="audio", timeout=5.0)
        with pytest.raises(AsrFailure):
            client.transcribe(WAV, filename="a.wav")
        assert backend.calls == []                 # 确实没进到后端


def test_reverse_control_response_without_text_fails():
    """**反向对照 2**：响应里缺 `text` 就必须失败。

    证明回环用例的断言确实在检查契约字段，而不是"只要 200 就算过"。
    """
    app = FastAPI()

    @app.post("/transcribe")
    async def _wrong(file: UploadFile = File(...)):  # noqa: A002 - 故意模仿服务端签名
        return JSONResponse({"result": "图书馆几点关门"})   # 键名写错

    with _live_server(app) as base:
        client = HttpRemoteBackend(f"{base}{ENDPOINT}", timeout=5.0)
        with pytest.raises(AsrFailure) as exc:
            client.transcribe(WAV, filename="a.wav")
        assert "text" in str(exc.value)


def test_reverse_control_search_route_is_not_the_contract():
    """**反向对照 3**：路径写错（打不到 /transcribe）也必须失败，而不是静默成功。"""
    app = create_app(backend=FakeBackend())
    with _live_server(app) as base:
        client = HttpRemoteBackend(f"{base}/wrong-path", timeout=5.0)
        with pytest.raises(AsrFailure):
            client.transcribe(WAV, filename="a.wav")


def test_client_sees_connect_error_as_unavailable():
    """服务没起 → 客户端的"服务不可用"路径（对应路由 5002）。

    用一个**已关闭**的端口，模拟"识别服务挂了 / 地址填错"。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]                  # 拿到就关，端口必然连不上
    client = HttpRemoteBackend(f"http://127.0.0.1:{port}{ENDPOINT}", timeout=1.0)
    with pytest.raises(AsrUnavailable):
        client.transcribe(WAV, filename="a.wav")


# ============================================== 与主应用配置的贯通 ====


def test_backend_can_be_pointed_at_this_server(monkeypatch):
    """主应用按 `XJT_ASR_BACKEND=http` + URL 就能挑中本服务的契约。

    这里只验证**配置→后端选择**这一段（真起 uvicorn 见上面的回环用例）。
    """
    from app.core.config import settings
    from app.services import asr

    monkeypatch.setattr(settings, "asr_backend", "http")
    monkeypatch.setattr(settings, "asr_http_url", "http://127.0.0.1:9001/transcribe")
    monkeypatch.setattr(settings, "asr_http_api_key", "k")
    asr.reset_backend()
    try:
        chosen = asr.get_backend()
        assert chosen.name == "http"
        assert chosen.url == "http://127.0.0.1:9001/transcribe"
        assert chosen.api_key == "k"
        assert chosen.availability()[0] is True
    finally:
        asr.reset_backend()


# =============================== 回环地址绕过代理（C30 实机踩到的坑）====
#
# 现象：识别服务明明在本机活着，主应用却报 5003「转写失败」，错误文案是
#      「ASR 服务返回 HTTP 502」。
# 根因：`httpx` 默认 `trust_env=True`，会把 **Windows 注册表里的系统代理**
#      （`urllib.request.getproxies()`）也应用到 `http://127.0.0.1:9001` 上，
#      代理接走请求并回 502。
# 为何必须修而不是写进文档让运维设 `NO_PROXY`：本服务的用途就是"网络受限时
#      兜底"，而"网络受限"的机器**恰恰**都配着代理。


class FakeResponse:
    """最小 httpx.Response 替身（只用到客户端真正读的几个属性）。"""

    def __init__(self, status_code: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _spy_post(response=None):
    """记录 kwargs 的假 post（用来断言"传了什么"）。"""
    calls: list[dict] = []

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    _post.calls = calls  # type: ignore[attr-defined]
    return _post


def test_is_loopback_truth_table():
    from app.services.asr.http_remote import _is_loopback

    for url in ("http://127.0.0.1:9001/transcribe", "http://127.0.0.5:1/x",
                "http://localhost:9001/x", "http://[::1]:9001/x"):
        assert _is_loopback(url) is True, url
    for url in ("http://asr.internal:9000/x", "https://asr.example.com/v1",
                "http://10.0.0.7:9000/x", "http://172.217.1.1/x"):
        assert _is_loopback(url) is False, url


def test_loopback_url_asks_httpx_to_ignore_env_proxy():
    post = _spy_post(FakeResponse(200, {"text": "ok"}))
    HttpRemoteBackend("http://127.0.0.1:9001/transcribe", post=post).transcribe(WAV)
    assert post.calls[0]["trust_env"] is False


def test_non_loopback_url_keeps_default_proxy_behaviour():
    """**反向对照**：内网/云端地址**不能**一刀切关掉代理 ——
    否则"必须经代理访问外部识别服务"的部署会直接连不上。"""
    post = _spy_post(FakeResponse(200, {"text": "ok"}))
    HttpRemoteBackend("https://asr.example.com/v1", post=post).transcribe(WAV)
    assert "trust_env" not in post.calls[0]


def test_loopback_bypass_is_effective_against_a_real_proxy(monkeypatch):
    """端到端证据：给 httpx 一个真实生效的代理，回环地址仍能直达。

    与上一条"只看 kwargs"的区别：这条真起了服务、真发了 HTTP —— 若客户端
    没绕过代理，请求会打到 `127.0.0.1:1` 上，必然失败。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")     # 必然连不上
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    with _live_server(create_app(backend=FakeBackend(text="离线也能识别"))) as base:
        result = HttpRemoteBackend(f"{base}{ENDPOINT}", timeout=5.0).transcribe(
            WAV, filename="a.wav")
    assert result.text == "离线也能识别"

# ============================== 分片读的累加上限（C30 加固）==============
#
# 为什么要单独补这一节：对 `server.py` 做变异测试（把 `_read_limited` 里的
# `if total > limit:` 改成 `if False:`）后，**本文件仍然 29 项全绿** —— 说明原先
# 只覆盖了 `Content-Length` 预检那条早退分支（`declared > limit`），而
# **分片累加这道守卫没有任何用例**。
#
# 而分片累加正是防「**谎报或不报 `Content-Length` 的客户端**」的那一道：
# 删掉它不会有任何测试变红，保护会静默消失 —— 这正是 PR #60 审查里
# 「先 `read()` 整包再判大小」那个坑的正面防线。
#
# 为什么用桩而不是端到端：`TestClient`/httpx 发 multipart 时一定会带上
# 正确的 `Content-Length`，走不到累加分支；要打中它必须绕过 `Content-Length`，
# 故直接对 `_read_limited` 做单元级测试（它是模块级函数，可独立调用）。


class _FakeUpload:
    """最小 `UploadFile` 桩：只实现 `read`/`size`，并记录读了几次。"""

    def __init__(self, chunks, size=None):
        self._chunks = list(chunks)
        self.size = size
        self.reads = 0

    async def read(self, n=-1):
        self.reads += 1
        return self._chunks.pop(0) if self._chunks else b""


def test_read_limited_rejects_when_declared_size_exceeds_limit():
    """（a）`Content-Length` 已超限 → 立刻拒绝，且**一个分片都不读**。"""
    up = _FakeUpload([b"x" * 10], size=999)
    with pytest.raises(_TooLarge):
        asyncio.run(_read_limited(up, 100))
    assert up.reads == 0, "已由 size 判定超限，不应再读内容"


def test_read_limited_rejects_oversized_stream_ignoring_declared_size():
    """（b）**谎报 `size` 的客户端**：声明 1 字节、实际超上限 → 仍须拒绝。

    这是删掉分片累加守卫后会静默失效的那条路径（原先无任何用例覆盖）。
    """
    up = _FakeUpload([b"a" * 100, b"b" * 51], size=1)
    with pytest.raises(_TooLarge):
        asyncio.run(_read_limited(up, 150))


def test_read_limited_accepts_exactly_at_limit():
    """边界反向对照：**恰好等于**上限必须放行，不是「宁严不宽」。"""
    up = _FakeUpload([b"a" * 100, b"b" * 50], size=None)
    assert len(asyncio.run(_read_limited(up, 150))) == 150


def test_read_limited_rejects_one_byte_over_limit_without_content_length():
    """无 `size` 时 **超 1 字节**也必须拒绝（上限是「≤」而不是「<」或「明显超过」）。"""
    up = _FakeUpload([b"a" * 150, b"b"], size=None)
    with pytest.raises(_TooLarge):
        asyncio.run(_read_limited(up, 150))

