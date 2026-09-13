# 校捷通 · 内部联调部署（公网 IP + 8080）

> ## 🟡 状态：**备选方案 · 暂不实际部署**（2026-09-13 决定）
>
> 本方案已完整编写并**已验证核心安全组件**（访问闸门 12/12、约束合规全通过），
> 但**当前阶段不执行实际部署**，作为**备选预案**保留：
>
> - ✅ 随时可用：脚本 + 文档已就绪，需要时按 [`服务器后端部署-负责人手册.md`](服务器后端部署-负责人手册.md) 执行
> - ⏸️ **未在真实服务器上跑过**（开发机为 Windows，无法执行 `bash -n`）
> - ⏸️ 服务器 **122.51.248.93** 目前**未部署本方案**
>
> **恢复部署的前提**：先读 [`ICP备案期间守则-全员必读.md`](ICP备案期间守则-全员必读.md)，
> 确认四项前置（安全组 / 出口 IP / 令牌分发 / 备案状态）后再动手。

> **本文档目录的定位**：ICP 备案审核期间，**仅用于 4 人团队内部联调自测**的部署方案。
> **不对外提供网站服务、不使用域名、不使用 80/443、不使用 HTTPS。**

---

## 📌 只看这两份就够了（按你的角色选）

| 你的角色 | 看这份 | 内容 |
|---|---|---|
| **负责部署的人**（持有服务器 root + 数据库密码） | **[`服务器后端部署-负责人手册.md`](服务器后端部署-负责人手册.md)** | 9 步操作：安全组 → 防火墙 → 一键部署 → 初始化库 → 体检 → 发令牌；含预期输出与排障 |
| **团队 4 人全部** | **[`ICP备案期间守则-全员必读.md`](ICP备案期间守则-全员必读.md)** | 红线卡（10 条）、能做/不能做、令牌纪律、发现异常怎么办、全员确认模板 |

> ⚠️ **顺序**：先读守则 → 再按负责人手册操作。**安全组必须在云控制台配，脚本改不了。**

下面的 `docs/01~05` 是**深入附录**（原理、鉴权细节、完整风险说明、验收清单），需要时再翻。

---

## 一、访问方式（就这一条）

```
http://<公网IP>:8080/api/v1
请求头：X-Access-Token: <团队访问令牌>
```

- 当前服务器 IP：**`122.51.248.93`**
- 端口：**8080**（固定；绝不使用 80/443）
- 健康检查（无需令牌）：`http://122.51.248.93:8080/api/v1/health`

```bash
# 带令牌调用示例
curl -H "X-Access-Token: <令牌>" http://122.51.248.93:8080/api/v1/secondhand/items
```

---

## 二、目录结构

### 第一部分 · 部署 Shell 脚本

| 文件 | 路径 | 作用 |
|---|---|---|
| 公共库 | `scripts/lib/common.sh` | 日志/配置/端口策略守卫（被 source，不单独执行） |
| 系统依赖 | `scripts/00-install-system-deps.sh` | gcc/cmake/libmysqlclient-dev/python/mysql-server |
| 拉取代码 | `scripts/01-pull-code.sh` | 从 GitHub 拉取最新代码（可重复执行） |
| 编译 C++ | `scripts/02-build-cpp.sh` | 本机编译 `jt_db.cpython-*.so` |
| Python 环境 | `scripts/03-setup-python-env.sh` | venv + 依赖（清华镜像） |
| 环境变量 | `scripts/04-configure-env.sh` | 生成 `server.env`（含随机访问令牌） |
| 初始化库 | `scripts/05-init-database.sh` | 导入 45 张表 + 种子数据 |
| 安装服务 | `scripts/06-install-systemd.sh` | systemd 单元（监听 8080） |
| 防火墙 | `scripts/07-firewall.sh` | 放行 8080 / **拒绝 80·443·3306·6379** |
| **★一键部署** | **`scripts/08-deploy.sh`** | **拉码 → 编译 → 装依赖 → 配环境 → 重启** |
| 重启 | `scripts/09-restart.sh` | 仅重启 + 就绪检查 |
| 体检 | `scripts/10-status.sh` | 服务/端口/闸门/数据库/防火墙/日志 |
| 卸载 | `scripts/99-uninstall.sh` | 卸载服务与防火墙规则（保留代码与库） |
| 服务单元 | `systemd/xiaojietong-api.service` | systemd 模板（占位符由 06 脚本替换） |

### 第二部分 · 部署说明文档

**★ 主文档（按角色选，先看这两份）**

| 文件 | 读者 | 内容 |
|---|---|---|
| **`服务器后端部署-负责人手册.md`** | 负责人 | 9 步操作 + 每步预期输出 + 排障速查 + 负责人独有责任 |
| **`ICP备案期间守则-全员必读.md`** | 全员 4 人 | 红线卡 + 能做/不能做 + 令牌纪律 + 异常处置 + 确认模板 |

**深入附录（需要时再翻）**

| 文件 | 作用 |
|---|---|
| [`docs/01-部署方案.md`](docs/01-部署方案.md) | 整体架构、端口策略、目录规划、部署流程图 |
| [`docs/02-部署操作手册.md`](docs/02-部署操作手册.md) | 安全组 + 逐步命令 + 启动 + 访问 + 注意事项 |
| [`docs/03-访问鉴权说明.md`](docs/03-访问鉴权说明.md) | 简易接口鉴权（防外网陌生人）+ 验证方法 |
| [`docs/04-ICP备案风险提示.md`](docs/04-ICP备案风险提示.md) | ⚠️ **监管依据、12 条禁止、7 项自查（最详细）** |
| [`docs/05-运维排障与验收清单.md`](docs/05-运维排障与验收清单.md) | 日志、常见错误、回滚、交付验收清单 |

配套代码：`backend/app/core/access_gate.py`（访问闸门实现）
配套工具：`tools/verify_access_gate.py`（闸门行为验证）

---

## 三、5 分钟快速开始

在服务器上（Ubuntu 22.04，root 或 sudo）：

```bash
# ① 装系统依赖（含 MySQL，并强制 bind-address=127.0.0.1）
sudo bash deploy/internal-test/scripts/00-install-system-deps.sh

# ② 配主机防火墙（放行 8080；拒绝 80/443/3306/6379）
sudo bash deploy/internal-test/scripts/07-firewall.sh

# ③ 一键部署（拉码 + 编译 C++ + 装依赖 + 配环境 + 重启）
export GITHUB_TOKEN=<你的只读PAT>          # 私有仓库必需
sudo -E bash deploy/internal-test/scripts/08-deploy.sh

# ④ 初始化数据库（首次执行）
sudo bash deploy/internal-test/scripts/05-init-database.sh

# ⑤ 重启并体检
sudo bash deploy/internal-test/scripts/09-restart.sh
bash deploy/internal-test/scripts/10-status.sh
```

> **云服务器【安全组】必须在控制台单独配置**（脚本无法代改）：
> ✅ 放行 `8080/TCP`　❌ **拒绝 `80`、`443`**　❌ 拒绝 `3306`、`6379`

---

## 四、日常更新（改完代码后）

```bash
# 全量：拉最新代码 + 重新编译 C++ + 重启
sudo bash deploy/internal-test/scripts/08-deploy.sh

# 只改了 Python（未动 C++ 层）—— 省去编译
sudo bash deploy/internal-test/scripts/08-deploy.sh --no-build

# 只重启
sudo bash deploy/internal-test/scripts/09-restart.sh
```

---

## 五、红线清单（**违反即须立即回滚**）

| # | 红线 | 说明 |
|---|---|---|
| 1 | 不得监听/放行 **80、443** | 备案未完成，对外提供网站服务属违规 |
| 2 | 不得绑定**域名**、不得加 A 记录 | 本阶段只用 `IP:8080` |
| 3 | 不得申请/安装 **SSL 证书** | 本方案无 HTTPS |
| 4 | 不得把 **3306 / 6379** 暴露公网 | 数据库/缓存只绑 `127.0.0.1` |
| 5 | 不得做**内网穿透 / 隧道代理** | 属规避备案，禁止 |
| 6 | 不得把 8080 对**全网**长期开放 | 建议安全组限制团队固定出口 IP |
| 7 | **`XJT_ACCESS_TOKEN` 不得为空** | 否则陌生人可任意访问测试接口 |
| 8 | 令牌与 `.env` **不得**提交进仓库 / 发公开群 | 泄露等于门禁失效 |

> 详细说明与后果见 [`docs/04-ICP备案风险提示.md`](docs/04-ICP备案风险提示.md)。
