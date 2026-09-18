"""极简 DOM + CSS 选择器（B27）—— **只用标准库**。

为什么不引 BeautifulSoup / lxml：本仓 `services/parser/html_parser.py` 一直用 stdlib 的
`html.parser`（它自己的 `engine` 字段就写着 `html.parser(stdlib)`）。B27 是为采集器做
列表页/详情页提取，没必要为它引入新依赖 —— 沿同一口径，也让 CI 与部署不必多装东西。

支持的选择器语法（**覆盖 C21 的 `sources[].selectors` 里出现的全部写法**）：

| 写法 | 例子 | 说明 |
|---|---|---|
| `tag` | `div` | 标签名 |
| `.class` | `.news-list` | 含该类名 |
| `#id` | `#main` | id 相等 |
| `tag.class` | `h1.article-title` | 组合 |
| 后代组合 | `ul.news-list li a` | 空格分隔，逐级在**上一步结果的子树**里找 |

**不支持**：`>` `+` `~`、`[attr]`、`:pseudo`（C21 配置里没用到；需要时再扩，
`parse_selector` 遇到不认识的写法会**直接报错**而不是静默返回空 —— 免得"配置写错了但采集悄悄没数据"）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: HTML 里没有闭合标签的元素，遇到它们不要压栈
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

_SIMPLE_RE = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*)?(?P<rest>(?:[.#][\w-]+)*)$")
_TOKEN_RE = re.compile(r"[.#][\w-]+")


@dataclass
class Node:
    """轻量节点。只保留采集需要的东西：标签、属性、自身文本、子节点、父指针。"""

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    text_parts: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    @property
    def id(self) -> str:
        return self.attrs.get("id") or ""

    def text(self) -> str:
        """递归取文本（含子节点），调用方通常还要 `clean_text()` 归一空白。"""
        buf = list(self.text_parts)
        for child in self.children:
            buf.append(child.text())
        return "".join(buf)

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name.lower(), default)

    def descendants(self):
        """深度优先遍历全部后代（不含自身）。"""
        for child in self.children:
            yield child
            yield from child.descendants()


class _Builder(HTMLParser):
    """把 HTML 喂进来，构造一棵 Node 树。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - stdlib 签名
        node = Node(
            tag.lower(),
            {str(k).lower(): (v or "") for k, v in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)
        if node.tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        # 容忍畸形 HTML：从栈顶往下找到同名标签，把它之上的都弹掉
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == target:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._stack[-1].text_parts.append(data)


@dataclass(frozen=True)
class _Step:
    tag: str = ""
    classes: tuple[str, ...] = ()
    ident: str = ""

    def matches(self, node: Node) -> bool:
        if self.tag and node.tag != self.tag:
            return False
        if self.ident and node.id != self.ident:
            return False
        if self.classes and not set(self.classes).issubset(node.classes):
            return False
        return True


def parse_selector(expr: str) -> list[_Step]:
    """把选择器拆成若干步；不支持的写法**直接报错**。"""
    steps: list[_Step] = []
    for raw in (expr or "").split():
        m = _SIMPLE_RE.match(raw)
        if not m:
            raise ValueError(
                f"不支持的选择器：{raw!r}（本实现只支持 tag / .class / #id 及其后代组合）"
            )
        classes: list[str] = []
        ident = ""
        for token in _TOKEN_RE.findall(m.group("rest") or ""):
            if token.startswith("."):
                classes.append(token[1:])
            else:
                ident = token[1:]
        steps.append(_Step((m.group("tag") or "").lower(), tuple(classes), ident))
    if not steps:
        raise ValueError(f"空选择器：{expr!r}")
    return steps


def select_all(root: Node, expr: str) -> list[Node]:
    """返回全部命中节点（文档顺序）。"""
    current = [root]
    for step in parse_selector(expr):
        found: list[Node] = []
        for base in current:
            for node in base.descendants():
                if step.matches(node):
                    found.append(node)
        current = found
        if not current:
            return []
    return current


def select_first(root: Node, expr: str) -> Node | None:
    hits = select_all(root, expr)
    return hits[0] if hits else None


def parse_html(html: str) -> Node:
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def clean_text(value: str) -> str:
    """归一空白：换行/连续空格压成单个空格，去首尾。"""
    return re.sub(r"\s+", " ", value or "").strip()


def absolutize(href: str, base_url: str) -> str:
    """把列表页里的相对链接补全成绝对地址（详情页要单独去抓）。"""
    from urllib.parse import urljoin  # noqa: PLC0415 - 仅此处需要

    return urljoin(base_url, (href or "").strip())
