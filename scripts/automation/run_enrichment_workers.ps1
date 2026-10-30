param(
    [ValidateRange(1, 16)]
    [int]$Workers = 4,

    [int]$Limit = 0,

    [switch]$All,

    [switch]$AllMaster,

    [switch]$ReEnrich,

    [string]$Suburb = "",

    [string]$State = "NSW",

    [string]$Url = "",

    [switch]$NoQdrant,

    [string]$Python = "",

    [switch]$Status,

    [switch]$Stop,

    [switch]$Wait
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$scriptPath = Join-Path $projectRoot "scratch\run_address_first_enrichment.py"
$logsDir = Join-Path $projectRoot "logs"

if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
}

# ---------------------------------------------------------------------------
# Utility: STOP Active Workers
# ---------------------------------------------------------------------------
if ($Stop) {
    Write-Host "===========================================================================" -ForegroundColor Yellow
    Write-Host " STOPPING ALL ACTIVE ENRICHMENT WORKERS" -ForegroundColor Yellow
    Write-Host "===========================================================================" -ForegroundColor Yellow

    $procs = Get-CimInstance Win32_Process | Where-Object { 
        $_.CommandLine -like "*run_address_first_enrichment.py*" -and $_.ProcessId -ne $PID 
    }

    if ($procs) {
        foreach ($p in $procs) {
            Write-Host " Terminating PID $($p.ProcessId)..." -ForegroundColor Yellow
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Write-Host " All worker processes stopped." -ForegroundColor Green
    } else {
        Write-Host " No running enrichment workers detected." -ForegroundColor Cyan
    }
    return
}

# ---------------------------------------------------------------------------
# Utility: CHECK STATUS
# ---------------------------------------------------------------------------
if ($Status) {
    Write-Host "===========================================================================" -ForegroundColor Cyan
    Write-Host " ENRICHMENT WORKERS STATUS MONITOR" -ForegroundColor Cyan
    Write-Host "===========================================================================" -ForegroundColor Cyan

    $procs = Get-CimInstance Win32_Process | Where-Object { 
        $_.CommandLine -like "*run_address_first_enrichment.py*" -and $_.ProcessId -ne $PID 
    }

    if ($procs) {
        Write-Host "Active Worker Processes:" -ForegroundColor Green
        foreach ($p in $procs) {
            $memMb = [math]::Round((Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue).WorkingSet64 / 1MB, 1)
            Write-Host "  - PID $($p.ProcessId) | Memory: ${memMb} MB | Command: $($p.CommandLine.Substring(0, [math]::Min(90, $p.CommandLine.Length)))..."
        }
    } else {
        Write-Host "No active worker processes running." -ForegroundColor Yellow
    }

    Write-Host "`nCheckpoint Files:" -ForegroundColor Cyan
    $checkpointFiles = Get-ChildItem -Path (Join-Path $projectRoot "scratch") -Filter "pilot_enrichment_checkpoint*.jsonl" -ErrorAction SilentlyContinue
    $totalRecords = 0
    $totalEnriched = 0
    foreach ($f in $checkpointFiles) {
        $lines = (Get-Content $f.FullName -ErrorAction SilentlyContinue | Where-Object { $_.Trim() -ne "" })
        $enrichedInFile = ($lines | Select-String '"status": "enriched"').Count
        $totalRecords += $lines.Count
        $totalEnriched += $enrichedInFile
        Write-Host ("  - {0,-45} : {1,4} records processed ({2,3} enriched)" -f $f.Name, $lines.Count, $enrichedInFile)
    }
    $pct = if ($totalRecords -gt 0) { [math]::Round(($totalEnriched / $totalRecords) * 100, 1) } else { 0 }
    $remaining = [math]::Max(0, 988 - $totalRecords)
    Write-Host ("`nTotal Scanned: {0} | Fully Enriched: {1} ({2}% Success Rate)" -f $totalRecords, $totalEnriched, $pct) -ForegroundColor Green
    Write-Host ("Pilot Dataset Remaining: {0} of 988 properties" -f $remaining) -ForegroundColor Cyan
    Write-Host "===========================================================================" -ForegroundColor Cyan
    return
}

# ---------------------------------------------------------------------------
# LAUNCH WORKERS
# ---------------------------------------------------------------------------
if (-not $Python) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $Python = $venvPython
    } else {
        $Python = "python"
    }
}

if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Target Python script not found: $scriptPath"
}

Write-Host "===========================================================================" -ForegroundColor Cyan
Write-Host " LAUNCHING MULTI-WORKER ADDRESS-FIRST ENRICHMENT ENGINE" -ForegroundColor Cyan
Write-Host " Workers: $Workers parallel processes | Python: $Python" -ForegroundColor Cyan
if ($Limit -gt 0) {
    Write-Host " Limit per worker: $Limit items" -ForegroundColor Cyan
} else {
    Write-Host " Limit: Unlimited (all assigned partitioned items)" -ForegroundColor Cyan
}
if ($ReEnrich) {
    Write-Host " Source: RE-ENRICH (Re-processing saved URLs with full hydration)" -ForegroundColor Magenta
} elseif ($AllMaster) {
    Write-Host " Source: QDRANT STREAMING (All 5.3M properties in PropertyMaster)" -ForegroundColor Cyan
} elseif ($Suburb) {
    Write-Host " Source: QDRANT STREAMING (Suburb: $Suburb)" -ForegroundColor Cyan
} else {
    Write-Host " Source: PILOT DATASET (scratch\pilot_1000_nsw_residential.json)" -ForegroundColor Cyan
}
if ($NoQdrant) {
    Write-Host " Mode: DRY RUN (Qdrant write disabled)" -ForegroundColor Yellow
} else {
    Write-Host " Mode: LIVE SYNC -> Qdrant collection 'PropertyMaster'" -ForegroundColor Green
}
Write-Host "===========================================================================" -ForegroundColor Cyan

$processes = @()

for ($workerId = 0; $workerId -lt $Workers; $workerId++) {
    $stdout = Join-Path $logsDir "enrich_worker_$workerId.log"
    $stderr = Join-Path $logsDir "enrich_worker_$workerId.error.log"

    $argList = @(
        "-u",
        $scriptPath,
        "--worker-id", $workerId.ToString(),
        "--worker-count", $Workers.ToString()
    )

    if ($Limit -gt 0) {
        $argList += @("--limit", $Limit.ToString())
    } else {
        # Unlimited default: process all remaining partitioned properties
        $argList += "--all"
    }
    if ($AllMaster) {
        $argList += "--all-master"
    }
    if ($ReEnrich) {
        $argList += "--re-enrich"
    }
    if ($Suburb) {
        $argList += @("--suburb", $Suburb)
    }
    if ($State) {
        $argList += @("--state", $State)
    }
    if ($Url) {
        $argList += @("--url", $Url)
    }
    if ($NoQdrant) {
        $argList += "--no-qdrant"
    }

    $proc = Start-Process `
        -FilePath $Python `
        -ArgumentList $argList `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    $processes += $proc
    Write-Host ("  [Started] Worker {0}/{1} (PID {2}) -> {3}" -f $workerId, $Workers, $proc.Id, (Split-Path $stdout -Leaf)) -ForegroundColor Green
}

Write-Host "`nAll $Workers workers have been successfully spawned in the background!" -ForegroundColor Green
Write-Host "---------------------------------------------------------------------------"
Write-Host "Useful Commands for Monitoring:" -ForegroundColor Yellow
Write-Host "  1. Live tail Worker 0:      Get-Content logs\enrich_worker_0.log -Wait -Tail 20"
Write-Host "  2. Live tail Worker 1:      Get-Content logs\enrich_worker_1.log -Wait -Tail 20"
Write-Host "  3. Check Overall Status:    .\run_enrichment_workers.ps1 -Status"
Write-Host "  4. Aggregate Stats Python:  .\.venv\Scripts\python.exe scratch\monitor_enrichment.py"
Write-Host "  5. Stop All Workers:        .\run_enrichment_workers.ps1 -Stop"
Write-Host "===========================================================================" -ForegroundColor Cyan

if ($Wait) {
    Write-Host "Waiting for all workers to finish..." -ForegroundColor Yellow
    $processes | Wait-Process
    Write-Host "All worker processes have completed." -ForegroundColor Green
}
