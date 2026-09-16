"""`C21` · 适配器配置结构：**一份配置描述一所学校**。

任务要求（总表 §3.1 `C21`）
--------------------------
> 一份配置描述一所学校（名称 / 校区 / 部门 / **术语映射** / **采集源**），
> 新学校**不改代码**即可接入。

三类消费方（这就是本 schema 的字段为什么是这些）
------------------------------------------------
| 字段 | 谁在用 | 用途 |
|---|---|---|
| `sources[]` | **`B27`** 配置驱动采集器 | URL / 选择器 / 分类 / 限频 / robots |
| `student_no.pattern` | **`B23`** 用户资料增强 | 学号格式校验（原 `B30`） |
| `term_map` | `C27`/`C28` 通知抽取与打分 | 本校叫法 → 标准术语（检索与抽取归一化） |
| `grades` | `B20`/`B29` 通知投递 | `campus_notice.target_grade` 的合法值域 |
| `school` / `campuses` / `departments` | `D10` 需求说明书、省级评审材料 | 多校推广的叙事与配置证据 |

设计约定（**请后续维护者遵守**）
--------------------------------
1. **校验失败抛 `ConfigError` 且必须带字段路径**（如 `sources[2].url`），
   不要只报"配置有误" —— 运维拿到路径才能直接改。
2. **默认值全部显式写出**，不依赖"缺省即安全"的隐式行为。
3. **本模块零业务依赖**：不 import `app.db` / `app.services`，
   这样 CLI、CI、独立采集脚本都能直接用，也便于单测（无需数据库与 Ollama）。

作者：成员3 · C21
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

__all__ = [
    "ConfigError",
    "SchoolInfo",
    "StudentNoRule",
    "SourceConfig",
    "SchoolConfig",
]

# 来源 key 的命名规则：小写字母/数字开头，允许 `_` `-`（用作幂等键与状态文件名）
_SOURCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigError(ValueError):
    """配置错误（人话 + **字段路径**）。

    例：``sources[2].url 必须是 http/https 地址，当前为 'ftp://x'``
    """


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    """把 pydantic 的 loc 元组转成可读路径：('sources', 2, 'url') -> 'sources[2].url'。"""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "<root>"


# ---------------------------------------------------------------- 子结构 ----


class SchoolInfo(BaseModel):
    """学校基本信息（多校推广时用于区分实例）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., description="唯一标识，与配置文件名/环境变量取值一致")
    name: str = Field(..., description="学校全称")
    short_name: str = Field("", description="短名（界面展示用）")
    campuses: list[str] = Field(default_factory=list, description="校区列表")
    departments: list[str] = Field(default_factory=list, description="部门列表（通知归属用）")

    @field_validator("code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        v = (v or "").strip()
        if not _SOURCE_KEY_RE.match(v):
            raise ValueError("code 只能是小写字母/数字开头，允许 _ 与 -")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name 不能为空")
        return v


class StudentNoRule(BaseModel):
    """学号规则（`C21` 交付给 `B23` 用）。

    ⚠️ **不同学校学号规则不同，因此一律由配置提供，禁止在代码里写死正则**
    （产品方 2026-09-12 决策，见总表 §7.2）。
    """

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        r"^\S{4,20}$",
        description="完整匹配正则；默认只校验“非空 + 长度”（宽松兜底，各校自行收窄）",
    )
    hint: str = Field("学号（4~20 位，不含空格）", description="给用户看的格式说明")
    upper: bool = Field(True, description="校验前是否转大写（部分学校学号含字母）")
    strip: bool = Field(True, description="校验前是否去首尾空白")

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"pattern 不是合法正则：{exc}") from exc
        if not v.startswith("^") or not v.endswith("$"):
            # 不禁止，但必须提示：不加锚点会变成"部分匹配"，容易放行脏数据
            raise ValueError("pattern 必须用 ^...$ 锚定完整匹配（否则 'abc123' 里的 '123' 也会通过）")
        return v


class SourceConfig(BaseModel):
    """一个采集源（`C21` 交付给 `B27` 配置驱动采集器用）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., description="源标识（幂等键/状态文件名，全局唯一）")
    name: str = Field("", description="人读名称")
    url: str = Field(..., description="列表页或入口页 URL（http/https）")
    enabled: bool = Field(True, description="是否启用")
    type: Literal["html_list", "html_page", "rss", "json_api"] = Field(
        "html_list", description="抓取类型（B27 按此分派解析器）"
    )
    category: str = Field("", description="入库时的 campus_notice.category（如「图书馆」）")
    rate_limit_qps: float = Field(0.5, gt=0, description="每秒请求数上限（礼貌抓取）")
    respect_robots: bool = Field(True, description="是否遵守 robots.txt（B26 合规）")
    selectors: dict[str, str] = Field(
        default_factory=dict,
        description="CSS 选择器：list/title/content/date 等（html_* 类型必填 list）",
    )
    schedule: str = Field("", description="cron 表达式（留空 = 由 beat 统一每日跑）")
    tags: list[str] = Field(default_factory=list, description="给检索/打分用的标签")

    @field_validator("key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        v = (v or "").strip()
        if not _SOURCE_KEY_RE.match(v):
            raise ValueError("key 只能是小写字母/数字开头，允许 _ 与 -")
        return v

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"必须是 http/https 地址，当前为 {v!r}")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> "SourceConfig":
        if self.enabled and not self.category.strip():
            raise ValueError("enabled 的采集源必须有 category（否则入库后无法分类检索）")
        if self.type in ("html_list", "html_page") and self.enabled and "list" not in self.selectors:
            raise ValueError(f"type={self.type} 时必须提供 selectors.list")
        return self


# ---------------------------------------------------------------- 主配置 ----


class SchoolConfig(BaseModel):
    """一所学校的完整接入配置。"""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(1, description="配置格式版本（便于将来迁移）")
    school: SchoolInfo
    grades: list[str] = Field(default_factory=list, description="合法年级（target_grade 值域）")
    student_no: StudentNoRule = Field(default_factory=StudentNoRule)
    term_map: dict[str, str] = Field(
        default_factory=dict, description="术语映射：本校叫法 → 标准术语（B18/C28 归一化用）"
    )
    sources: list[SourceConfig] = Field(default_factory=list)

    # ------------------------------------------------------------ 校验 ----

    @field_validator("grades")
    @classmethod
    def _check_grades(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for g in v:
            g = (g or "").strip()
            if not g:
                raise ValueError("grades 不能包含空字符串")
            if g not in out:
                out.append(g)
        return out

    @field_validator("term_map")
    @classmethod
    def _check_term_map(cls, v: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, val in v.items():
            k, val = (k or "").strip(), (val or "").strip()
            if not k or not val:
                raise ValueError("term_map 的键与值都不能为空")
            out[k] = val
        return out

    @model_validator(mode="after")
    def _check_sources_unique(self) -> "SchoolConfig":
        seen: set[str] = set()
        dup: list[str] = []
        for s in self.sources:
            if s.key in seen:
                dup.append(s.key)
            seen.add(s.key)
        if dup:
            raise ValueError(f"sources 的 key 必须唯一，重复：{sorted(set(dup))}")
        return self

    # ------------------------------------------------------- 访问接口 ----
    # 下面这些方法是 `B27` / `B23` 真正要调用的，先定死契约

    def enabled_sources(self) -> list[SourceConfig]:
        """启用中的采集源（`B27` 只跑这些）。"""
        return [s for s in self.sources if s.enabled]

    def source(self, key: str) -> SourceConfig | None:
        for s in self.sources:
            if s.key == key:
                return s
        return None

    def normalize_student_no(self, value: str) -> str:
        """按配置归一化学号（默认去空白 + 转大写）。"""
        v = value or ""
        if self.student_no.strip:
            v = v.strip()
        if self.student_no.upper:
            v = v.upper()
        return v

    def student_no_valid(self, value: str) -> tuple[bool, str]:
        """校验学号。

        Returns:
            ``(是否合法, 归一化后的值 | 错误原因)`` —— 错误原因直接用 `student_no.hint`，
            这样前端**不需要自己拼提示文案**（`F17` 两类错误提示之一）。
        """
        v = self.normalize_student_no(value)
        if not v or not re.fullmatch(self.student_no.pattern, v):
            return False, self.student_no.hint
        return True, v

    def is_grade(self, value: str) -> bool:
        """是否为本校合法年级（通知 `target_grade` 白名单）。"""
        return (value or "").strip() in self.grades

    def normalize_term(self, text: str) -> str:
        """按 `term_map` 归一化术语（**长键优先**，避免「饭卡」被「卡」抢先替换）。"""
        out = text or ""
        for src in sorted(self.term_map, key=len, reverse=True):
            out = out.replace(src, self.term_map[src])
        return out

    def summary(self) -> dict:
        """给 `/health/detail`、`B34` CI 报告用的一页摘要。"""
        return {
            "code": self.school.code,
            "name": self.school.name,
            "campuses": len(self.school.campuses),
            "departments": len(self.school.departments),
            "grades": len(self.grades),
            "terms": len(self.term_map),
            "sources_total": len(self.sources),
            "sources_enabled": len(self.enabled_sources()),
        }


def wrap_validation_error(exc: ValidationError, *, where: str) -> ConfigError:
    """把 pydantic 的报错转成**带字段路径**的 `ConfigError`。

    这是本模块最重要的一条工程约定：运维看到的必须是 ``sources[2].url ...``
    而不是 ``1 validation error for SchoolConfig``。
    """
    lines: list[str] = []
    for err in exc.errors():
        path = _loc_to_path(tuple(err.get("loc") or ()))
        msg = str(err.get("msg") or "").removeprefix("Value error, ")
        lines.append(f"  - {path}: {msg}")
    return ConfigError(f"{where} 配置校验失败：\n" + "\n".join(lines))
