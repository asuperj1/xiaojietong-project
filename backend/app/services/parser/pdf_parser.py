"""PDF 解析器（B16）：三层引擎，环境无第三方库也能抽文本。

**引擎优先级**（逐层降级，任一层抽到文本即返回）：

1. ``pypdf``（若已安装）——支持压缩对象流（ObjStm）、加密空口令解密，最稳；
2. ``pdfminer.six``（若已安装）——版式还原好；
3. **内置极简抽取器**（纯标准库，本文件实现）——直接解析 PDF 对象与内容流：
   支持 ``FlateDecode`` / ``ASCIIHexDecode`` / ``ASCII85Decode`` / ``RunLengthDecode``
   过滤器、``Tj/TJ/'/"`` 文本算子，并通过 ``/ToUnicode`` CMap 还原中文
   （Word/Chrome 导出的 PDF 普遍带此映射）。

**能力边界（会写入 warnings，不静默失败）**：
- 扫描件/图片版 PDF（无文本层）→ 抽不到文本，上报「疑似扫描件，需 OCR」；
- 缺 ``/ToUnicode`` 的 CID 字体 → 中文可能丢失，建议装 ``pypdf``；
- Object Stream 压缩对象（PDF 1.5+ 的 ``/ObjStm``）内置层不展开 → 会自动提示。

安装建议（可选，不影响运行）：``pip install pypdf``
"""

from __future__ import annotations

import re
import zlib
from typing import Optional

from .base import ParseError, ParsedDoc, finalize, guess_title, title_from_filename

_MAX_STREAM_BYTES = 64 * 1024 * 1024   # 单个流解压上限（防 zip bomb）
_MAX_STREAMS = 512                     # 单文档处理的内容流上限
_OBJ_RE = re.compile(rb"(?m)(\d+)\s+(\d+)\s+obj\b")
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![sA-Za-z])")
_PAGE_FOOTER_RE = re.compile(
    r"^(第\s*\d+\s*页(\s*/\s*共?\s*\d+\s*页)?|Page\s+\d+(\s+of\s+\d+)?|\d{1,3}|[-—]\s*\d{1,3}\s*[-—])$",
    re.IGNORECASE,
)


# ============================================================ 对外入口 ====

def parse_bytes(data: bytes, name: str = "") -> ParsedDoc:
    """解析 PDF 字节串（三层引擎依次尝试）。"""
    if not data[:5].startswith(b"%PDF"):
        raise ParseError("不是有效的 PDF 文件（缺少 %PDF- 文件头）")

    attempts: list[dict] = []
    for engine_name, func in (
        ("pypdf", _extract_with_pypdf),
        ("pdfminer", _extract_with_pdfminer),
        ("builtin", _extract_with_builtin),
    ):
        try:
            text, meta, warnings = func(data)
        except Exception as exc:  # noqa: BLE001
            # 引擎缺失（未安装 pypdf/pdfminer）或该引擎解析失败，一律降级到下一层；
            # 只有全部引擎都拿不到文本时才对外报错（致命错误在函数开头已挡掉）。
            attempts.append({"engine": engine_name, "error": str(exc)[:200]})
            continue
        if text and text.strip():
            meta = {"source_name": name or "", **meta}
            meta["engines_tried"] = attempts
            doc = finalize(
                text,
                title=meta.get("pdf_title") or guess_title(text, title_from_filename(name)),
                fmt="pdf",
                fallback_title=title_from_filename(name),
                meta=meta,
                warnings=warnings,
                keep_blank=False,
            )
            return doc
        attempts.append({"engine": engine_name, "error": "未抽取到文本"})

    detail = "；".join(f"{a['engine']}: {a['error']}" for a in attempts) or "无可用引擎"
    raise ParseError(
        "PDF 未抽取到任何文本（疑似扫描件/图片版，需 OCR）。各引擎结果：" + detail
    )


# ======================================================= 第 1/2 层：库 ====

def _extract_with_pypdf(data: bytes) -> tuple[str, dict, list[str]]:
    """第 1 层：pypdf（未安装则直接跳过）。"""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError as exc:
            raise ParseError("未安装 pypdf") from exc

    import io

    warnings: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")           # 空口令（仅权限密码）常见
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"PDF 已加密且空口令解密失败：{exc}")
    pages: list[str] = []
    for idx, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - 单页失败不放弃整篇
            warnings.append(f"第 {idx + 1} 页抽取失败：{exc}")
    text = "\n".join(pages)
    info = {}
    try:
        raw_info = reader.metadata or {}
        info = {str(k).lstrip("/").lower(): str(v) for k, v in raw_info.items() if v}
    except Exception:  # noqa: BLE001
        pass
    return text, {
        "engine": "pypdf",
        "pages": len(reader.pages),
        "pdf_title": info.get("title", ""),
        "pdf_author": info.get("author", ""),
        "pdf_info": info,
    }, warnings


def _extract_with_pdfminer(data: bytes) -> tuple[str, dict, list[str]]:
    """第 2 层：pdfminer.six（未安装则直接跳过）。"""
    try:
        from pdfminer.high_level import extract_text  # type: ignore
    except ImportError as exc:
        raise ParseError("未安装 pdfminer.six") from exc

    import io

    text = extract_text(io.BytesIO(data))
    return text, {"engine": "pdfminer.six"}, []


# ==================================================== 第 3 层：内置实现 ====

def _extract_with_builtin(data: bytes) -> tuple[str, dict, list[str]]:
    """第 3 层：内置极简抽取器（纯标准库）。

    流程：对象切分 → ToUnicode CMap 收集 → 逐页取 /Contents 内容流 →
    解码过滤器 → 扫描文本算子 → 按当前字体 CMap 解码字符串。
    """
    warnings: list[str] = []
    if b"/ObjStm" in data:
        warnings.append("检测到压缩对象流(/ObjStm)，内置抽取器不展开，可能漏内容（装 pypdf 可解决）")
    if b"/Encrypt" in data:
        warnings.append("PDF 标记为加密，内置抽取器可能读取失败（装 pypdf 可解决）")

    objects = _parse_objects(data)
    if not objects:
        raise ParseError("内置抽取器未能切分出任何 PDF 对象")

    cmaps = _collect_cmaps(objects)
    page_nums = [n for n, body in objects.items() if _PAGE_RE.search(body)]
    if not page_nums:
        page_nums = sorted(objects)          # 少数 PDF 对象结构不规范，退化为全文扫描

    pages_text: list[str] = []
    stream_count = 0
    for num in page_nums:
        body = objects[num]
        font_cmaps = _page_font_cmaps(body, objects, cmaps)
        chunks: list[str] = []
        for cnum in _contents_refs(body, objects):
            raw = _stream_bytes(objects.get(cnum, b""), warnings)
            if raw is None:
                continue
            stream_count += 1
            if stream_count > _MAX_STREAMS:
                warnings.append(f"内容流数量超过 {_MAX_STREAMS}，已截断")
                break
            chunks.append(_extract_text_from_content(raw, font_cmaps, warnings))
        pages_text.append("\n".join(c for c in chunks if c))

    text = _reflow("\n".join(pages_text))
    if not text.strip():
        raise ParseError("内置抽取器未抽取到文本（可能是扫描件或使用了不支持的过滤器）")

    info_title = _pdf_info_title(objects)
    if not cmaps:
        warnings.append("未发现 /ToUnicode 映射：非 ASCII 字符（中文）可能无法还原，建议 pip install pypdf")
    return text, {
        "engine": "builtin(stdlib)",
        "pages": len(page_nums),
        "objects": len(objects),
        "content_streams": stream_count,
        "to_unicode_cmaps": len(cmaps),
        "pdf_title": info_title,
    }, warnings


# ------------------------------------------------------------ PDF 对象 ----

def _parse_objects(data: bytes) -> dict[int, bytes]:
    """粗切 ``N G obj ... endobj``，返回 {对象号: 对象体}。

    说明：不做 xref 解析（部分 PDF 的 xref 已损坏或为增量更新），直接按关键字切分，
    对 99% 的常规 PDF 足够；流内容中若含 ``endobj`` 字样会被误切（记 warning）。
    """
    objects: dict[int, bytes] = {}
    for m in _OBJ_RE.finditer(data):
        num = int(m.group(1))
        start = m.end()
        end = data.find(b"endobj", start)
        if end == -1:
            end = len(data)
        objects[num] = data[start:end]
    return objects


def _apply_filters(raw: bytes, filters: list[bytes], warnings: list[str]) -> Optional[bytes]:
    """按 /Filter 链解码流数据（不支持的过滤器返回 None 并记 warning）。"""
    out = raw
    for name in filters:
        fname = name.strip(b"[]/ \r\n\t").decode("latin-1", "ignore")
        try:
            if fname in ("FlateDecode", "Fl"):
                try:
                    out = zlib.decompress(out)
                except zlib.error:
                    out = zlib.decompressobj().decompress(out)  # 容错：容忍尾部截断
            elif fname in ("ASCIIHexDecode", "AHx"):
                hexed = re.sub(rb"[^0-9A-Fa-f>]", b"", out.split(b">")[0])
                if len(hexed) % 2:
                    hexed += b"0"
                out = bytes.fromhex(hexed.decode("ascii"))
            elif fname in ("ASCII85Decode", "A85"):
                import base64

                payload = out.strip()
                if payload.endswith(b"~>"):
                    payload = payload[:-2]
                out = base64.a85decode(payload, adobe=False)
            elif fname in ("RunLengthDecode", "RL"):
                out = _run_length_decode(out)
            else:
                warnings.append(f"不支持的 PDF 过滤器 {fname}，该流已跳过")
                return None
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"PDF 流解码失败（{fname}）：{str(exc)[:120]}")
            return None
        if len(out) > _MAX_STREAM_BYTES:
            warnings.append("PDF 流解压后超限，已跳过")
            return None
    return out


def _run_length_decode(data: bytes) -> bytes:
    """PDF RunLengthDecode（用于少数压缩流）。"""
    out = bytearray()
    i = 0
    while i < len(data):
        length = data[i]
        i += 1
        if length == 128:
            break
        if length < 128:
            out += data[i : i + length + 1]
            i += length + 1
        else:
            if i < len(data):
                out += bytes([data[i]]) * (257 - length)
            i += 1
    return bytes(out)


def _stream_bytes(body: bytes, warnings: list[str]) -> Optional[bytes]:
    """从对象体里取出并解码 ``stream ... endstream`` 数据。"""
    i = body.find(b"stream")
    if i < 0:
        return None
    j = i + len(b"stream")
    if body[j : j + 2] == b"\r\n":
        j += 2
    elif body[j : j + 1] in (b"\n", b"\r"):
        j += 1
    end = body.rfind(b"endstream")
    raw = body[j:end] if end > j else body[j:]
    head = body[:i]
    m = re.search(rb"/Filter\s*(\[[^\]]*\]|/\w+)", head)
    filters: list[bytes] = []
    if m:
        filters = re.findall(rb"/\w+", m.group(1))
    return _apply_filters(raw, filters, warnings) if filters else raw


def _deref(token: bytes, objects: dict[int, bytes]) -> Optional[bytes]:
    """把 ``12 0 R`` 解引用为对象体；非引用则原样返回。"""
    m = re.match(rb"^\s*(\d+)\s+\d+\s+R\s*$", token)
    if m:
        return objects.get(int(m.group(1)))
    return token


# ------------------------------------------------------- ToUnicode CMap ----

def _collect_cmaps(objects: dict[int, bytes]) -> dict[int, dict[int, str]]:
    """收集所有字体对象的 /ToUnicode CMap：{对象号: {字符码: 文本}}。"""
    cmaps: dict[int, dict[int, str]] = {}
    for num, body in objects.items():
        if b"/Type" not in body and b"beginbf" not in body:
            continue
        m = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", body)
        target = num if b"beginbf" in body else (int(m.group(1)) if m else None)
        if target is None or target not in objects:
            continue
        if b"beginbfchar" not in objects[target] and b"beginbfrange" not in objects[target]:
            continue
        raw = _stream_bytes(objects[target], [])
        if raw is None:
            continue
        table = _parse_cmap(raw)
        if table:
            cmaps[num] = table
            if target != num:
                cmaps[target] = table
    return cmaps


def _parse_cmap(raw: bytes) -> dict[int, str]:
    """解析 CMap 的 bfchar / bfrange 段，返回 {码点: 字符串}。"""
    text = raw.decode("latin-1", "ignore")
    table: dict[int, str] = {}

    def to_str(hexstr: str) -> str:
        h = re.sub(r"[^0-9A-Fa-f]", "", hexstr)
        if len(h) % 2:
            h += "0"
        try:
            return bytes.fromhex(h).decode("utf-16-be", "ignore")
        except ValueError:
            return ""

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.DOTALL):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            table[int(src, 16)] = to_str(dst)

    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.DOTALL):
        for src, dst, tail in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block
        ):
            lo, hi = int(src, 16), int(dst, 16)
            if tail.startswith("["):
                dsts = re.findall(r"<([0-9A-Fa-f]+)>", tail)
                for offset, item in enumerate(dsts):
                    if lo + offset <= hi:
                        table[lo + offset] = to_str(item)
            else:
                base = to_str(tail)
                base_code = ord(base[0]) if base else 0
                for code in range(lo, min(hi, lo + 65535) + 1):
                    table[code] = chr(base_code + (code - lo)) if base_code else ""
    return table


def _page_font_cmaps(
    page_body: bytes, objects: dict[int, bytes], cmaps: dict[int, dict[int, str]]
) -> dict[str, dict[int, str]]:
    """页内字体名 → CMap：解析 ``/Resources /Font << /F1 5 0 R >>``。"""
    res = page_body
    m = re.search(rb"/Resources\s+(\d+)\s+\d+\s+R", page_body)
    if m and int(m.group(1)) in objects:
        res = objects[int(m.group(1))]
    fm = re.search(rb"/Font\s*<<(.*?)>>", res, re.DOTALL)
    fonts: dict[str, dict[int, str]] = {}
    if not fm:
        return fonts
    for fname, onum in re.findall(rb"/(\w+)\s+(\d+)\s+\d+\s+R", fm.group(1)):
        table = cmaps.get(int(onum))
        if table:
            fonts[fname.decode("latin-1")] = table
    return fonts


def _contents_refs(page_body: bytes, objects: dict[int, bytes]) -> list[int]:
    """页面的内容流对象号列表（``/Contents 12 0 R`` 或数组形式）。"""
    m = re.search(rb"/Contents\s+(\[[^\]]*\]|\d+\s+\d+\s+R)", page_body, re.DOTALL)
    if not m:
        return []
    return [int(n) for n in re.findall(rb"(\d+)\s+\d+\s+R", m.group(1))]


def _pdf_info_title(objects: dict[int, bytes]) -> str:
    """文档信息字典 /Info /Title（尽力而为）。"""
    for body in objects.values():
        if b"/Producer" not in body and b"/Creator" not in body:
            continue
        m = re.search(rb"/Title\s*(\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>)", body, re.DOTALL)
        if m:
            token = m.group(1)
            if token.startswith(b"<"):
                h = re.sub(rb"[^0-9A-Fa-f]", b"", token)
                if len(h) % 2:
                    h += b"0"
                try:
                    return (
                        bytes.fromhex(h.decode("ascii"))
                        .decode("utf-16-be", "ignore")
                        .replace("\ufeff", "")     # 去掉 UTF-16 BOM
                        .strip()
                    )
                except ValueError:
                    return ""
            raw = _unescape_literal(token[1:-1])
            if raw[:2] == b"\xfe\xff" or raw[:2] == b"\xff\xfe":
                return raw.decode("utf-16", "ignore").replace("\ufeff", "").strip()
            return raw.decode("latin-1", "ignore").strip()
    return ""


# --------------------------------------------------------- 内容流文本 ----

def _tokenize(content: bytes) -> list[tuple[str, object]]:
    """把内容流切成 token：lit/hex 字符串、num、name、op、[、]。"""
    tokens: list[tuple[str, object]] = []
    i, n = 0, len(content)
    while i < n:
        ch = content[i : i + 1]
        if ch in b" \t\r\n\x00\x0c":
            i += 1
            continue
        if ch == b"%":
            nl = content.find(b"\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == b"(":
            j, depth, buf = i + 1, 1, bytearray()
            while j < n:
                c = content[j : j + 1]
                if c == b"\\":
                    nxt = content[j + 1 : j + 2]
                    if nxt in (b"n", b"r", b"t", b"b", b"f"):
                        buf += {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}[nxt]
                        j += 2
                        continue
                    if nxt.isdigit():
                        oct_digits = content[j + 1 : j + 4]
                        match = re.match(rb"[0-7]{1,3}", oct_digits)
                        if match:
                            buf.append(int(match.group(0), 8) & 0xFF)
                            j += 1 + len(match.group(0))
                            continue
                    buf += nxt
                    j += 2
                    continue
                if c == b"(":
                    depth += 1
                elif c == b")":
                    depth -= 1
                    if depth == 0:
                        break
                buf += c
                j += 1
            tokens.append(("lit", bytes(buf)))
            i = j + 1
            continue
        if ch == b"<":
            if content[i : i + 2] == b"<<":
                depth, j = 1, i + 2
                while j < n and depth:
                    if content[j : j + 2] == b"<<":
                        depth += 1
                        j += 2
                        continue
                    if content[j : j + 2] == b">>":
                        depth -= 1
                        j += 2
                        continue
                    j += 1
                i = j
                continue
            j = content.find(b">", i)
            if j == -1:
                break
            h = re.sub(rb"[^0-9A-Fa-f]", b"", content[i + 1 : j])
            if len(h) % 2:
                h += b"0"
            tokens.append(("hex", h))
            i = j + 1
            continue
        if ch in b"[]":
            tokens.append(("[" if ch == b"[" else "]", None))
            i += 1
            continue
        if ch == b"/":
            match = re.match(rb"/([^\s/\[\]<>(){}%]*)", content[i:])
            name = match.group(1) if match else b""
            tokens.append(("name", name))
            i += 1 + len(name)
            continue
        match = re.match(rb"[+-]?[0-9.]+", content[i:])
        if match:
            try:
                tokens.append(("num", float(match.group(0))))
            except ValueError:
                pass
            i += len(match.group(0))
            continue
        match = re.match(rb"[^\s/\[\]<>(){}%]+", content[i:])
        word = match.group(0) if match else content[i : i + 1]
        tokens.append(("op", word))
        i += len(word)
    return tokens


def _unescape_literal(raw: bytes) -> bytes:
    """字面量字符串的转义还原（供 /Title 解析复用）。"""
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i : i + 1] == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1 : i + 2]
            if nxt in b"nrtbf":
                out += {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}[nxt]
                i += 2
                continue
            if nxt.isdigit():
                m = re.match(rb"[0-7]{1,3}", raw[i + 1 : i + 4])
                if m:
                    out.append(int(m.group(0), 8) & 0xFF)
                    i += 1 + len(m.group(0))
                    continue
            out += nxt
            i += 2
            continue
        out += raw[i : i + 1]
        i += 1
    return bytes(out)


def _decode_string(raw: bytes, cmap: Optional[dict[int, str]], two_byte: bool) -> str:
    """按字体 CMap 解码 PDF 字符串（无映射时 latin-1 近似）。"""
    if not cmap:
        return raw.decode("latin-1", "ignore")
    out: list[str] = []
    if two_byte and len(raw) >= 2:
        for k in range(0, len(raw) - 1, 2):
            code = (raw[k] << 8) | raw[k + 1]
            out.append(cmap.get(code, ""))
        if len(raw) % 2:
            out.append(cmap.get(raw[-1], ""))
    else:
        out = [cmap.get(b, "") for b in raw]
    text = "".join(out)
    if not text and raw:
        return raw.decode("latin-1", "ignore")
    return text


def _extract_text_from_content(
    content: bytes, font_cmaps: dict[str, dict[int, str]], warnings: list[str]
) -> str:
    """扫描内容流，抽取 Tj/TJ/'/" 的文本（按当前字体 CMap 解码）。"""
    tokens = _tokenize(content)
    out: list[str] = []
    cmap: Optional[dict[int, str]] = None
    two_byte = False
    pending: list[str] = []
    last_name = ""
    array_depth = 0

    def flush() -> None:
        if pending:
            out.append("".join(pending))
            pending.clear()

    def break_line() -> None:
        """换行：先把待输出文本落地，再补一个换行符（避免行首空行）。"""
        flush()
        if out and not out[-1].endswith("\n"):
            out.append("\n")

    for kind, value in tokens:
        if kind == "name":
            last_name = str(value, "latin-1")
            continue
        if kind == "[":
            array_depth += 1
            continue
        if kind == "]":
            array_depth = max(0, array_depth - 1)
            continue
        if kind in ("lit", "hex"):
            if kind == "lit":
                raw = _unescape_literal(value)  # type: ignore[arg-type]
            else:
                try:
                    raw = bytes.fromhex(bytes(value).decode("ascii"))  # type: ignore[arg-type]
                except ValueError:
                    raw = b""
            pending.append(_decode_string(raw, cmap, two_byte))
            continue
        if kind == "op":
            op = value
            if op == b"Tf":                       # 字体切换 → 换用该字体的 CMap
                cmap = font_cmaps.get(last_name)
                two_byte = bool(cmap) and max(cmap) > 0xFF
            elif op in (b"Tj", b"TJ"):
                flush()
            elif op in (b"'", b'"'):              # 换行后再输出
                break_line()
            elif op in (b"Td", b"TD", b"T*", b"ET", b"BT"):
                break_line()
            continue
        if kind == "num" and array_depth > 0:
            # TJ 数组内的负偏移（字距）较大时视为空格
            try:
                if float(value) < -180:  # type: ignore[arg-type]
                    pending.append(" ")
            except (TypeError, ValueError):
                pass
    flush()
    text = "".join(out)
    if not cmap and re.search(r"[\x80-\xff]", text or ""):
        warnings.append("部分内容使用非标准编码且无 /ToUnicode 映射，可能存在乱码")
    return text


def _reflow(text: str) -> str:
    """PDF 换行重排：合并被硬换行切断的句子，去页眉页脚。"""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    merged: list[str] = []
    for line in lines:
        if not line:
            if merged and merged[-1] != "":
                merged.append("")
            continue
        if _PAGE_FOOTER_RE.match(line):
            continue
        if merged and merged[-1] and _should_join(merged[-1], line):
            prev = merged[-1]
            sep = "" if _is_cjk(prev[-1]) and _is_cjk(line[0]) else " "
            merged[-1] = f"{prev}{sep}{line}"
        else:
            merged.append(line)
    return "\n".join(merged)


def _is_cjk(ch: str) -> bool:
    """是否 CJK 字符（判断拼接时是否需要空格）。"""
    return "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _display_width(text: str) -> int:
    """近似显示宽度（CJK 记 2，其余记 1）——用于判断"这行是否值得与下一行拼接"。"""
    return sum(2 if _is_cjk(ch) or ch.isupper() else 1 for ch in text)


def _should_join(prev: str, cur: str) -> bool:
    """是否把 cur 接到 prev 末尾（行尾无句末标点 + 行首是正文内容）。

    阈值按**显示宽度**而非字符数：中文一行 5 个字（宽 10）就已是正文，
    按字符数会被误判成短标题而拒绝拼接。
    """
    if _display_width(prev) < 10:
        return False
    if prev[-1] in "。！？；：.!?;:)]}】）”,、":
        return False
    # prev 本身像小节标题（"一、门诊时间" / "1. 借阅规则" / "## 标题"）时不拼接，
    # 否则标题会被并进正文，破坏分块的语义边界。
    if re.match(r"^(#{1,6}\s|第?[一二三四五六七八九十百]+[、.)）]|\d+[.)、]\s?)", prev):
        return False
    if cur[:1] in "#•·-*>":
        return False
    if re.match(r"^(\d+[.)、]|[一二三四五六七八九十]+[、.])", cur):
        return False
    first = cur[:1]
    return _is_cjk(first) or first.isalnum()


__all__ = ["parse_bytes"]
