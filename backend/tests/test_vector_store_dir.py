#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向量库目录解析：必须**与当前工作目录无关**（C41 实测踩过）。

背景：原实现 `Path(settings.rag_vector_dir).resolve()` 相对 CWD 解析，于是
「从仓库根跑 `ai/eval/rag_bench.py`」读到的是仓库里跟的一份**空库**（count=0）→
向量检索必然空手而归 → `rag.py` **静默降级成关键词**，而 C14 的 hit@3 基线
正是在这种状态下量出来的。而「从 `backend/` 起 uvicorn」读的是有数据的那个库 ——
同一个配置，两个进程，两套行为。

作者：成员3 · C41
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import vector_store as vs


def test_default_dir_is_anchored_at_backend_not_cwd(tmp_path, monkeypatch):
    """相对路径要锚定在 `backend/`，而不是 CWD。"""
    monkeypatch.setattr(vs.settings, "rag_vector_dir", "data/rag")
    monkeypatch.chdir(tmp_path)                      # 假装从别处运行

    got = vs._persist_dir()

    assert got.is_absolute(), "必须是绝对路径，否则换个 CWD 就是另一个库"
    assert got == (vs.BACKEND_DIR / "data/rag").resolve()
    assert got != (tmp_path / "data/rag").resolve()


def test_same_dir_from_any_cwd(tmp_path, monkeypatch):
    """换 CWD 不得改变解析结果（这就是本次要修的 bug 本尊）。"""
    monkeypatch.setattr(vs.settings, "rag_vector_dir", "data/rag")
    a = vs._persist_dir()
    monkeypatch.chdir(tmp_path)
    b = vs._persist_dir()
    assert a == b


def test_absolute_setting_is_respected(tmp_path, monkeypatch):
    """绝对路径原样生效（运维要指到别的盘时不能被改写）。"""
    target = tmp_path / "custom-rag"
    monkeypatch.setattr(vs.settings, "rag_vector_dir", str(target))
    assert vs._persist_dir() == target.resolve()


def test_backend_dir_points_at_repo_backend():
    """锚点本身要正确：`backend/app/services/vector_store.py` → `backend/`。"""
    assert vs.BACKEND_DIR.name == "backend"
    assert (vs.BACKEND_DIR / "app" / "services" / "vector_store.py").is_file()


def test_explicit_env_var_also_anchored(monkeypatch, tmp_path):
    """`.env` 里常见的相对写法（`XJT_RAG_VECTOR_DIR=data/rag`）同样要锚定 backend/。"""
    monkeypatch.setattr(vs.settings, "rag_vector_dir", os.path.join("data", "rag"))
    monkeypatch.chdir(tmp_path)
    assert vs._persist_dir() == (vs.BACKEND_DIR / "data" / "rag").resolve()


@pytest.mark.parametrize("raw", ["data/rag", "./data/rag", "data/rag/"])
def test_trailing_and_prefix_variants_collapse(raw, monkeypatch):
    """写法差异不该指向不同目录（排查环境问题时最容易踩）。"""
    monkeypatch.setattr(vs.settings, "rag_vector_dir", raw)
    assert vs._persist_dir() == (vs.BACKEND_DIR / "data" / "rag").resolve()


def test_resolved_dir_is_not_repo_root_data_rag(monkeypatch):
    """回归锁：**不能**再解析到仓库根那份空库（本次 bug 的直接现场）。"""
    monkeypatch.setattr(vs.settings, "rag_vector_dir", "data/rag")
    repo_root_data_rag = Path(vs.__file__).resolve().parents[3] / "data" / "rag"
    assert vs._persist_dir() != repo_root_data_rag.resolve()
