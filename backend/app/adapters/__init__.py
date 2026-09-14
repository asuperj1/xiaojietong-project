"""`C21` 适配器配置包：**一份配置描述一所学校**。

对外只暴露下面这些（其余是内部细节，别直接 import 子模块）：

    from app.adapters import get_school_config, load_config, ConfigError

    cfg = get_school_config()
    cfg.student_no_valid("2024001234")   # → (True, "2024001234")     给 B23 用
    cfg.enabled_sources()                # → [SourceConfig, ...]      给 B27 用
    cfg.normalize_term("饭卡丢了")        # → "校园卡丢了"              给 C27/C28 用

自检（B34 CI 可直接跑）：

    python -m app.adapters --check       # 校验 configs/ 下全部学校配置，退出码 0/1

作者：成员3 · C21
"""

from __future__ import annotations

from app.adapters.loader import (
    CONFIG_DIR,
    ConfigError,
    available_configs,
    config_path,
    get_school_config,
    load_config,
    reset_cache,
    validate_all,
)
from app.adapters.schema import SchoolConfig, SchoolInfo, SourceConfig, StudentNoRule

__all__ = [
    # 数据模型
    "SchoolConfig",
    "SchoolInfo",
    "SourceConfig",
    "StudentNoRule",
    # 加载
    "CONFIG_DIR",
    "ConfigError",
    "available_configs",
    "config_path",
    "get_school_config",
    "load_config",
    "reset_cache",
    "validate_all",
]
