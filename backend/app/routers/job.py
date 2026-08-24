"""兼职实习：岗位 / 投递 / 可信度。

契约：docs/api.md §7
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge

router = APIRouter(prefix="/jobs", tags=["job"])


@router.get("")
def list_jobs(
    type: str = "",
    q: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    rows = cpp_bridge.job_dao().page_jobs(page, size, type, q)
    return ok(paged(rows, len(rows), page, size))


@router.get("/{job_id}")
def job_detail(job_id: int, user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT j.*, c.name AS company_name, c.credit_score FROM job_post j "
        "JOIN company c ON j.company_id = c.id WHERE j.id = ?",
        [job_id],
    )
    if not rows:
        raise BizError(1001, "岗位不存在")
    return ok(rows[0])


class ApplyIn(BaseModel):
    resume: str = ""


@router.post("/{job_id}/apply")
def apply_job(job_id: int, body: ApplyIn, user: dict = Depends(get_current_user)):
    try:
        application_id = cpp_bridge.job_dao().apply(
            int(user["id"]), job_id, body.resume
        )
    except RuntimeError:
        # UK(job_id, user_id) 冲突 → 重复投递
        raise BizError(3002, "您已投递过该岗位")
    return ok({"application_id": application_id})


@router.get("/applications/me")
def my_applications(user: dict = Depends(get_current_user)):
    rows = cpp_bridge.job_dao().my_applications(int(user["id"]))
    return ok({"items": rows})


@router.get("/{job_id}/trust")
def trust(job_id: int, user: dict = Depends(get_current_user)):
    score = cpp_bridge.job_dao().get_trust_score(job_id)
    rows = cpp_bridge.query(
        "SELECT risk_level, company_id FROM job_post WHERE id = ?", [job_id]
    )
    company_credit = 0
    if rows:
        cid = rows[0]["company_id"]
        cr = cpp_bridge.query("SELECT credit_score FROM company WHERE id = ?", [cid])
        company_credit = float(cr[0]["credit_score"]) if cr else 0
    return ok(
        {
            "job_id": job_id,
            "trust_score": score,
            "risk_level": int(rows[0]["risk_level"]) if rows else 0,
            "company_credit": company_credit,
        }
    )
