param(
    [ValidateRange(1, 64)]
    [int]$Workers = 4,

    [string]$InputCsv = "urls.csv",

    [string]$Python = "python",

    [string]$CacheDir = ".cache\scraper",

    [ValidateRange(16, 4096)]
    [int]$BrowserCacheMB = 256,

    [string]$Collection = "",

    [switch]$Restart,

    [string]$CheckpointDb = "",

    [switch]$Wait
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonScript = Join-Path $projectRoot "app\get_info_from_html.py"

if (-not [System.IO.Path]::IsPathRooted($CacheDir)) {
    $CacheDir = Join-Path $projectRoot $CacheDir
}
$CacheDir = [System.IO.Path]::GetFullPath($CacheDir)
$browserProfileDir = Join-Path $CacheDir "nodriver"
$tempDir = Join-Path $CacheDir "temp"
New-Item -ItemType Directory -Force -Path $browserProfileDir, $tempDir | Out-Null

# Child workers inherit these values. This keeps browser profiles, HTTP caches,
# and Python/native temporary files off the Windows system drive.
$env:NODRIVER_CACHE_DIR = $browserProfileDir
$env:NODRIVER_DISK_CACHE_MB = $BrowserCacheMB.ToString()
$env:CRAWL4_AI_BASE_DIRECTORY = $CacheDir
$env:TEMP = $tempDir
$env:TMP = $tempDir

if (-not [System.IO.Path]::IsPathRooted($InputCsv)) {
    $InputCsv = Join-Path $projectRoot $InputCsv
}

if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
    throw "Python script not found: $pythonScript"
}

if (-not (Test-Path -LiteralPath $InputCsv -PathType Leaf)) {
    throw "Input CSV not found: $InputCsv"
}

if ($Python -eq "python") {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $Python = $venvPython
    }
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$processes = @()

for ($workerId = 0; $workerId -lt $Workers; $workerId++) {
    $stdout = Join-Path $projectRoot "worker_$workerId.log"
    $stderr = Join-Path $projectRoot "worker_$workerId.error.log"
    $arguments = @(
        "-u"
        ('"{0}"' -f $pythonScript)
        "--input-csv"
        ('"{0}"' -f $InputCsv)
        "--worker-id"
        $workerId
        "--worker-count"
        $Workers
        "--batch-size"
        1
        "--mapping-concurrency"
        1
    )

    if ($Collection) {
        $arguments += @("--collection", $Collection)
    }
    if ($Restart) {
        $arguments += "--restart"
    }
    if ($CheckpointDb) {
        $arguments += @("--checkpoint-db", ('"{0}"' -f $CheckpointDb))
    }

    $process = Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    $processes += $process
    Write-Host "Started worker $workerId/$Workers (PID $($process.Id))"
}

Write-Host "Progress checkpoint: logs\get_info_from_html.sqlite3"
if ($Collection) {
    Write-Host "Target Qdrant Collection: $Collection"
}
Write-Host "Process output: worker_*.log and worker_*.error.log"
Write-Host "Worker cache: $CacheDir (Chrome disk cache: ${BrowserCacheMB} MB/worker)"

if ($Wait) {
    $processes | Wait-Process
    foreach ($process in $processes) {
        $process.Refresh()
        Write-Host "Worker process $($process.Id) exited with code $($process.ExitCode)"
    }
}
