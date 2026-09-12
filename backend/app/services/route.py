"""校园路网寻路（B13）：网格 A* 步行路径规划（无外部地图 API 依赖）。

**路网模型**
- 范围：全部 POI 与建筑的包围盒，外扩 150m；
- 网格：30m × 30m；
- 障碍：建筑视为不可通行（半径 45m 缓冲，避免路径"穿楼"）；
- 起终点吸附到最近可通行网格。

**算法**：A* 八方向搜索（代价 = 欧氏距离，对角 √2；启发函数 = 直线距离），
路径做共线压缩后输出拐点折线（经纬度）、路网距离、直线距离与步行时长（1.4 m/s）。

对比旧实现（起点取第一个 POI + 两点直线）：本实现给出绕开建筑的真实折线路径，
并返回 `straight_distance` 以便前端展示"绕行增量"。

网格分辨率 / 建筑缓冲半径 / 步行速度均可用常量调整，适配不同校区数据。
"""

from __future__ import annotations

import heapq
import math

from app.db import cpp_bridge

_GRID_M = 30.0        # 网格边长（米）
_MARGIN_M = 150.0     # 范围外扩（米）
_BUILDING_R_M = 45.0  # 建筑障碍缓冲半径（米）
_WALK_MPS = 1.4       # 步行速度（米/秒）
_EARTH_R = 6371000.0

# 八方向：(行增量, 列增量, 代价)
_DIRS = [
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, 1.41421), (-1, 1, 1.41421), (1, -1, 1.41421), (1, 1, 1.41421),
]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(a))


def _m_per_deg(lat: float) -> tuple[float, float]:
    """纬度向 / 经度向 每度对应的米数（局部平面近似）。"""
    m_lat = math.pi * _EARTH_R / 180.0
    m_lng = m_lat * math.cos(math.radians(lat))
    return m_lat, max(m_lng, 1.0)


def _nearest_free(blocked: list[list[bool]], cell: tuple[int, int], rows: int, cols: int) -> tuple[int, int]:
    """若目标格被占用，向外螺旋找最近可通行格。"""
    r, c = cell
    if not blocked[r][c]:
        return cell
    for radius in range(1, 12):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                rr, cc = r + dr, c + dc
                if 0 <= rr < rows and 0 <= cc < cols and not blocked[rr][cc]:
                    return (rr, cc)
    return cell


def _astar(
    blocked: list[list[bool]], start: tuple[int, int], goal: tuple[int, int], rows: int, cols: int
) -> list[tuple[int, int]]:
    """A* 搜索，返回网格路径（含起终点）；不可达返回空列表。"""
    if blocked[start[0]][start[1]] or blocked[goal[0]][goal[1]]:
        return []

    def h(r: int, c: int) -> float:
        return math.hypot(r - goal[0], c - goal[1])

    heap: list[tuple[float, float, tuple[int, int]]] = [(h(*start), 0.0, start)]
    best: dict[tuple[int, int], float] = {start: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}

    while heap:
        _, cost, cur = heapq.heappop(heap)
        if cur == goal:
            path = [cur]
            while cur in parent:
                cur = parent[cur]
                path.append(cur)
            return list(reversed(path))
        if cost > best.get(cur, math.inf):
            continue
        for dr, dc, w in _DIRS:
            nr, nc = cur[0] + dr, cur[1] + dc
            if not (0 <= nr < rows and 0 <= nc < cols) or blocked[nr][nc]:
                continue
            ng = cost + w
            if ng < best.get((nr, nc), math.inf):
                best[(nr, nc)] = ng
                parent[(nr, nc)] = cur
                heapq.heappush(heap, (ng + h(nr, nc), ng, (nr, nc)))
    return []


def _simplify_cells(path: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """共线压缩：仅保留方向发生变化的拐点。"""
    if len(path) <= 2:
        return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        d1 = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        d2 = (path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
        if d1 != d2:
            out.append(path[i])
    out.append(path[-1])
    return out


def plan_route(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> dict:
    """网格 A* 路网寻路。

    返回 ``{distance, straight_distance, duration, path:[{lat,lng}], algorithm}``；
    建筑把通路封死时回退为直线（algorithm=straight-fallback），不抛异常。
    """
    pois = cpp_bridge.query("SELECT id, latitude, longitude FROM poi")
    buildings = cpp_bridge.query("SELECT id, latitude, longitude FROM building")

    lats = [float(p["latitude"]) for p in pois + buildings] + [from_lat, to_lat]
    lngs = [float(p["longitude"]) for p in pois + buildings] + [from_lng, to_lng]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    m_lat, m_lng = _m_per_deg((min_lat + max_lat) / 2.0)
    min_lat -= _MARGIN_M / m_lat
    max_lat += _MARGIN_M / m_lat
    min_lng -= _MARGIN_M / m_lng
    max_lng += _MARGIN_M / m_lng

    rows = max(2, int(math.ceil((max_lat - min_lat) * m_lat / _GRID_M)) + 1)
    cols = max(2, int(math.ceil((max_lng - min_lng) * m_lng / _GRID_M)) + 1)

    def grid_of(lat: float, lng: float) -> tuple[int, int]:
        r = int(round((lat - min_lat) * m_lat / _GRID_M))
        c = int(round((lng - min_lng) * m_lng / _GRID_M))
        return max(0, min(rows - 1, r)), max(0, min(cols - 1, c))

    def latlng_of(r: int, c: int) -> tuple[float, float]:
        return (min_lat + r * _GRID_M / m_lat, min_lng + c * _GRID_M / m_lng)

    # 建筑障碍（圆形缓冲 → 网格占用）
    blocked = [[False] * cols for _ in range(rows)]
    rad = int(math.ceil(_BUILDING_R_M / _GRID_M))
    for b in buildings:
        br, bc = grid_of(float(b["latitude"]), float(b["longitude"]))
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                rr, cc = br + dr, bc + dc
                if 0 <= rr < rows and 0 <= cc < cols:
                    if math.hypot(dr * _GRID_M, dc * _GRID_M) <= _BUILDING_R_M:
                        blocked[rr][cc] = True

    start = _nearest_free(blocked, grid_of(from_lat, from_lng), rows, cols)
    goal = _nearest_free(blocked, grid_of(to_lat, to_lng), rows, cols)

    cells = _astar(blocked, start, goal, rows, cols)
    straight = _haversine(from_lat, from_lng, to_lat, to_lng)

    if not cells:
        path = [(from_lat, from_lng), (to_lat, to_lng)]
        return {
            "distance": round(straight),
            "straight_distance": round(straight),
            "duration": max(1, round(straight / _WALK_MPS / 60)),
            "path": [{"lat": round(a, 6), "lng": round(b, 6)} for a, b in path],
            "algorithm": "straight-fallback",
        }

    if len(cells) == 1:
        # 起终点吸附到同一网格（原地或极近距离）：返回两点直连，保证 path 至少 2 点
        path = [(from_lat, from_lng), (to_lat, to_lng)]
        return {
            "distance": round(straight),
            "straight_distance": round(straight),
            "duration": max(1, round(straight / _WALK_MPS / 60)),
            "path": [{"lat": round(a, 6), "lng": round(b, 6)} for a, b in path],
            "algorithm": "astar-grid",
        }

    cells = _simplify_cells(cells)
    points = [latlng_of(r, c) for r, c in cells]
    points[0] = (from_lat, from_lng)   # 端点使用真实坐标（网格吸附会偏移）
    points[-1] = (to_lat, to_lng)
    distance = sum(
        _haversine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )
    return {
        "distance": round(distance),
        "straight_distance": round(straight),
        "duration": max(1, round(distance / _WALK_MPS / 60)),
        "path": [{"lat": round(a, 6), "lng": round(b, 6)} for a, b in points],
        "algorithm": "astar-grid",
    }
