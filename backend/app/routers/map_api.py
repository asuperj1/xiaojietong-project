"""校园地图：POI / 周边 / 导航 / 建筑详情。

契约：docs/api.md §9
"""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, ok
from app.db import cpp_bridge

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
    rows = cpp_bridge.query("SELECT * FROM poi WHERE id = ?", [body.to_poi_id])
    if not rows:
        raise BizError(1001, "目标点位不存在")
    target = rows[0]
    # 占位：起点缺省取第一个 POI；真实路径规划接入地图 SDK/路网
    start = cpp_bridge.query("SELECT * FROM poi ORDER BY id LIMIT 1")[0]
    distance = _haversine(
        float(start["latitude"]), float(start["longitude"]),
        float(target["latitude"]), float(target["longitude"]),
    )
    path = [
        {"lat": float(start["latitude"]), "lng": float(start["longitude"])},
        {"lat": float(target["latitude"]), "lng": float(target["longitude"])},
    ]
    cpp_bridge.execute(
        "INSERT INTO navigation_log (user_id, from_poi_id, to_poi_id, path_json) "
        "VALUES (?, ?, ?, ?)",
        [int(user["id"]), int(start["id"]), body.to_poi_id, json.dumps(path)],
    )
    return ok({"distance": round(distance), "duration": round(distance / 1.4 / 60), "path": path})


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
