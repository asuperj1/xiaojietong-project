"""`C21` 配置加载器：从 YAML / JSON 读入一所学校的适配器配置。

用法
----
    from app.adapters import get_school_config

    cfg = get_school_config()                 # 取 settings.school_config（默认 xiaojietong）
    ok, val = cfg.student_no_valid("2024001234")   # B23 学号校验
    for s in cfg.enabled_sources():                # B27 采集器
        print(s.key, s.url, s.category)

命令行自检（`B34` CI 里可直接跑）
--------------------------------
    python -m app.adapters --check     # 校验 configs/ 下全部配置，退出码 0/1

约定
----
- 支持 ``.yml`` / ``.yaml``（需 PyYAML）与 ``.json``（**零依赖兜底**）
- 进程内缓存（配置是只读的）；改完配置调 :func:`reset_cache`
- 找不到 / 解析失败 / 校验失败 → 一律抛 :class:`ConfigError`，且**带字段路径**
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.adapters.schema import SchoolConfig, wrap_validation_error
from app.adapters.schema import ConfigError as _ConfigError
from pydantic import ValidationError

# 对外暴露的名字（`__all__` 里声明为 `ConfigError`）
ConfigError = _ConfigError

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_DIR",
    "ConfigError",
    "available_configs",
    "config_path",
    "get_school_config",
    "load_config",
    "reset_cache",
    "validate_all",
]

CONFIG_DIR = Path(__file__).resolve().parent / "configs"
_SUFFIXES = (".yml", ".yaml", ".json")

_cache: dict[str, SchoolConfig] = {}


# ---------------------------------------------------------------- 读文件 ----


def _read_raw(path: Path) -> dict[str, Any]:
    """按扩展名分派读取；返回原始 dict（**不做**字段校验）。"""
    if not path.is_file():
        raise _ConfigError(
            f"配置文件不存在：{path}\n"
            f"  可用配置：{', '.join(available_configs()) or '（configs/ 为空）'}"
        )
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ConfigError(f"{path.name} 不是合法 JSON：第 {exc.lineno} 行 {exc.msg}") from exc
    elif suffix in (".yml", ".yaml"):
        try:
            import yaml  # 局部导入：没装 PyYAML 时仍可用 JSON 配置
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise _ConfigError(
                f"{path.name} 是 YAML，但环境未安装 PyYAML。\n"
                "  二选一：① pip install PyYAML；② 改用 .json 配置（零依赖）"
            ) from exc
        try:
            data = yaml.safe_load(text)  # safe_load：禁止 !!python/object 之类的构造器
        except yaml.YAMLError as exc:
            raise _ConfigError(f"{path.name} 不是合法 YAML：{exc}") from exc
    else:
        raise _ConfigError(f"不支持的配置扩展名 {suffix!r}（只支持 {', '.join(_SUFFIXES)}）")

    if not isinstance(data, dict):
        raise _ConfigError(f"{path.name} 顶层必须是对象（mapping），当前是 {type(data).__name__}")
    return data


# ------------------------------------------------------------ 路径解析 ----


def available_configs() -> list[str]:
    """`configs/` 下可用的配置名（不含扩展名）。"""
    if not CONFIG_DIR.is_dir():
        return []
    names = {p.stem for p in CONFIG_DIR.iterdir() if p.suffix.lower() in _SUFFIXES}
    return sorted(names)


def config_path(name_or_path: str) -> Path:
    """把「配置名」或「路径」解析成真实路径。

    优先级：**字面路径存在 → 用它**；否则当作配置名去 `configs/` 里找
    （`<name>.yml` → `<name>.yaml` → `<name>.json`）。
    """
    raw = (name_or_path or "").strip()
    if not raw:
        raise _ConfigError("配置名不能为空（可设 XJT_SCHOOL_CONFIG，或用 get_school_config()）")

    p = Path(raw)
    if p.is_file():
        return p
    # 带扩展名但不在 configs/ 下 → 也允许按路径处理（保持"路径优先"的直觉）
    if p.suffix.lower() in _SUFFIXES and (p.is_absolute() or p.parent != Path(".")):
        return p

    for suffix in _SUFFIXES:
        cand = CONFIG_DIR / f"{raw}{suffix}"
        if cand.is_file():
            return cand
    raise _ConfigError(
        f"找不到配置 {raw!r}。可用：{', '.join(available_configs()) or '（configs/ 为空）'}\n"
        f"  也支持直接给文件路径（{', '.join(_SUFFIXES)}）"
    )


# ---------------------------------------------------------------- 加载 ----


def load_config(name_or_path: str) -> SchoolConfig:
    """加载并校验一份配置（**不走缓存**）。"""
    path = config_path(name_or_path)
    raw = _read_raw(path)
    try:
        cfg = SchoolConfig.model_validate(raw)
    except ValidationError as exc:
        raise wrap_validation_error(exc, where=str(path)) from exc
    return cfg


def get_school_config(reload: bool = False) -> SchoolConfig:
    """取当前生效的学校配置（**进程内缓存**）。

    配置名来自 ``settings.school_config``（环境变量 ``XJT_SCHOOL_CONFIG``）。
    """
    from app.core.config import settings  # 局部导入：避免循环依赖

    name = str(getattr(settings, "school_config", "") or "").strip()
    if not name:
        name = "xiaojietong"
    if not reload and name in _cache:
        return _cache[name]

    cfg = load_config(name)
    _cache[name] = cfg
    logger.info("已加载学校配置 %s（%s），采集源 %d 个（启用 %d）",
                cfg.school.code, cfg.school.name,
                len(cfg.sources), len(cfg.enabled_sources()))
    return cfg


def reset_cache() -> None:
    """清缓存（测试与热改配置用）。"""
    _cache.clear()


def validate_all() -> dict[str, SchoolConfig]:
    """校验 `configs/` 下**全部**配置；任何一个不合法即抛 `ConfigError`。

    给 `B34` CI 用：新增一所学校如果写错，流水线立刻红。
    """
    names = available_configs()
    if not names:
        raise _ConfigError(f"{CONFIG_DIR} 下没有任何配置")
    out: dict[str, SchoolConfig] = {}
    for name in names:
        out[name] = load_config(name)
    return out


# ---------------------------------------------------------------- CLI ----


def _main(argv: list[str] | None = None) -> int:
    import sys

    # 输出里含中文；Windows 控制台默认 GBK，遇到无法编码的字符会
    # UnicodeEncodeError 直接崩掉 CLI。这里只降级不崩（保持原编码）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - 非 TTY/已被重定向
            pass

    args = list(argv if argv is not None else sys.argv[1:])
    if "--check" not in args:
        print("用法：python -m app.adapters --check   # 校验 configs/ 下全部学校配置")
        return 2
    try:
        configs = validate_all()
    except _ConfigError as exc:
        print(f"[NG] {exc}")
        return 1

    print(f"[OK] {len(configs)} 份学校配置全部通过校验（{CONFIG_DIR}）")
    for name, cfg in configs.items():
        s = cfg.summary()
        print(f"  - {name:<24} {s['name']:<16} 采集源 {s['sources_enabled']}/{s['sources_total']}"
              f"  年级 {s['grades']}  术语 {s['terms']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(_main())
