#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`C21` 适配器配置测试（**纯离线**：无数据库、无 Ollama、无网络）。

    pytest backend/tests/test_adapters.py

覆盖两类：
  A. **正向**：内置两所学校配置可加载、字段消费接口（学号校验/采集源/术语映射）行为正确
  B. **反向**：写错的配置必须**报错且带字段路径**（否则运维无从下手）

作者：成员3 · C21
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml

from app.adapters import (
    ConfigError,
    available_configs,
    get_school_config,
    load_config,
    reset_cache,
    validate_all,
)
from app.adapters.loader import _main

XJT = "xiaojietong"
UNI_B = "example_university_b"


# ============================================================== A. 正向 ====


def test_builtin_configs_all_valid() -> None:
    """内置的每份配置都必须能通过校验（CI 门禁靠它）。"""
    configs = validate_all()
    assert XJT in configs
    assert UNI_B in configs
    assert available_configs() == sorted([XJT, UNI_B])


def test_summary_shape() -> None:
    s = load_config(XJT).summary()
    assert s["code"] == "xiaojietong"
    assert s["sources_total"] == 3
    assert s["sources_enabled"] == 2  # xsc-notice 示例默认关闭
    assert s["grades"] == 4


def test_student_no_ok() -> None:
    cfg = load_config(XJT)
    ok, val = cfg.student_no_valid("2024001234")
    assert ok is True and val == "2024001234"


def test_student_no_strip_and_upper() -> None:
    """归一化：去空白 + 转大写（`upper` 为真时才转）。"""
    cfg = load_config(UNI_B)
    ok, val = cfg.student_no_valid("  a20240001  ")
    assert ok is True and val == "A20240001"


def test_student_no_reject_gives_hint() -> None:
    """不合法时**返回可直接展示的提示文案**（前端不必自己拼）。"""
    cfg = load_config(XJT)
    ok, msg = cfg.student_no_valid("123")            # 太短
    assert ok is False
    assert msg == cfg.student_no.hint
    assert "10 位" in msg

    ok2, _ = cfg.student_no_valid("20240012")        # 8 位
    assert ok2 is False


def test_student_no_rule_differs_per_school() -> None:
    """同一串学号：在 A 校合法、在 B 校非法 —— 证明规则真由配置驱动。"""
    a, b = load_config(XJT), load_config(UNI_B)
    assert a.student_no_valid("2024001234")[0] is True
    assert b.student_no_valid("2024001234")[0] is False   # B 校要首位字母
    assert b.student_no_valid("A20240001")[0] is True
    assert a.student_no_valid("A20240001")[0] is False


def test_enabled_sources_filters_disabled() -> None:
    cfg = load_config(XJT)
    keys = [s.key for s in cfg.enabled_sources()]
    assert keys == ["library-notice", "jwc-notice"]
    assert cfg.source("xsc-notice") is not None
    assert cfg.source("xsc-notice").enabled is False
    assert cfg.source("no-such-key") is None


def test_source_fields_for_collector() -> None:
    """B27 采集器真正要读的字段。"""
    src = load_config(XJT).source("library-notice")
    assert src.url.startswith("https://")
    assert src.category == "图书馆"
    assert src.selectors["list"]
    assert src.rate_limit_qps > 0
    assert src.respect_robots is True


def test_normalize_term_longest_key_first() -> None:
    """长键优先：「研讨间」不能被更短的键抢先替换。"""
    cfg = load_config(XJT)
    assert cfg.normalize_term("研讨间里的热得快") == "研讨室里的违章电器"
    assert cfg.normalize_term("饭卡丢了，一卡通也要补") == "校园卡丢了，校园卡也要补"


def test_normalize_term_unknown_text_unchanged() -> None:
    cfg = load_config(XJT)
    assert cfg.normalize_term("图书馆几点关门") == "图书馆几点关门"
    assert cfg.normalize_term("") == ""


def test_is_grade() -> None:
    cfg = load_config(XJT)
    assert cfg.is_grade("2024级") is True
    assert cfg.is_grade(" 2024级 ") is True
    assert cfg.is_grade("2019级") is False
    assert cfg.is_grade("") is False


def test_get_school_config_uses_setting_and_cache(monkeypatch) -> None:
    """配置名来自 settings.school_config；缓存生效，reset 后重读。"""
    from app.core import config as app_config

    reset_cache()
    monkeypatch.setattr(app_config.settings, "school_config", UNI_B, raising=False)
    first = get_school_config()
    second = get_school_config()
    assert first is second                      # 命中缓存（同一对象）
    assert first.school.code == "university-b"
    reset_cache()
    third = get_school_config()
    assert third is not first                   # reset 后重新加载
    reset_cache()


def test_get_school_config_defaults_when_setting_missing(monkeypatch) -> None:
    """settings 没有该字段时也要能跑（防御性：默认 xiaojietong）。"""
    from app.core import config as app_config

    reset_cache()
    monkeypatch.setattr(app_config.settings, "school_config", "", raising=False)
    assert get_school_config().school.code == "xiaojietong"
    reset_cache()


def test_cli_check_passes() -> None:
    assert _main(["--check"]) == 0


def test_cli_without_flag_returns_usage() -> None:
    assert _main([]) == 2


def test_documented_command_actually_runs() -> None:
    """README / B34 CI 里写的是 `python -m app.adapters --check`。

    反向经验（PR #60 同类问题）：只测 `_main()` 函数**不能**证明命令行可用 ——
    缺少 `__main__.py` 时 `python -m app.adapters` 会直接
    `No module named app.adapters.__main__`，而单元测试依旧全绿。
    所以这里必须真的把文档里那条命令跑一遍。

    ⚠️ Windows 坑：子进程的 stdout 接管道时按**本地编码（GBK）**写出，
    若这里硬用 utf-8 解码，`proc.stdout` 会变成 `None`，
    断言随即炸成 `TypeError: argument of type 'NoneType'`。
    故显式把子进程钉成 UTF-8（`PYTHONIOENCODING`）+ 解码用 `errors="replace"`。
    """
    import os
    import subprocess
    import sys

    backend_dir = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.adapters", "--check"],
        cwd=backend_dir, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "[OK]" in (proc.stdout or "")
    assert "xiaojietong" in (proc.stdout or "")


# ============================================================== B. 反向 ====


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


MINIMAL = """
    version: 1
    school:
      code: x-school
      name: 测试学校
    student_no:
      pattern: '^\\d{6}$'
      hint: 6 位数字
"""


def test_json_config_is_supported(tmp_path: Path) -> None:
    """YAML 只是习惯；JSON 也可（零依赖兜底）。"""
    p = tmp_path / "x.json"
    p.write_text(json.dumps({
        "version": 1,
        "school": {"code": "x-school", "name": "测试学校", "campuses": ["主校区"]},
        "student_no": {"pattern": r"^\d{6}$", "hint": "6 位数字"},
        "sources": [{
            "key": "s1", "url": "https://a.edu.cn/n/", "category": "通知",
            "selectors": {"list": "li a"},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.school.campuses == ["主校区"]
    assert len(cfg.enabled_sources()) == 1


def test_missing_file_error_lists_available() -> None:
    with pytest.raises(ConfigError) as ei:
        load_config("no-such-school")
    msg = str(ei.value)
    assert "找不到配置" in msg
    assert XJT in msg            # 提示可用配置，方便改对


def test_duplicate_source_key_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "dup.yml", MINIMAL + """
    sources:
      - {key: same, url: 'https://a.edu.cn/1', category: 通知, selectors: {list: 'a'}}
      - {key: same, url: 'https://a.edu.cn/2', category: 通知, selectors: {list: 'a'}}
    """)
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "key 必须唯一" in str(ei.value) and "same" in str(ei.value)


def test_bad_url_reports_field_path(tmp_path: Path) -> None:
    """**最关键的一条**：报错必须带字段路径，运维才知道改哪里。"""
    p = _write(tmp_path, "badurl.yml", MINIMAL + """
    sources:
      - {key: s1, url: 'ftp://a.edu.cn/', category: 通知, selectors: {list: 'a'}}
    """)
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    msg = str(ei.value)
    assert "sources[0].url" in msg
    assert "http" in msg


def test_enabled_source_requires_category(tmp_path: Path) -> None:
    p = _write(tmp_path, "nocat.yml", MINIMAL + """
    sources:
      - {key: s1, url: 'https://a.edu.cn/', selectors: {list: 'a'}}
    """)
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "category" in str(ei.value)


def test_html_list_requires_list_selector(tmp_path: Path) -> None:
    p = _write(tmp_path, "nosel.yml", MINIMAL + """
    sources:
      - {key: s1, url: 'https://a.edu.cn/', category: 通知}
    """)
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "selectors.list" in str(ei.value)


def test_disabled_source_may_skip_category_and_selector(tmp_path: Path) -> None:
    """关着的源允许信息不全（便于"先登记、后补全"）。"""
    p = _write(tmp_path, "off.yml", MINIMAL + """
    sources:
      - {key: s1, url: 'https://a.edu.cn/', enabled: false}
    """)
    cfg = load_config(str(p))
    assert cfg.enabled_sources() == []


def test_bad_regex_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "badre.yml", MINIMAL.replace(r"^\d{6}$", "^[unclosed$"))
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "student_no.pattern" in str(ei.value)


def test_unanchored_pattern_rejected(tmp_path: Path) -> None:
    """不锚定的正则等于"部分匹配"，必须拦住。"""
    p = _write(tmp_path, "unanchor.yml", MINIMAL.replace(r"^\d{6}$", r"\d{6}"))
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "锚定" in str(ei.value)


def test_unknown_field_rejected(tmp_path: Path) -> None:
    """`extra=forbid`：拼错字段名要报错，而不是被静默忽略。"""
    p = _write(tmp_path, "extra.yml", MINIMAL + "\n    gradez: [2024级]\n")
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "gradez" in str(ei.value)


def test_empty_term_map_value_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "term.yml", MINIMAL + "\n    term_map:\n      饭卡: ''\n")
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "term_map" in str(ei.value)


def test_blank_grade_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "grade.yml", MINIMAL + "\n    grades: ['2024级', ' ']\n")
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "grades" in str(ei.value)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = _write(tmp_path, "list.yml", "- a\n- b\n")
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "顶层必须是对象" in str(ei.value)


def test_bad_yaml_reports_line(tmp_path: Path) -> None:
    p = _write(tmp_path, "badyaml.yml", "version: 1\nschool: {code: x, name: X\n")
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "不是合法 YAML" in str(ei.value)


def test_unsupported_suffix_rejected(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text("version = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "不支持的配置扩展名" in str(ei.value)


def test_yaml_safe_load_blocks_python_object(tmp_path: Path) -> None:
    """**安全**：必须用 safe_load —— 否则配置文件能变成 RCE。"""
    p = tmp_path / "evil.yml"
    p.write_text(
        "version: 1\n"
        "school: {code: x, name: X}\n"
        "sources: !!python/object/apply:os.system ['echo pwned']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as ei:
        load_config(str(p))
    assert "不是合法 YAML" in str(ei.value)


def test_env_override_via_settings(monkeypatch) -> None:
    """`XJT_SCHOOL_CONFIG` → settings.school_config → 真正切换学校。"""
    from app.core import config as app_config

    reset_cache()
    monkeypatch.setattr(app_config.settings, "school_config", UNI_B, raising=False)
    cfg = get_school_config()
    assert cfg.school.name == "某某工业大学"
    assert cfg.student_no.pattern == r"^[A-Z]\d{8}$"
    reset_cache()


def test_yaml_module_actually_used(tmp_path: Path) -> None:
    """确认 YAML 走的是真解析（不是被当成 JSON 蒙混过关）。"""
    p = _write(tmp_path, "anchors.yml", """
    version: 1
    school: &s
      code: x-school
      name: 测试学校
    student_no:
      pattern: '^\\d{6}$'
      hint: 6 位数字
    """)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["school"]["code"] == "x-school"
    assert load_config(str(p)).school.code == "x-school"
