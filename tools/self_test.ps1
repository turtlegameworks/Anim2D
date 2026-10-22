# Runs the built program's own build checks (editor and FX builder) and fails if either reports a problem.
#   pwsh tools/self_test.ps1 -Program dist/Anim2D.exe -Output selftest
param(
    [Parameter(Mandatory = $true)][string]$Program,
    [string]$Output = "selftest"
)
$ErrorActionPreference = "Stop"
$env:QT_QPA_PLATFORM = "offscreen"
$program = (Resolve-Path $Program).Path
$failed = $false

foreach ($mode in @("editor", "fx")) {
    $dir = Join-Path $Output $mode
    New-Item -ItemType Directory -Force $dir | Out-Null
    $arguments = @("--self-test", (Resolve-Path $dir).Path)
    if ($mode -eq "fx") { $arguments = @("--fx-builder") + $arguments }
    # A windowed program: wait for it explicitly, with a time limit.
    $process = Start-Process -FilePath $program -ArgumentList $arguments -PassThru -NoNewWindow
    $null = $process.Handle  # keep the handle, or ExitCode reads as empty after WaitForExit
    if (-not $process.WaitForExit(300000)) {
        $process.Kill()
        Write-Host "::error::$mode self-test timed out"
        $failed = $true
        continue
    }
    $report = Join-Path $dir "report.json"
    if (-not (Test-Path $report)) {
        Write-Host "::error::$mode self-test wrote no report (exit code $($process.ExitCode))"
        $failed = $true
        continue
    }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    if ($process.ExitCode -ne 0 -or -not $result.ok) {
        Write-Host "::error::$mode self-test failed"
        Get-Content $report
        $failed = $true
    } else {
        Write-Host "$mode self-test passed"
    }
}
if ($failed) { exit 1 }
