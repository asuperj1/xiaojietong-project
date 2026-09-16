# 数据库迁移规范（db/sql）

> 生效日期：2026-09-15　｜　适用范围：`db/sql/` 下所有脚本
> 缘起：`15_home_banner.sql` / `18_takeaway_pickup.sql` 在多人多机环境下反复出现"**同一脚本，一边跑通一边报 `ERROR 1054`**"，并已造成 `pickup_point`、`home_banner` 出现**三种互不相同的列形状**。本规范用于终止这类漂移。

---

## 一、唯一真源原则（最重要的一条）

> **`db/sql/` 里脚本的 `CREATE TABLE` 定义，是表结构的唯一真源。**

- ✅ 新环境：跑脚本 → 按定义建表。
- ✅ 旧环境（表形状不同）：**用「守卫式补列」把它收敛到定义的样子**。
- ❌ **禁止**：为了让脚本在自己的旧库上跑通，而去**修改 `CREATE TABLE` 定义**（删列、改名、换类型）。
  这会把"个人环境的历史包袱"固化进真源，让别人和环境全部跟着错。

**判据一句话**：*你改的应该是"环境"，不是"定义"。*

---

## 二、为什么必须这样：`CREATE TABLE IF NOT EXISTS` 的陷阱

```sql
CREATE TABLE IF NOT EXISTS `t` ( ... );   -- 表已存在时：只做一件事 —— 跳过
```

它**不校验列名、不补列、不改形状**。于是：

| 环境 | 表状态 | 跑 `CREATE IF NOT EXISTS` | 后续依赖新列/新名字的语句 |
|---|---|---|---|
| A（表建得晚） | 按定义建的 | 跳过 | ✅ 对得上 |
| B（表建得早） | 老形状 | 跳过 | ❌ `ERROR 1054 Unknown column` |

两边都"跑过了"，但**脚本对谁都没生效** —— 这就是所有"各测各的都对"现象的根因。

**结论**：结构变更**不能只靠 `CREATE IF NOT EXISTS`**，必须配「守卫式补列」。

---

## 三、守卫式补列标准写法（MySQL 8）

MySQL 8 **没有** `ADD COLUMN IF NOT EXISTS`（那是 MariaDB 语法）。标准做法是 `information_schema` 探测 + 预处理语句：

```sql
-- 模板：补一列（幂等；重复执行只打印 [skip]，不报错、不丢数据）
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `t` ADD COLUMN `col` VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''说明'' AFTER `prev_col`',
        'SELECT ''[skip] t.col 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 't' AND column_name = 'col'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;
```

**回填/改名（旧列 → 新列）**，同样守卫，且**只回填空值**：

```sql
-- 1) 先补新列（用上面的模板，默认值留空/NULL）
-- 2) 回填：仅当新列还是空时才从旧列搬，避免覆盖已有数据
SET @ddl := (
    SELECT IF(COUNT(*) > 0,
        'UPDATE `t` SET `business_hours` = `open_time` WHERE (`business_hours` IS NULL OR `business_hours` = '''') AND `open_time` IS NOT NULL',
        'SELECT ''[skip] 旧列 open_time 不存在，无需回填'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 't' AND column_name = 'open_time'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;
-- 3) 旧列【暂不 DROP】——留一个观察期，确认无代码/接口引用后再单独清理
```

**要点**
1. **幂等**：重复执行只打印 `[skip]`；
2. **不丢数据**：`ADD COLUMN` 用可空/默认值；回填只填空值；
3. **旧列先留**：直接 `DROP` 会丢数据且难回滚，观察一轮再删；
4. **不改定义**：所有动作用 `ALTER`，**不碰** `CREATE TABLE` 段。

---

## 四、权威列定义（有争议的表以本节为准）

以下为**已裁定**的权威定义。其余环境请用「守卫式补列 + 回填」收敛过来。

### 4.1 `pickup_point`（快递驿站 / 取件点）

```sql
id, name, address, business_hours, latitude, longitude,
contact_phone, sort, status, is_deleted, created_at, updated_at
```

**理由**：驿站业务需要 **经纬度**（地图定位，必须成对）、**联系电话**（联系驿站）、**软删除 `is_deleted`**（项目统一约定，见 §5）；启用状态用 `status`（与项目其他表一致）。
**需收敛的旧名**：`open_time` → `business_hours`；`enabled` → `status`；补 `latitude`。
**注意**：`campus` 为可选扩展，若保留需在定义中显式声明（不得只存在于个别环境）。

### 4.2 `home_banner`（首页轮播）

```sql
id, title, image, link_type, link_target, sort, start_at, end_at, enabled, created_at, updated_at
```

**需收敛的旧名**：`image_url` → `image`；`link_url` → `link_type` + `link_target`（拆分为类型+目标，`link_type` 取值 `none` / `page` / `notice` / `url`）。
**跳转路径必须是真实注册的页面**（见 `miniprogram/app.json`）：例如 `pages/service/service`、`pages/agent/index`、`pages/secondhand/index`；**不要写** `pages/ai/index` 这类未注册路径（点击会跳转失败）。

---

## 五、种子数据规范

### 5.1 幂等：固定主键 + `ON DUPLICATE KEY UPDATE`

```sql
INSERT INTO `t` (`id`, `col_a`, `col_b`) VALUES
  (1, 'a1', 'b1'),
  (2, 'a2', 'b2')
ON DUPLICATE KEY UPDATE `col_a` = VALUES(`col_a`), `col_b` = VALUES(`col_b`);
```

- ❌ **不要**用 `WHERE NOT EXISTS (SELECT 1 FROM t)`：它是"**整表空才插**"，表非空时**既不补齐也不更新**，且**静默无效果**，比报错更难发现。
- ✅ 固定主键 + ODKU：新环境全插、重跑只更新、缺行能补齐。

### 5.2 运营数据（如 `home_banner`、`pickup_point`）用**空更新**

运营会改文案/排序。若种子用全列 ODKU，**每次重跑都会把运营的改动还原**：

```sql
ON DUPLICATE KEY UPDATE `id` = VALUES(`id`);   -- 只保证"缺行补齐"，不覆盖已有内容
```

### 5.3 种子内容要可验证

脚本里的引用（跳转路径、外键 id、枚举值）**必须能在当前代码库里找到对应物**（页面已注册、表已存在）。写不存在的路径 = 埋一个只在点击时才暴露的 bug。

---

## 六、脚本结构模板

```sql
-- ============================================================
-- <编号>_<模块>.sql —— <一句话职责>
--
-- 幂等性：CREATE TABLE IF NOT EXISTS + 各级 information_schema 守卫
-- 结构真源：本文件 CREATE TABLE 段（旧环境用守卫式补列收敛，勿改定义）
-- 回滚：见文件末尾
-- ============================================================
USE xiaojietong;

-- 1. 建表（新环境）
CREATE TABLE IF NOT EXISTS `t` ( ... );

-- 2. 守卫式补列 / 回填（旧环境收敛）  ← 见 §三
-- 3. 索引（守卫判定后建）
-- 4. 种子（固定主键 + ODKU / 运营表用空更新）  ← 见 §五
-- 5. 自检（打印关键状态，便于人工核对）
SELECT CONCAT('t 行数 = ', COUNT(*)) AS `自检` FROM `t`;
```

---

## 七、提交前检查清单

- [ ] `CREATE TABLE` 定义**没有**为了迁就本机旧库而被修改（只允许新增表/新增列）
- [ ] 新增/重命名的列，都配了 `information_schema` 守卫 + 回填
- [ ] 旧列**没有**被直接 `DROP`
- [ ] 种子是**固定主键 + ODKU**（运营表用空更新），**没有** `WHERE NOT EXISTS`
- [ ] 种子里的跳转路径/外键/枚举**在代码库里真实存在**
- [ ] 在**至少两个不同形状的环境**各跑一遍（或明确说明只在单一环境验证过）
- [ ] 自检输出符合预期

---

## 八、附：本项目已发生的两次漂移（案例）

| PR | 发生了什么 | 教训 |
|---|---|---|
| `#76` | 为了让本机旧库的形状（`image_url`/`open_time`）跑通，直接改了 `CREATE TABLE` 定义 | 应改环境（守卫补列），不应改定义 |
| `#83` | 在本机形状基础上继续删列（含 `is_deleted`），造成第三种形状 | 删定义会让"真源"越来越小，且违背项目约定 |

**共同点**：都是在**单一环境**验证通过后就下了"真实库就是这样"的结论。
**纠正方式**：跨环境判断对错前，先确认**两边表形状是否一致**；不一致时，以**定义**为准去收敛环境。
