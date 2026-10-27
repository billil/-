# 入仓前自检（解压后双击运行）

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  群忆 frontend-c · 入仓前自检" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $here

# 1) 文件数量与关键文件
$files = Get-ChildItem -Path $here -Recurse -File
Write-Host "[1/3] 文件清单：共 $($files.Count) 个文件" -ForegroundColor Yellow
$files | ForEach-Object { "      " + $_.FullName.Replace($here + "\", "") }

$must = @(
  "index.html", "serve.py",
  "assets\styles.css", "assets\api.js", "assets\app.js", "assets\util.js",
  "assets\message-actions.js",
  "assets\views\overview.js", "assets\views\library.js", "assets\views\search.js",
  "assets\views\detail.js", "assets\views\groups.js", "assets\views\notifications.js",
  "assets\views\system.js", "tools\smoke_test.py"
)
$missing = @()
foreach ($m in $must) { if (-not (Test-Path (Join-Path $here $m))) { $missing += $m } }
if ($missing.Count -eq 0) {
  Write-Host "      OK  关键文件齐全（15 个必需项都在）" -ForegroundColor Green
} else {
  Write-Host "      FAIL  缺文件：$($missing -join ', ')" -ForegroundColor Red
}
Write-Host ""

# 2) 敏感文件检查
Write-Host "[2/3] 敏感数据检查（应全部为「未发现」）" -ForegroundColor Yellow
$bad = Get-ChildItem -Path $here -Recurse -File | Where-Object {
  $_.Name -eq "credentials.json" -or
  $_.Name -like "*.sqlite3*" -or
  $_.Name -like "*.pyc" -or
  $_.DirectoryName -like "*\__pycache__*" -or
  $_.DirectoryName -like "*\state*"
}
if ($bad.Count -eq 0) {
  Write-Host "      未发现令牌 / 数据库 / 缓存文件" -ForegroundColor Green
} else {
  Write-Host "      发现不该入仓的文件，请删除后再打包：" -ForegroundColor Red
  $bad | ForEach-Object { Write-Host ("        " + $_.FullName) -ForegroundColor Red }
}
Write-Host ""

# 3) 联调自检
Write-Host "[3/3] 联调自检（45 项）" -ForegroundColor Yellow
Write-Host "      需要：后端已在 127.0.0.1:8000 运行，且前端 serve.py 已在 5173 运行。" -ForegroundColor DarkGray
$backend = Join-Path (Split-Path -Parent $here) "member-d\backend"
if (-not (Test-Path $backend)) { $backend = "..\member-d\backend" }

python "tools\smoke_test.py" --backend-dir $backend
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
  Write-Host "结论：可以入仓。把整个 frontend-c 目录提交到仓库根目录。" -ForegroundColor Green
} else {
  Write-Host "结论：自检未通过（退出码 $code）。先看上面标 FAIL 的项，或确认后端/前端已启动。" -ForegroundColor Red
}
Write-Host ""
pause
