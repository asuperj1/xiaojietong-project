# rag —— RAG 知识库构建与检索

- 知识源：学校官方通知、院系政策、图书馆/校医院规则、FAQ。
- 流程：文档 → 清洗 → 分块(500~800字符) → `bge-m3` 向量化(Ollama `/api/embed`) → ChromaDB → 检索 top-k → 拼 Prompt 生成。
- 检索置信度低时明确回答"未收录"，避免幻觉（相似度低于阈值即视为未命中）。
- **已落地**（`backend/app/services/`）：
  - `embedder.py`：bge-m3 embedding 客户端（批量 + 缓存 + 30s 熔断）
  - `chunker.py`：中文句子聚合分块（600 字符 / 重叠 100）
  - `vector_store.py`：ChromaDB 持久化，缺依赖自动降级 numpy 余弦（pickle 持久化）
  - `rag.py`：`build_index()/index_doc()` 建索引、`retrieve()/build_system_prompt()` 检索（向量优先，失败降级关键词分词匹配）
- 命令运维脚本（本目录）：
  - `build_index.py`：`--force` 全量重建 / `--doc 1,2,3` 指定文档
  - `retrieve.py`：`python retrieve.py "查询词" [top_k]`
  - `requirements.txt`：RAG 依赖（numpy + chromadb，Python 3.14 已验证）

## 使用（Git Bash，需 MySQL 已启动）

```bash
cd backend
# 1) 安装依赖
pip install -r ../ai/rag/requirements.txt
# 2) 构建索引（Ollama 加载 bge-m3 后；--force 全量）
XJT_DB_PORT=3307 XJT_DB_PASSWORD=*** python ../ai/rag/build_index.py --force
# 3) 检索验证
XJT_DB_PORT=3307 XJT_DB_PASSWORD=*** python ../ai/rag/retrieve.py "图书馆几点关门" 3
```

> 说明：embedding 依赖 Ollama 加载 bge-m3 模型；未就绪时建索引返回 `embed_failed`，在线检索自动降级为关键词匹配。
