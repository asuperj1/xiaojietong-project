"""图书馆：空教室 / 座位 / 预约 / 拥挤度。

契约：docs/api.md §5
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok
from app.db import cpp_bridge

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/free-rooms")
def free_rooms(
    campus: str = "",
    floor: str = "",
    period: str = "",
    user: dict = Depends(get_current_user),
):
    rooms = cpp_bridge.library_dao().find_free_rooms(campus, floor, period)
    return ok({"items": rooms, "total": len(rooms), "page": 1, "size": len(rooms)})


@router.get("/rooms/{room_id}/seats")
def room_seats(
    room_id: int,
    date: str = Query(...),
    user: dict = Depends(get_current_user),
):
    seats = cpp_bridge.library_dao().find_seats(room_id, date)
    return ok({"items": seats})


class ReserveIn(BaseModel):
    seat_id: int
    date: str
    begin_time: str
    end_time: str


@router.post("/reservations")
def reserve(body: ReserveIn, user: dict = Depends(get_current_user)):
    uid = int(user["id"])
    if body.begin_time >= body.end_time:
        raise err_param("结束时间需晚于开始时间")

    with cpp_bridge.begin():
        # 冲突校验：同座位同日期、状态有效且时段重叠
        rows = cpp_bridge.query(
            "SELECT COUNT(*) AS c FROM seat_reservation "
            "WHERE seat_id = ? AND reserve_date = ? AND status IN (0,1) "
            "  AND begin_time < ? AND end_time > ?",
            [body.seat_id, body.date, body.end_time, body.begin_time],
        )
        if int(rows[0]["c"]) > 0:
            raise BizError(3001, "该座位此时段已被预约")
        reservation_id = cpp_bridge.library_dao().reserve(
            body.seat_id, uid, body.date, body.begin_time, body.end_time
        )
    return ok({"reservation_id": reservation_id})


@router.get("/reservations/me")
def my_reservations(user: dict = Depends(get_current_user)):
    rows = cpp_bridge.library_dao().my_reservations(int(user["id"]))
    return ok({"items": rows})


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, user: dict = Depends(get_current_user)):
    ok_flag = cpp_bridge.library_dao().cancel_reservation(reservation_id, int(user["id"]))
    if not ok_flag:
        raise BizError(3001, "取消失败：预约不存在或不可取消")
    return ok({"reservation_id": reservation_id, "status": 2})


@router.get("/rooms/{room_id}/occupancy")
def occupancy(
    room_id: int,
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(get_current_user),
):
    history = cpp_bridge.library_dao().occupancy_history(room_id, days)
    prediction = cpp_bridge.library_dao().predict_occupancy(room_id, "")
    return ok({"room_id": room_id, "history": history, "prediction": prediction})
