"""采集器的出网安全闸门（B27 review P1）—— 挡住 SSRF。

**为什么需要它**

`extract_list` 只对页面里的 `href` 做 `urljoin` 补全，**不校验主机**。于是列表页里
一条指向别的主机的链接，会让采集器真的去抓它 —— 而且**在抓之前**还会先替这个主机取
一次 `robots.txt`（`FetchGuard.before`），那本身就已经是一次打到内网的外发请求。

更糟的是：内网服务对 `/robots.txt` 通常返回 404，而 RFC 9309 规定 404 =
"没有 robots.txt = 无限制"，所以**连最保守的 `on_robots_error="block"` 都放行**。
云主机上的 `http://169.254.169.254/latest/meta-data/...` 走的正是这条路
—— 实例凭据会变成一条"校园通知"。

**挡什么**

1. 非 http/https 的 scheme（实测 `ftp://` 会真的发起连接、`javascript:` 会抛
   `unknown url type` 被记成错误）；
2. 指向私有 / 回环 / 链路本地 / 保留地址的主机（`10.x`、`192.168.x`、`127.0.0.1`、
   `169.254.169.254`、`::1`、`0.0.0.0` …）；
3. 主机名先按**字面量**判，不是 IP 字面量就**解析 DNS 再逐 IP 判** ——
   否则 `internal.example.com` → `10.0.0.1` 这种写法会漏掉。

**不挡什么**

跨域的**公网**主机仍然允许（有些学校把正文放在 `content.xxx.edu.cn`）——
强制"详情页必须与源站同源"会漏采合法正文。这与 robots 的口径一致：
只拦"明显不该去的地方"，不替运维做"只能同源"的决定。

判定顺序上，本模块**必须跑在 `FetchGuard.before` 之前**：先问 robots 等于先把请求
打到内网去，那时候再拦已经晚了。

**已知边界（不假装挡得住）**

本模块挡的是"配置或页面里**明显**指向内网/保留地址的目标"：IP 字面量、`localhost`、
`.internal` 这类名字，以及 DNS 解析结果落在内网的域名。它**挡不住 DNS rebinding**
（解析时返回公网 IP、连接时返回内网 IP）—— 那个要在**连接层**绑定 IP 校验才能根治，
属于比"出网闸门"更大的改造（应落在 `fetcher` 层），不能靠这里多写两行假装解决了。

同理，"解析失败"是**放行**的（见 `check_url`）：解析不出来就连接不上，
不放行只会把 DNS 故障误报成安全问题。
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

#: 允许的协议（其余一律拒绝，含 `ftp:` / `file:` / `javascript:` / `data:`）
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: 明确指向本机的主机名（与 `services/asr/http_remote.py` 的 `_LOOPBACK_HOSTS` 同口径）
LOOPBACK_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})

#: 一眼就是内网的主机名后缀
INTERNAL_SUFFIXES = (".local", ".internal", ".lan", ".home", ".corp", ".intranet")


def _as_ip(text: str) -> Optional[ipaddress._BaseAddress]:
    """把字面量解析成 IP 对象；不是 IP 就返回 None（**不猜**）。"""
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    """私有 / 回环 / 链路本地 / 保留 / 组播 / 未指定 —— 一律不给抓。"""
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _resolve(host: str) -> Iterable[str]:
    """解析主机名到 IP 列表；解析失败返回空（调用方按"拒绝"处理）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return ()
    return {str(info[4][0]) for info in infos}


def check_url(
    url: str, *, resolver: Optional[Callable[[str], Iterable[str]]] = None
) -> tuple[bool, str]:
    """这个 URL 能不能被抓 → `(是否允许, 不允许的原因)`。

    `resolver` 可注入，便于单测完全离线地覆盖"域名解析到内网"这类分支。
    """
    parts = urlsplit(url or "")
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return False, f"协议不允许：{scheme or '(空)'}（只允许 http/https）"

    host = (parts.hostname or "").lower()
    if not host:
        return False, "URL 没有主机名"
    if host in LOOPBACK_HOSTNAMES or host.endswith(INTERNAL_SUFFIXES):
        return False, f"主机指向内网：{host}"

    literal = _as_ip(host)
    if literal is not None:
        if _ip_is_blocked(literal):
            return False, f"主机是私有/回环/保留地址：{host}"
        return True, ""

    ips = list((resolver or _resolve)(host))
    if not ips:
        # ⚠️ 解析不出来时**放行**，理由有两层：
        # ① 解析失败 ⇒ 连接也必然失败，请求根本发不出去，不构成 SSRF；
        # ② 若在这里拒绝，普通的 DNS 故障 / 内网 DNS 环境会被误报成"安全问题"，
        #    把排查方向带偏（实测：虚构域名会被一律拦成"主机无法解析"，
        #    整条采集链路看起来像被安全策略封死，其实只是域名不存在）。
        #    交给 HTTP 层报"抓取失败"，错误信息才准确。
        return True, ""
    for text in ips:
        ip = _as_ip(str(text))
        if ip is None or _ip_is_blocked(ip):
            return False, f"主机解析到内网/保留地址：{host} -> {text}"
    return True, ""


def is_allowed(url: str, *, resolver: Optional[Callable[[str], Iterable[str]]] = None) -> bool:
    """`check_url` 的布尔便捷入口。"""
    return check_url(url, resolver=resolver)[0]
