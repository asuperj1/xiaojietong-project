"""校园地图：POI / 周边 / 导航 / 建筑详情。

契约：docs/api.md §9
导航（B13）：网格 A* 路网寻路，见 services/route.py（不再用两点直线占位）。
"""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, ok
from app.db import cpp_bridge
from app.services.route import plan_route

router = APIRouter(prefix="/map", tags=["map"])

EARTH_R = 6371000.0  # 地球半径(米)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


@router.get("/pois")
def pois(category: str = "", user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT id, name, category, latitude, longitude, floor FROM poi "
        "WHERE (? = '' OR category = ?) ORDER BY category, name",
        [category, category],
    )
    return ok({"items": rows})


@router.get("/nearby")
def nearby(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: int = Query(500, ge=1, le=5000),
    user: dict = Depends(get_current_user),
):
    rows = cpp_bridge.query("SELECT id, name, category, latitude, longitude FROM poi")
    items = []
    for r in rows:
        d = _haversine(lat, lng, float(r["latitude"]), float(r["longitude"]))
        if d <= radius:
            r["distance"] = round(d)
            items.append(r)
    items.sort(key=lambda x: x["distance"])
    return ok({"items": items})


class NavigateIn(BaseModel):
    to_poi_id: int
    from_lat: float | None = None
    from_lng: float | None = None


@router.post("/navigate")
def navigate(body: NavigateIn, user: dict = Depends(get_current_user)):
    """步行导航（B13）：网格 A* 路网寻路，绕开建筑，返回折线路径与距离/时长。

    起点优先取传入坐标（用户定位）；缺省时取**距目标最近的 POI** 作为校园地标锚点
    （响应 `start_source` 标明来源）。旧实现「起点取第一个 POI + 两点直线」已移除。
    """
    rows = cpp_bridge.query("SELECT * FROM poi WHERE id = ?", [body.to_poi_id])
    if not rows:
        raise BizError(1001, "目标点位不存在")
    target = rows[0]
    to_lat, to_lng = float(target["latitude"]), float(target["longitude"])

    if body.from_lat is not None and body.from_lng is not None:
        from_lat, from_lng = float(body.from_lat), float(body.from_lng)
        start_source = "user_location"
        start_poi_id = 0  # navigation_log.from_poi_id 为 NOT NULL，0 表示定位起点
    else:
        pois = cpp_bridge.query("SELECT id, name, latitude, longitude FROM poi")
        if not pois:
            raise BizError(1001, "校园 POI 数据为空，无法规划路线")
        nearest = min(
            pois,
            key=lambda p: _haversine(
                to_lat, to_lng, float(p["latitude"]), float(p["longitude"])
            ),
        )
        from_lat, from_lng = float(nearest["latitude"]), float(nearest["longitude"])
        start_source = "nearest_poi"
        start_poi_id = int(nearest["id"])

    route = plan_route(from_lat, from_lng, to_lat, to_lng)
    cpp_bridge.execute(
        "INSERT INTO navigation_log (user_id, from_poi_id, to_poi_id, path_json) "
        "VALUES (?, ?, ?, ?)",
        [
            int(user["id"]),
            start_poi_id,
            body.to_poi_id,
            json.dumps(route["path"], ensure_ascii=False),
        ],
    )
    return ok(
        {
            "distance": route["distance"],
            "duration": route["duration"],
            "path": route["path"],
            "straight_distance": route["straight_distance"],
            "algorithm": route["algorithm"],
            "start_source": start_source,
            "target": {"id": int(target["id"]), "name": target["name"]},
        }
    )


@router.get("/building/{building_id}")
def building_detail(building_id: int, user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query("SELECT * FROM building WHERE id = ?", [building_id])
    if not rows:
        raise BizError(1001, "建筑不存在")
    b = rows[0]
    rooms = cpp_bridge.query(
        "SELECT id, floor, name, room_type, capacity, has_power FROM room "
        "WHERE building_id = ? ORDER BY floor, id",
        [building_id],
    )
    b["floor_plan"] = rooms
    # 聚合各层拥挤度（示例：从 occupancy_record 最近均值）
    b["services"] = ["library", "study"] if "图书" in b["name"] else ["study"]
    return ok(b)
