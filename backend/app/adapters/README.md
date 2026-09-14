# `C21` 适配器配置 · 一份配置描述一所学校

> **目标**：接入一所新学校**不用改代码** —— 复制一份 YAML，改学校名 / 学号规则 / 术语映射 / 采集源即可。
> **验收**：`python -m app.adapters --check` 退出码 = 0（`B34` CI 会跑这一条）。

---

## 1. 30 秒上手

```python
from app.adapters import get_school_config

cfg = get_school_config()                  # 读 XJT_SCHOOL_CONFIG，默认 xiaojietong

# ① B23 用户资料增强：学号校验（返回可直接展示的提示文案）
ok, val_or_hint = cfg.student_no_valid("2024001234")
#    (True, '2024001234')   或   (False, '10 位数字学号（见录取通知书右上角）')

# ② B27 配置驱动采集器：只跑启用中的源
for s in cfg.enabled_sources():
    print(s.key, s.url, s.category, s.selectors["list"], s.rate_limit_qps)

# ③ C27/C28 通知抽取与打分：术语归一化（长键优先）
cfg.normalize_term("研讨间里的热得快")     # → '研讨室里的违章电器'

# ④ 通知投递范围校验
cfg.is_grade("2024级")                     # → True
```

命令行：

```bash
cd backend
python -m app.adapters --check      # 校验 configs/ 下全部学校配置
```

---

## 2. 目录与文件

```
backend/app/adapters/
├── __init__.py                     # 对外 API（只从这里 import）
├── schema.py                       # 数据模型 + 校验（pydantic v2，extra=forbid）
├── loader.py                       # YAML/JSON 加载 + 缓存 + CLI
├── README.md                       # 本文件
└── configs/
    ├── xiaojietong.yml             # 本校（示例，可直接用）
    └── example_university_b.yml    # 第二所学校（省级推广叙事证据）
```

支持格式：**`.yml` / `.yaml`**（需 `PyYAML`）· **`.json`**（零依赖兜底）。

---

## 3. 字段表

| 字段 | 谁在用 | 说明 |
|---|---|---|
| `version` | — | 配置格式版本，当前 `1` |
| `school.code` | 全局 | 唯一标识（小写字母/数字开头，允许 `_` `-`），与文件名一致 |
| `school.name` / `short_name` | 界面、材料 | 学校全称 / 短名 |
| `school.campuses` | `D10` 需求说明书 | 校区列表 |
| `school.departments` | 通知归属 | 部门列表 |
| `grades` | `B20`/`B29` 投递 | 合法年级（`target_grade` 白名单） |
| `student_no.pattern` | **`B23`** | **必须 `^...$` 锚定**；不锚定会拦下（否则 `abc123` 里的 `123` 也通过） |
| `student_no.hint` | `B23`/`F17` | 校验失败时**直接返回给用户**的文案 |
| `student_no.upper` / `strip` | `B23` | 归一化：是否转大写 / 去首尾空白 |
| `term_map` | `C27`/`C28` | 本校叫法 → 标准术语（**长键优先**替换） |
| `sources[].key` | **`B27`** | 源标识（唯一；用作幂等键与状态文件名） |
| `sources[].url` | `B27` | 必须是 `http://` / `https://` |
| `sources[].enabled` | `B27` | 关掉的源允许信息不全（便于先登记后补全） |
| `sources[].type` | `B27` | `html_list` / `html_page` / `rss` / `json_api` |
| `sources[].category` | 入库 | 落 `campus_notice.category`（**启用的源必填**） |
| `sources[].rate_limit_qps` | **`B26`** | 每秒请求数上限（礼貌抓取） |
| `sources[].respect_robots` | **`B26`** | 是否遵守 robots.txt |
| `sources[].selectors` | `B27` | `html_*` 类型**必须**有 `list` |
| `sources[].schedule` | `B27`/beat | cron 表达式；留空 = 由 beat 统一每日跑 |
| `sources[].tags` | `C28` 打分 | 检索/推荐标签 |

---

## 4. 接入一所新学校（**全程不改代码**）

1. `cp configs/xiaojietong.yml configs/<新学校code>.yml`
2. 改 `school.*` / `grades` / `student_no.pattern` / `term_map` / `sources`
3. `python -m app.adapters --check` → 必须 **退出码 0**
4. 运行时切换：`.env` 里设 `XJT_SCHOOL_CONFIG=<新学校code>`（或直接给文件路径）

`configs/example_university_b.yml` 就是按这个流程做的第二所学校示范：
它把学号规则从「10 位数字」换成了「首位字母 + 8 位数字」，
**同一串 `2024001234` 在 A 校合法、在 B 校非法** —— 这就是"规则由配置驱动"的证明。

---

## 5. 设计约定（后续维护者请遵守）

1. **报错必须带字段路径**：`sources[2].url: 必须是 http/https 地址，当前为 'ftp://x'`。
   不要只报"配置有误"——运维拿着路径才能直接改对。
   > 实现见 `schema.wrap_validation_error()` + 单测 `test_bad_url_reports_field_path`。
2. **`extra="forbid"`**：拼错字段名要报错，而不是被静默忽略（`test_unknown_field_rejected`）。
3. **只用 `yaml.safe_load`**：绝不允许配置文件变成 RCE 入口
   （`test_yaml_safe_load_blocks_python_object` 用 `!!python/object/apply:os.system` 验证）。
4. **本模块零业务依赖**：不 import `app.db` / `app.services`，因此
   CLI、CI、独立采集脚本都能直接用，单测也不需要数据库与 Ollama。
5. **默认值显式写出**（见 `schema.py` 的 `Field(...)`），不依赖"缺省即安全"的隐式行为。

---

## 6. 单测

```bash
cd backend
python -m pytest tests/test_adapters.py -q      # 33 项，纯离线
```

覆盖：两所学校加载、学号规则按校不同、采集源过滤、术语长键优先、
缓存与 `reset`、CLI 退出码，以及 **11 项反向用例**
（坏 URL / 重复 key / 未锚定正则 / 未知字段 / 顶层非对象 / 不安全 YAML …）。

作者：成员3 · C21
