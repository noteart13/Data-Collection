<#
.SYNOPSIS
    High-Throughput Bulk Enrichment Runner for PropertyMaster (16M records)
.DESCRIPTION
    Runs bulk_enrich_master.py to populate:
      - bed, bath, features.garage
      - area.totalArea (sqM)
      - constructionYear
      - expectedPrice
    WITHOUT downloading images (maximum speed & bandwidth efficiency).
.EXAMPLE
    .\run_bulk_enrichment.ps1 -State WA
    .\run_bulk_enrichment.ps1 -State ALL
    .\run_bulk_enrichment.ps1 -Suburb "North Coogee" -State WA
#>

param (
    [string]$State = "WA",
    [string]$Suburb = "",
    [string]$Url = "http://localhost:6333",
    [int]$BatchSize = 1000,
    [int]$Limit = 0,
    [switch]$Force,
    [switch]$DryRun
)

# Protect against accidental parameter bleed if someone passed --url
if ($Suburb -and $Suburb.StartsWith("-")) {
    $Suburb = ""
}

# Auto-detect remote Qdrant URL from .env if default localhost was left unchanged
if ($Url -eq "http://localhost:6333") {
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envFile) {
        $envMatch = Get-Content $envFile | Select-String -Pattern "^QDRANT_URI=(.+)"
        if ($envMatch) {
            $Url = $envMatch.Matches.Groups[1].Value.Trim().Trim('"').Trim("'")
        }
    }
}

$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

Write-Host "===========================================================================" -ForegroundColor Cyan
Write-Host " LAUNCHING PROPERTYMASTER HIGH-THROUGHPUT BULK ENRICHMENT ENGINE" -ForegroundColor Cyan
Write-Host " Python       : $PythonExe" -ForegroundColor White
Write-Host " Target State : $State" -ForegroundColor White
Write-Host " Suburb       : $(if ($Suburb) { $Suburb } else { 'ALL SUBURBS' })" -ForegroundColor White
Write-Host " Target URL   : $Url" -ForegroundColor White
Write-Host " Batch Size   : $BatchSize" -ForegroundColor White
Write-Host " Mode         : NO IMAGES (Metadata only - 16M scale)" -ForegroundColor Green
Write-Host "===========================================================================" -ForegroundColor Cyan

$ArgsList = @(
    "bulk_enrich_master.py",
    "--url", $Url,
    "--state", $State,
    "--batch-size", $BatchSize
)

if ($Suburb) {
    $ArgsList += @("--suburb", $Suburb)
}

if ($Limit -gt 0) {
    $ArgsList += @("--limit", $Limit)
}

if ($Force) {
    $ArgsList += @("--force")
}

if ($DryRun) {
    $ArgsList += @("--dry-run")
}

& $PythonExe @ArgsList
