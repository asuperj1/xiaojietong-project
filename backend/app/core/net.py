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

判据（这里踩过两次，别再手写前缀匹配）：

- IP 字面量用 `ipaddress.ip_address(host).is_loopback`，**不要**写 `host.startswith("127.")` ——
  那样 `127.evil.com`（合法域名）会被误判成本机，本该走代理的**远程**主机被静默绕过代理；
- `0.0.0.0` / `::` 是 **bind（监听）** 地址，不是回环地址，**不算本机**。

> 注：`app/services/asr/http_remote.py` 在 PR #115（C30，**尚未合并**）里引入了同源的私有实现
> `_is_loopback`（口径与本模块一致）。#115 合并后应改为 `from app.core.net import is_loopback`，
> 本模块即为唯一出处。
"""
from __future__ import annotations

import ipaddress
import urllib.parse

#: 非 IP 字面量的"本机"主机名（RFC 6761 保留名 + IPv6 回环字面量的可读别名）。
_LOOPBACK_HOSTS = frozenset({"localhost", "::1"})


def is_loopback(url: str) -> bool:
    """URL 是否指向本机（含 `127.0.0.0/8` 任意地址与 IPv6 `::1`）。

    ⚠️ `0.0.0.0` / `::` **不算**：它们是"监听全部网卡"的 bind 地址，不是回环地址。
    非 http(s)、解析不出主机名、或主机名既不是 IP 也不是 `localhost` → False。
    """
    host = (urllib.parse.urlsplit(url or "").hostname or "").strip().lower()
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:      # 域名：`127.evil.com` 这种前缀巧合不是本机
        return False


def proxy_bypass_kwargs(url: str) -> dict:
    """给 `httpx.Client/AsyncClient` 用的代理绕过参数：回环地址才返回 `{"trust_env": False}`。"""
    return {"trust_env": False} if is_loopback(url) else {}
