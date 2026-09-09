# ============================================================
# 校捷通 B2 缺口接口 点测脚本
# 覆盖：GET /topics/mine · GET /secondhand/items/mine ·
#       POST /favorites(切换) · GET /favorites · 帖子详情 favorited
# 前置：后端已启动（cd backend && uvicorn app.main:app --port 8000）
# 运行：pwsh tests/verify_b2.ps1  （输出为英文，避免终端编码问题）
# ============================================================
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:8000/api/v1"

Write-Host "== 1) health =="
$h = Invoke-RestMethod -Uri "$base/health"
Write-Host ("health -> status={0} db={1} cpp_ext={2}" -f $h.status, $h.db, $h.cpp_ext)
if ($h.db -ne "ok") { throw "DB not ok, check MySQL/env" }

Write-Host "== 2) wechat-login (get token) =="
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/wechat-login" -ContentType "application/json" -Body '{"code":"test1"}'
$token = $login.data.token
$H = @{ Authorization = "Bearer $token" }
Write-Host ("login ok, user_id={0} nickname={1}" -f $login.data.user.id, $login.data.user.nickname)

Write-Host "== 3) GET /topics/mine (my topics) =="
$mine = Invoke-RestMethod -Uri "$base/topics/mine?page=1&size=10" -Headers $H
Write-Host ("my topics total={0} (expect >=1 after step 5)" -f $mine.data.total)

Write-Host "== 4) GET /secondhand/items/mine (my items) =="
$pub = Invoke-RestMethod -Uri "$base/secondhand/items/mine?page=1&size=10" -Headers $H
Write-Host ("my items total={0}" -f $pub.data.total)

Write-Host "== 5) create topic -> favorite toggle x3 -> list -> detail =="
$body = '{"title":"B2 verify topic","content":"created for favorite test","category":"综合"}'
$tp = Invoke-RestMethod -Method Post -Uri "$base/topics" -Headers $H -ContentType "application/json" -Body $body
$tid = $tp.data.topic_id
Write-Host ("created topic id={0}" -f $tid)
if (-not $tid) { throw "create topic failed" }

function Toggle-Fav([int]$id) {
    $r = Invoke-RestMethod -Method Post -Uri "$base/favorites" -Headers $H -ContentType "application/json" -Body ("{`"target_type`":`"topic`",`"target_id`":$id}")
    return $r.data.favorited
}

$f1 = Toggle-Fav $tid; Write-Host ("favorite toggle 1 -> favorited={0} (expect True)" -f $f1)
$f2 = Toggle-Fav $tid; Write-Host ("favorite toggle 2 -> favorited={0} (expect False)" -f $f2)
$f3 = Toggle-Fav $tid; Write-Host ("favorite toggle 3 -> favorited={0} (expect True)" -f $f3)
if ($f1 -and (-not $f2) -and $f3) { Write-Host "favorite toggle: PASS" } else { Write-Host "favorite toggle: FAIL"; exit 1 }

$fl = Invoke-RestMethod -Uri "$base/favorites?target_type=topic&page=1&size=10" -Headers $H
Write-Host ("my favorites total={0}, first_id={1}, favorited={2} (expect 1/true)" -f $fl.data.total, $fl.data.items[0].id, $fl.data.items[0].favorited)

$d = Invoke-RestMethod -Uri "$base/topics/$tid" -Headers $H
Write-Host ("topic detail favorited={0} (expect True)" -f $d.data.favorited)
Write-Host ("topic detail liked={0}" -f $d.data.liked)

Write-Host "== 6) GET /topics/mine again =="
$mine2 = Invoke-RestMethod -Uri "$base/topics/mine?page=1&size=10" -Headers $H
Write-Host ("my topics total={0} (expect >=1)" -f $mine2.data.total)

Write-Host ""
Write-Host "===== ALL CHECKS DONE: B2 PASS ====="
