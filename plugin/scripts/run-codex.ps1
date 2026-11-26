[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [ValidateSet('read-only','write')][string]$Mode='write',
    [ValidateSet('low','medium','high','xhigh')][string]$Effort='medium',
    [string]$Model='', [string]$Prompt='', [string]$PromptFile='',
    [string]$ResumeSessionId='', [switch]$InPlace
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codex) {
    foreach ($candidate in @((Join-Path $env:APPDATA 'npm/codex.cmd'), (Join-Path $env:LOCALAPPDATA 'Programs/OpenAI/Codex/bin/codex.exe'), (Join-Path $env:USERPROFILE '.local/bin/codex.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $codex = Get-Command $candidate; break }
    }
}
if (-not $codex) { throw 'codex not found on PATH' }
$target = (Resolve-Path -LiteralPath $TargetPath).Path
if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw 'TargetPath must be a directory' }
if ($PromptFile) { $Prompt = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $PromptFile).Path, [Text.Encoding]::UTF8) }
if (-not $Prompt.Trim()) { throw 'Provide -Prompt or -PromptFile' }
$root = $null
if (Get-Command git -ErrorAction SilentlyContinue) { $root = & git -C $target rev-parse --show-toplevel 2>$null }
$isGit = [bool]$root
if ($isGit) { $target = $root.Trim() }
$sandbox = if ($Mode -eq 'write') { 'workspace-write' } else { 'read-only' }
$arguments = @('exec','-C',$target,'-s',$sandbox,'-c',"model_reasoning_effort=`"$Effort`"")
if (-not $isGit) { $arguments += '--skip-git-repo-check' }
if ($isGit -and $Mode -eq 'write' -and -not $InPlace) { $arguments += '--worktree' }
if ($Model) { $arguments += @('-m',$Model) }
if ($ResumeSessionId) { $arguments += @('resume',$ResumeSessionId) }
$arguments += @('--json','-')
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
[IO.Directory]::CreateDirectory($codexHome) | Out-Null
$logPath = Join-Path $codexHome 'codex-worker-dispatches.json'
$entry = @{dispatch_id=[guid]::NewGuid().ToString(); direction='ag_to_codex'; session_id=$ResumeSessionId; target_root=$target; mode=$Mode; model=$Model; effort=$Effort; started_at=[DateTime]::UtcNow.ToString('o'); prompt_preview=$Prompt.Substring(0,[Math]::Min(4000,$Prompt.Length)); status='running'}
function Update-Dispatch {
    $items = @()
    if (Test-Path -LiteralPath $logPath) { $items = @([IO.File]::ReadAllText($logPath,[Text.Encoding]::UTF8) | ConvertFrom-Json) }
    $items = @($items | Where-Object { $_.dispatch_id -ne $entry.dispatch_id }) + @([pscustomobject]$entry)
    $temp = Join-Path $codexHome ('.dispatch-' + [guid]::NewGuid().ToString() + '.tmp')
    try {
        [IO.File]::WriteAllText($temp,(ConvertTo-Json -InputObject @($items) -Depth 8),[Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $logPath) { [IO.File]::Replace($temp,$logPath,$null) } else { [IO.File]::Move($temp,$logPath) }
    } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp } }
}
Update-Dispatch
Write-Output "SESSION_ID: $ResumeSessionId`nWORKDIR: $target`nMODEL: $Model`nEFFORT: $Effort"
$old = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$lines = $Prompt | & $codex.Source @arguments 2>&1 | ForEach-Object { $line = $_.ToString(); Write-Host $line; $line }
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $old
foreach ($line in $lines) {
    try { $obj = $line | ConvertFrom-Json; if ($obj.type -eq 'thread.started') { $entry.session_id = $obj.thread_id; break } } catch {}
}
$entry.status = if ($exitCode -eq 0) { 'completed' } else { 'failed' }
$entry.exit_code = $exitCode
Update-Dispatch
Write-Output "SESSION_ID: $($entry.session_id)`nWORKDIR: $target`nEXIT_CODE: $exitCode`nMODEL: $Model`nEFFORT: $Effort"
exit $exitCode
