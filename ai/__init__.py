"""校捷通 AI 侧代码包（`ai/`）。

注意：`ai/finetune/`、`ai/eval/`、`ai/rag/` 下的脚本仍是**可直接执行**的独立脚本
（`python ai/finetune/train.py`），本 `__init__.py` 只是让 `ai.dataset` 能作为
命名空间包被 `python -m ai.dataset.cli` 调用，不影响既有脚本的用法。
"""
