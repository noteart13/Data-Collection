param(
    [ValidateRange(1, 64)]
    [int]$Workers = 4,

    [string]$InputCsv = "",

    [string]$Collection = "PropertyMaster",

    [string]$LocalUrl = "http://34.69.4.94:6333",

    [string]$LocalKey = "zyrQ1ydww1NPDNsP01o4NNAMgDzGK7m0",

    [string]$Python = "python",

    [int]$Limit = 0,

    [switch]$Apply,

    [switch]$Headed,

    [switch]$Force,

    [switch]$Wait
)

$ErrorActionPreference = "Stop"
$projectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$pythonScript = Join-Path $projectRoot "enrich_local_master.py"

if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
    throw "Python script not found: $pythonScript"
}

if ($InputCsv -and (-not [System.IO.Path]::IsPathRooted($InputCsv))) {
    $InputCsv = Join-Path $projectRoot $InputCsv
}

if ($InputCsv -and (-not (Test-Path -LiteralPath $InputCsv -PathType Leaf))) {
    throw "Input CSV file not found: $InputCsv"
}

$pythonCommand = (Get-Command $Python -ErrorAction SilentlyContinue).Source
if (-not $pythonCommand) { $pythonCommand = "python" }

$processes = @()

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "🚀 KHỞI CHẠY $Workers WORKERS LÀM GIÀU DỮ LIỆU BẤT ĐỘNG SẢN ÚC" -ForegroundColor Cyan
Write-Host "• Endpoint Qdrant: $LocalUrl"
Write-Host "• Collection:      $Collection"
if ($InputCsv) {
    Write-Host "• Nguồn dữ liệu:   File CSV ($InputCsv)"
} else {
    Write-Host "• Nguồn dữ liệu:   Trực tiếp từ Qdrant Collection"
}
Write-Host "• Chế độ ghi:      $($(if ($Apply) {'[GHI THẬT VÀO DATABASE (--apply)]'} else {'[DRY-RUN (CHỈ ĐỌC)]'}))"
Write-Host "• Hiển thị Chrome: $($(if ($Headed) {'[BẬT CỬA SỔ (--headed)]'} else {'[CHẠY NGẦM HEADLESS]'}))"
Write-Host "================================================================================" -ForegroundColor Cyan

for ($workerId = 0; $workerId -lt $Workers; $workerId++) {
    $stdout = Join-Path $projectRoot "worker_$workerId.log"
    $stderr = Join-Path $projectRoot "worker_$workerId.error.log"
    $outputFile = Join-Path $projectRoot "enriched_worker_$workerId.jsonl"

    $arguments = @(
        "-u",
        $pythonScript,
        "--worker-id", $workerId,
        "--worker-count", $Workers,
        "--collection", $Collection,
        "--local-url", $LocalUrl,
        "--local-key", $LocalKey,
        "--output-file", $outputFile,
        "--limit", $Limit,
        "--uc"
    )

    if ($InputCsv) {
        $arguments += @("--input-csv", $InputCsv)
    }

    if ($Apply) {
        $arguments += "--apply"
    }

    if ($Headed) {
        $arguments += "--headed"
    }

    if ($Force) {
        $arguments += "--force"
    }

    $process = Start-Process `
        -FilePath $pythonCommand `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    $processes += $process
    Write-Host "✅ Đã khởi động Worker $workerId/$Workers (PID $($process.Id))" -ForegroundColor Green
    Write-Host "   • Log tiến độ:   worker_$workerId.log"
    Write-Host "   • File kết quả:  enriched_worker_$workerId.jsonl"
}

Write-Host "`n📊 TẤT CẢ $Workers WORKERS ĐANG CHẠY SONG SONG TRONG NỀN." -ForegroundColor Yellow
Write-Host "• Theo dõi log worker 0:  Get-Content worker_0.log -Wait" -ForegroundColor Gray
Write-Host "• Kiểm tra file kết quả:  Get-Item enriched_worker_*.jsonl" -ForegroundColor Gray

if ($Wait) {
    Write-Host "`nĐang chờ tất cả workers hoàn tất..." -ForegroundColor Cyan
    $processes | Wait-Process
    Write-Host "🎉 Tất cả $Workers workers đã hoàn tất công việc!" -ForegroundColor Green
}
