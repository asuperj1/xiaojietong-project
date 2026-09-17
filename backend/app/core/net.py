"""出站 HTTP 的网络环境适配（C30/C36 共用的坑）。

**回环地址必须绕过代理**：`httpx` 默认 `trust_env=True`，除了读 `HTTP_PROXY` 等环境变量，
在 Windows 上还会读到**注册表里的系统代理**（`urllib.request.getproxies()` 的返回值）。
后果是发往 `http://127.0.0.1:11434/api/chat` 的请求被**代理**接走并回 502 ——
本机的 Ollama 明明活着，调用方却只看到"模型服务返回 502"。

本仓库的推理服务（Ollama / 自建识别服务 / 自建抽取服务）默认都跑在本机或内网回环上，
且"网络受限"的机器恰恰多半配着代理，所以这条不是边角情况。

非回环地址**保持 httpx 默认行为**：需要经代理访问外部模型服务的部署不能被一刀切关掉。

用法：

```python
async with httpx.AsyncClient(timeout=30.0, **proxy_bypass_kwargs(url)) as client:
    ...
```

> 注：`app/services/asr/http_remote.py` 里有一份同源的私有实现（`_is_loopback`）。
> 它已随 PR #115 提交，为避免两处同时改同一段代码产生冲突，**待 #115 合并后**再改为
> 引用本模块 —— 届时本模块即为唯一出处。
"""
from __future__ import annotations

import urllib.parse

#: 视为"本机"的主机名。回环地址永远不需要代理。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def is_loopback(url: str) -> bool:
    """URL 是否指向本机（含 `127.0.0.0/8` 任意地址）。非 http(s) 或解析不出主机名返回 False。"""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def proxy_bypass_kwargs(url: str) -> dict:
    """给 `httpx.Client/AsyncClient` 用的代理绕过参数：回环地址才返回 `{"trust_env": False}`。"""
    return {"trust_env": False} if is_loopback(url) else {}
