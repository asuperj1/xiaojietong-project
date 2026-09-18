"""B31 代收业务闭环：下单生成取件码 → 到件登记 → 站内通知。

产品边界（任务单 §7.1，产品方已确认）：

| 要素 | 本期最小可用 | 预留（下期） |
|---|---|---|
| 取件码 | 下单生成 **6 位**，详情页大字展示 | 二维码 / 条形码核销 |
| 驿站 | **固定取件点单选**（`pickup_point`） | 多驿站、按距离推荐 |
| 到件通知 | **复用站内通知**（`notice_delivery`） | 微信订阅消息 / 短信 |
| 费用 | **不计费**（字段预留） | — |

> 本期边界：取件码 + 驿站单选 + 站内到件通知 + 不计费；**核销（扫码/输码）列入下期**。

`biz_type`（见 `db/sql/18_takeaway_pickup.sql`）：1 = 代买（历史数据），2 = 代收（本期起默认）。

⚠️ **`status` 列是代买（外卖）形状的枚举**（0待支付/1已支付/2配送中/3已完成/4已取消）。
代收**不计费**，故按下面的语义映射复用（改枚举要动表，列入下期）：

    1 已受理（下单成功，待驿站揽收） → 2 已到站（`arrived_at` 已写，待取件） → 3 已取件（核销，下期）

⚠️ **不引用 `pickup_point.is_deleted`**：该列只存在于 `18_` 的 `CREATE TABLE` 定义里，
早期建过表的库（`CREATE IF NOT EXISTS` 直接跳过）没有它 —— 引用了就是 `ERROR 1054`。
本期以 `enabled` 为准；列的收敛按《数据库迁移规范》§一 的「守卫式补列」另行处理。

契约：`docs/api.md` §9。
"""

from __future__ import annotations

import secrets

from app.core.response import BizError, err_biz, err_server
from app.db import cpp_bridge
from app.services import notice_scheduler

#: 取件码位数（产品方确认：6 位）
PICKUP_CODE_LEN = 6
#: 取件码冲突时的重试次数（6 位数字共 90 万种，本量级下重试几次足够）
_CODE_MAX_TRY = 20

#: 代收业务类型（1 = 代买历史 / 2 = 代收）
BIZ_TYPE_TAKEAWAY = 2

#: 代收 status 语义（见模块 docstring 的映射表）
STATUS_ACCEPTED = 1
STATUS_ARRIVED = 2


def pickup_points() -> list[dict]:
    """可选驿站（固定取件点单选）：仅启用中、未删除的，按 `sort` 倒序。

    ⚠️ **列名以 `db/sql/18_takeaway_pickup.sql` 的权威定义为准**（PR #88 收敛后）：
    `business_hours` / `status` / `is_deleted`。漂移期的 `open_time` / `enabled` / `campus`
    会被该脚本**回填后 DROP**（`campus` 有数据时保留并警告），因此这里**一律不引用** ——
    引用了就会在"全新库"上报 `ERROR 1054`。
    """
    return cpp_bridge.query(
        "SELECT `id`, `name`, `address`, `business_hours`, `contact_phone`, `sort` "
        "FROM `pickup_point` WHERE `status` = 1 AND `is_deleted` = 0 "
        "ORDER BY `sort` DESC, `id` ASC",
        [],
    )


def _gen_pickup_code() -> str:
    """生成 6 位数字取件码；与库内已有码冲突则重试（避免回查歧义）。"""
    for _ in range(_CODE_MAX_TRY):
        code = "".join(secrets.choice("0123456789") for _ in range(PICKUP_CODE_LEN))
        rows = cpp_bridge.query(
            "SELECT `id` FROM `takeaway_order` WHERE `pickup_code` = ? LIMIT 1", [code]
        )
        if not rows:
            return code
    raise err_server("取件码生成失败，请稍后重试")


def _point_or_raise(pickup_point_id: int) -> dict:
    rows = cpp_bridge.query(
        "SELECT `id`, `name`, `address`, `business_hours` FROM `pickup_point` "
        "WHERE `id` = ? AND `status` = 1 AND `is_deleted` = 0",
        [int(pickup_point_id)],
    )
    if not rows:
        raise BizError(1001, "取件驿站不存在或已停用")
    return rows[0]


def create_order(
    user_id: int,
    pickup_point_id: int,
    address: str = "",
    contact: str = "",
    contact_phone: str = "",
    remark: str = "",
) -> dict:
    """代收下单：生成 6 位取件码，**不计费**。返回 `{order_id, pickup_code, ...}`。"""
    point = _point_or_raise(pickup_point_id)
    code = _gen_pickup_code()
    _, order_id = cpp_bridge.execute(
        "INSERT INTO `takeaway_order` (`user_id`, `merchant_id`, `items_json`, `total_amount`, "
        "`delivery_fee`, `pay_amount`, `status`, `address`, `contact`, `contact_phone`, `remark`, "
        "`biz_type`, `pickup_code`, `pickup_point_id`) "
        "VALUES (?, 0, '[]', 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            int(user_id),
            STATUS_ACCEPTED,
            address,
            contact,
            contact_phone,
            remark,
            BIZ_TYPE_TAKEAWAY,
            code,
            int(point["id"]),
        ],
    )
    return {
        "order_id": int(order_id),
        "pickup_code": code,
        "pickup_point": {"id": int(point["id"]), "name": point["name"], "address": point["address"]},
        "status": STATUS_ACCEPTED,
        "pay_amount": 0,
    }


def order_view(row: dict) -> dict:
    """订单对外结构：补驿站名 + 取件码/时间线（详情与列表共用）。"""
    view = dict(row)
    point_id = int(view.get("pickup_point_id") or 0)
    view["biz_type"] = int(view.get("biz_type") or 0)
    view["pickup_point_name"] = ""
    view["pickup_point_address"] = ""
    if point_id:
        rows = cpp_bridge.query(
            "SELECT `name`, `address` FROM `pickup_point` WHERE `id` = ?", [point_id]
        )
        if rows:
            view["pickup_point_name"] = rows[0]["name"]
            view["pickup_point_address"] = rows[0]["address"]
    view["pickup_code"] = view.get("pickup_code") or ""
    view["arrived_at"] = view.get("arrived_at") or ""
    view["notified_at"] = view.get("notified_at") or ""
    return view


def arrive(order_id: int, operator_id: int = 0) -> dict:
    """到件登记（驿站/运营）：写 `arrived_at` 并给下单人发**一条**站内通知。

    幂等：`notified_at` 非空即**不再重复投递**（重跑只回报当前状态）；
    `arrived_at` 取首次登记时间（`COALESCE`），便于追溯。
    订单更新与通知投递在**同一事务**内 —— 要么都成，要么都不成。

    `operator_id` 仅用于日志与审计语义，不写库（本期无驿站账号体系）。
    """
    rows = cpp_bridge.query(
        "SELECT `id`, `user_id`, `pickup_code`, `biz_type`, `pickup_point_id`, "
        "`arrived_at`, `notified_at` FROM `takeaway_order` WHERE `id` = ?",
        [int(order_id)],
    )
    if not rows:
        raise BizError(1001, "订单不存在")
    order = rows[0]
    if int(order.get("biz_type") or 0) != BIZ_TYPE_TAKEAWAY:
        raise err_biz("仅代收订单支持到件登记")

    already_notified = bool(order.get("notified_at"))
    code = order.get("pickup_code") or ""
    point = _point_or_raise(int(order.get("pickup_point_id") or 0)) if order.get(
        "pickup_point_id"
    ) else {"name": "取件驿站", "address": "", "business_hours": ""}

    with cpp_bridge.begin():
        cpp_bridge.execute(
            "UPDATE `takeaway_order` SET `arrived_at` = COALESCE(`arrived_at`, CURRENT_TIMESTAMP), "
            "`notified_at` = COALESCE(`notified_at`, CURRENT_TIMESTAMP), `status` = ? WHERE `id` = ?",
            [STATUS_ARRIVED, int(order_id)],
        )
        if not already_notified:
            hours = (
                f"，营业时间 {point['business_hours']}" if point.get("business_hours") else ""
            )
            notice_scheduler.push_to_user(
                kind="takeaway",
                ref_id=int(order_id),
                stage="arrived",
                title=f"包裹已到站：{point['name']}",
                content=(
                    f"取件码 {code}。请到 {point['name']}"
                    f"（{point.get('address') or '地址见驿站信息'}）凭码取件{hours}。"
                ),
                user_id=int(order["user_id"]),
                score=3.0,
                reason="代收包裹到件",
            )

    return {
        "order_id": int(order_id),
        "pickup_code": code,
        "status": STATUS_ARRIVED,
        "notified": not already_notified,
        "operator_id": int(operator_id or 0),
    }
