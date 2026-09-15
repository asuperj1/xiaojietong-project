"""文档解析公共层（B16）：解析结果契约、编码探测、文本归一化。

设计原则（二阶段「科研深化 · 知识库」线）：
- **零硬依赖**：仅用标准库即可解析 Markdown / HTML / 纯文本；PDF 优先用
  `pypdf` / `pdfminer.six`（若环境已装），否则走内置极简抽取器
  （见 ``pdf_parser.py``），保证离线环境也能跑通。
- **统一产出**：所有解析器都返回 :class:`ParsedDoc`，便于 ``services/knowledge.py``
  与 B17 批量导入管道复用。
- **可解释**：``meta`` 带解析引擎、编码、页数等信息，``warnings`` 记录降级原因
  （例如「PDF 缺 ToUnicode，中文可能丢失」），管理端原样回显，便于排查。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 单篇文档正文上限（字符）：防止误传超大文件把 knowledge_doc(LONGTEXT) 撑爆，
# 也避免 embedding 阶段一次塞进过多分块。超出部分截断并记 warning。
MAX_CONTENT_CHARS = 200_000

# 支持的扩展名 → 解析器类型（``parser/__init__.py`` 注册表据此分发）
EXT_KINDS: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".pdf": "pdf",
    ".txt": "text",
    ".text": "text",
}


class ParseError(Exception):
    """解析失败（对外统一转 400/1001 契约错误或 CLI 的 FAILED 状态）。"""


@dataclass
class ParsedDoc:
    """解析结果（B16 各解析器统一产出）。

    Attributes:
        title: 文档标题（front-matter / h1 / 文件名回退）。
        content: 归一化后的纯文本正文。
        fmt: 解析器类型：``markdown`` / ``html`` / ``pdf`` / ``text``。
        meta: 解析元信息（engine/encoding/pages/bytes/truncated…）。
        warnings: 降级与可疑点（原样返回给调用方，不静默）。
    """

    title: str
    content: str
    fmt: str
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        """正文字符数。"""
        return len(self.content or "")

    @property
    def content_hash(self) -> str:
        """正文 SHA256（B17 断点续传/去重比对用）。"""
        return hashlib.sha256((self.content or "").encode("utf-8")).hexdigest()

    def summary(self) -> str:
        """单行摘要（进度打印用）。"""
        text = re.sub(r"\s+", " ", self.content or "").strip()
        return text[:80] + ("…" if len(text) > 80 else "")

    def to_dict(self, preview: int = 0) -> dict:
        """转可 JSON 序列化的字典；``preview`` > 0 时附带正文预览。"""
        data = {
            "title": self.title,
            "fmt": self.fmt,
            "chars": self.chars,
            "content_hash": self.content_hash[:16],
            "meta": self.meta,
            "warnings": self.warnings,
        }
        if preview > 0:
            data["preview"] = (self.content or "")[:preview]
        return data


# ---------------------------------------------------------------- 编码 ----

_HTML_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE
)

# 常见中文站点编码兜底顺序（utf-8 优先，其次 GB18030 兼容 gbk/gb2312）
# 注意 utf-8 必须排在 utf-8-sig 之前：后者会把普通 UTF-8 文件也标记为 utf-8-sig，
# 使 meta.encoding 失真（BOM 场景已由 detect_charset 提前返回 utf-8-sig）。
_FALLBACK_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "big5", "latin-1")


def detect_charset(data: bytes, declared: str = "") -> str:
    """探测字节串编码：显式声明 > HTML meta > BOM > 兜底链。"""
    if declared:
        return declared
    if data[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    m = _HTML_CHARSET_RE.search(data[:4096])
    if m:
        return m.group(1).decode("ascii", "ignore")
    return ""


def decode_bytes(data: bytes, declared: str = "") -> tuple[str, str, list[str]]:
    """把字节串解码为文本。

    Returns:
        (text, encoding, warnings)。全部候选失败时用 ``latin-1`` 兜底（不抛异常，
        但记 warning），保证批量导入不会因个别坏文件整体中断。
    """
    warnings: list[str] = []
    charset = detect_charset(data, declared)
    if charset:
        try:
            return data.decode(charset), charset, warnings
        except (LookupError, UnicodeDecodeError) as exc:
            warnings.append(f"声明编码 {charset} 解码失败（{exc}），已自动回退")
    for enc in _FALLBACK_ENCODINGS:
        try:
            return data.decode(enc), enc, warnings
        except (UnicodeDecodeError, LookupError):
            continue
    warnings.append("所有候选编码均失败，已用 latin-1 兜底（可能出现乱码）")
    return data.decode("latin-1", "replace"), "latin-1", warnings


# ------------------------------------------------------------ 文本归一化 ----

_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t\u00a0\u3000]{2,}")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str, *, keep_blank: bool = False) -> str:
    """归一化文本：BOM、换行、全角空格、连续空行、控制字符。

    Args:
        text: 原始文本。
        keep_blank: True 时保留单空行（Markdown 段落结构），False 时压掉所有空行。
    """
    if not text:
        return ""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\u00a0", " ")
    text = _CTRL_RE.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    if not keep_blank:
        text = "\n".join(line for line in text.split("\n") if line.strip())
    return text.strip()


def guess_title(content: str, fallback: str = "", limit: int = 80) -> str:
    """从正文首行猜标题（去掉常见 Markdown/编号前缀）。"""
    for line in (content or "").split("\n"):
        candidate = line.strip()
        if not candidate:
            continue
        candidate = re.sub(r"^#{1,6}\s*", "", candidate)
        candidate = re.sub(r"^[>\-*+\d.、\s]+", "", candidate).strip()
        if len(candidate) >= 2:
            return candidate[:limit]
    return (fallback or "未命名文档")[:limit]


def title_from_filename(name: str) -> str:
    """文件名（去扩展名、去下载序号）作为标题回退。"""
    stem = Path(name or "").stem
    stem = re.sub(r"^[\d\-_\s]+", "", stem)          # 去前缀序号
    stem = re.sub(r"[-_]+", " ", stem).strip()
    return stem[:80] or "未命名文档"


def truncate_content(content: str, limit: int = MAX_CONTENT_CHARS) -> tuple[str, bool]:
    """按上限截断正文，返回 (content, truncated)。"""
    if limit and len(content) > limit:
        return content[:limit], True
    return content, False


def finalize(
    content: str,
    *,
    title: str = "",
    fmt: str = "text",
    fallback_title: str = "",
    meta: Optional[dict] = None,
    warnings: Optional[list[str]] = None,
    keep_blank: bool = False,
    max_chars: int = MAX_CONTENT_CHARS,
) -> ParsedDoc:
    """各解析器的统一收尾：归一化 → 截断 → 补标题 → 组装 ParsedDoc。"""
    body = normalize_text(content, keep_blank=keep_blank)
    body, truncated = truncate_content(body, max_chars)
    meta = dict(meta or {})
    meta["content_chars"] = len(body)
    if truncated:
        meta["truncated"] = True
        meta["max_chars"] = max_chars
    warns = list(warnings or [])
    if truncated:
        warns.append(f"正文超过 {max_chars} 字上限，已截断（保留前 {max_chars} 字）")
    if not body.strip():
        warns.append("解析结果为空文本，请确认文件不是扫描件/图片版")
    return ParsedDoc(
        title=(title or guess_title(body, fallback_title)).strip()[:200],
        content=body,
        fmt=fmt,
        meta=meta,
        warnings=warns,
    )
