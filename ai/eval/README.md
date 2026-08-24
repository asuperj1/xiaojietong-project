# eval —— 模型评估与接口压测

- 模型评估：留出测试集上准确率 / BLEU / 人工抽检问答，结果写入 MySQL `model_version.metrics_json`。
- 接口压测：`locust` 或脚本对典型查询（列表分页、批量插入）对比 C++ 扩展 vs 直连，产出具说服力的性能数据。
