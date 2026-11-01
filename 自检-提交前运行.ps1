# 群忆 · 提交前自检（成员 C 前端）
# 用法：右键「使用 PowerShell 运行」，或在同目录执行  powershell -ExecutionPolicy Bypass -File .\自检-提交前运行.ps1
# 参数：-BackendDir  后端目录（含 qunyi 包），默认 ..\member-d\backend

param(
    [string]$BackendDir = "..\member-d\backend"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Line($t) { Write-Host ("`n== " + $t + " ==") -ForegroundColor Cyan }

Line "1/3 检查关键文件是否齐全"
$must = @(
    "index.html", "serve.py", "start.bat",
    "assets\styles.css", "assets\api.js", "assets\app.js",
    "assets\util.js", "assets\message-actions.js",
    "assets\views\overview.js", "assets\views\library.js", "assets\views\search.js",
    "assets\views\detail.js", "assets\views\groups.js", "assets\views\notifications.js",
    "assets\views\system.js",
    "tools\smoke_test.py"
)
$missing = @()
foreach ($f in $must) { if (-not (Test-Path (Join-Path $here $f))) { $missing += $f } }
if ($missing.Count -gt 0) {
    Write-Host ("[x] 缺少文件：" + ($missing -join ", ")) -ForegroundColor Red
    Write-Host "    少了 serve.py 必然跨域失败；少了任一 views/*.js 对应页面会白屏。" -ForegroundColor Yellow
    exit 1
}
Write-Host ("[ok] " + $must.Count + " 个关键文件都在") -ForegroundColor Green

Line "2/3 扫描不该提交的文件"
$bad = @()
$bad += Get-ChildItem $here -Recurse -File -Filter "credentials.json" -ErrorAction SilentlyContinue
$bad += Get-ChildItem $here -Recurse -File -Include "*.sqlite3","*.sqlite3-wal","*.sqlite3-shm" -ErrorAction SilentlyContinue
$bad += Get-ChildItem $here -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue
$bad += Get-ChildItem $here -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
if ($bad.Count -gt 0) {
    Write-Host "[x] 发现不应提交的内容：" -ForegroundColor Red
    $bad | ForEach-Object { Write-Host ("    " + $_.FullName) }
    Write-Host "    请删除后再提交（含令牌/数据库/缓存）。" -ForegroundColor Yellow
    exit 1
}
Write-Host "[ok] 没有令牌、数据库、缓存文件" -ForegroundColor Green

Line "3/3 联调自检（对接真实后端，45 项）"
if (-not (Test-Path $BackendDir)) {
    Write-Host ("[!] 找不到后端目录：" + $BackendDir) -ForegroundColor Yellow
    Write-Host "    先启动后端，再用 -BackendDir 指向含 qunyi 包的目录重跑。" -ForegroundColor Yellow
    Write-Host "    例： .\自检-提交前运行.ps1 -BackendDir D:\repo\member-d\backend" -ForegroundColor Yellow
    exit 1
}

# 前端服务若没起，这里临时起一个（跑完关掉）
$started = $null
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:5173/" -UseBasicParsing -TimeoutSec 2
    Write-Host "[ok] 前端服务已在 5173 运行" -ForegroundColor Green
} catch {
    Write-Host "[..] 前端服务未运行，临时启动一个" -ForegroundColor Yellow
    $started = Start-Process -FilePath "python" -ArgumentList "serve.py", "--port", "5173" -WorkingDirectory $here -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

try {
    python tools\smoke_test.py --backend-dir $BackendDir
    $code = $LASTEXITCODE
} finally {
    if ($started) { Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue }
}

if ($code -eq 0) {
    Write-Host "`n[通过] 可以提交。" -ForegroundColor Green
} else {
    Write-Host "`n[x] 自检有失败项，先修再提交。末尾的「可选接口可用性报告」全 404 属正常。" -ForegroundColor Red
}
exit $code
