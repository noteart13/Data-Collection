param(
    [ValidateRange(1, 16)]
    [int]$Workers = 4,

    [string]$State = "ACT",

    [string]$Status = "sold",

    [string]$Python = "python",

    [switch]$Wait
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonScript = Join-Path $projectRoot "app\get_url_with_suburb.py"

if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
    throw "Python script not found: $pythonScript"
}

if ($Python -eq "python") {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $Python = $venvPython
    }
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$processes = @()

Write-Host "================================================================"
Write-Host "   KHỞI CHẠY $Workers WORKERS QUÉT URL SONG SONG BANG $State ($Status)"
Write-Host "================================================================"

for ($workerId = 0; $workerId -lt $Workers; $workerId++) {
    $stdout = Join-Path $logDir "worker_url_$workerId.log"
    $stderr = Join-Path $logDir "worker_url_$workerId.error.log"
    $arguments = @(
        "-u"
        ('"{0}"' -f $pythonScript)
        "--state"
        $State
        "--status"
        $Status
        "--worker-id"
        $workerId
        "--worker-count"
        $Workers
    )

    $process = Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    $processes += $process
    Write-Host "Started URL Worker $workerId/$Workers (PID $($process.Id))"
}

Write-Host "Log files: logs\worker_url_*.log"
Write-Host "Quá trình quét đang chạy nền song song..."
Write-Host "Để gộp các file worker CSV sau khi hoàn thành, chạy: python app\get_url_with_suburb.py --state $State --status $Status --worker-count $Workers --merge"

if ($Wait) {
    Write-Host "Đang chờ các workers hoàn tất..."
    $processes | Wait-Process
    Write-Host "Tất cả workers đã xong! Đang gộp dữ liệu thành file URLs duy nhất..."
    & $pythonCommand.Source $pythonScript --state $State --status $Status --worker-count $Workers --merge
}
