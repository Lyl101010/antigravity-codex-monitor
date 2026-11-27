[CmdletBinding()]
param(
    [string]$TargetPath='',
    [ValidateSet('read-only','write')][string]$Mode='write',
    [ValidateSet('low','medium','high','xhigh')][string]$Effort='medium',
    [string]$Model='', [string]$Prompt='', [string]$PromptFile='',
    [string]$ResumeSessionId='', [switch]$InPlace, [string]$DispatchId='',
    [ValidateSet('','read-only','workspace-write','full-access',IgnoreCase=$false)][string]$Sandbox=''
)
$ErrorActionPreference = 'Stop'
if (-not $Sandbox) { $Sandbox = if ($Mode -eq 'write') { 'workspace-write' } else { 'read-only' } }
if ($Mode -eq 'read-only' -and $Sandbox -ne 'read-only') { throw 'Read-only mode requires the read-only sandbox' }
if ($PSBoundParameters.ContainsKey('DispatchId')) {
    if (-not $DispatchId -or $DispatchId -match '[\s\p{Cc}]' -or
        ($DispatchId -replace '[\uD800-\uDBFF][\uDC00-\uDFFF]', '_').Length -gt 128) {
        throw 'Invalid dispatch ID'
    }
} else { $DispatchId = [guid]::NewGuid().ToString() }
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codex) {
    foreach ($candidate in @((Join-Path $env:APPDATA 'npm/codex.cmd'), (Join-Path $env:LOCALAPPDATA 'Programs/OpenAI/Codex/bin/codex.exe'), (Join-Path $env:USERPROFILE '.local/bin/codex.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $codex = Get-Command $candidate; break }
    }
}
if (-not $codex) { throw 'codex not found on PATH' }
if ($PromptFile) { $Prompt = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $PromptFile).Path, [Text.Encoding]::UTF8) }
if (-not $Prompt.Trim()) { throw 'Provide -Prompt or -PromptFile' }

function Resolve-ScratchPath([string]$Path, [int]$LinkDepth=0) {
    $parent = [IO.Path]::GetDirectoryName($Path)
    if (-not $parent) { return $Path }
    $parent = Resolve-ScratchPath $parent $LinkDepth
    $Path = [IO.Path]::Combine($parent, [IO.Path]::GetFileName($Path))
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        $link = @($item.Target)[0]
        if (-not $link -or $LinkDepth -ge 40) { throw 'TargetPath must be a directory' }
        $linkPath = [IO.Path]::GetFullPath([IO.Path]::Combine($parent, $link))
        return (Resolve-ScratchPath $linkPath ($LinkDepth + 1))
    }
    return $Path
}

$userHome = if ($HOME) { $HOME } else { $env:USERPROFILE }
$scratchTask = -not $TargetPath
if ($scratchTask -or -not (Test-Path -LiteralPath $TargetPath)) {
    $scratchRoot = if ($env:ANTIGRAVITY_SCRATCH_DIR) { $env:ANTIGRAVITY_SCRATCH_DIR } else { Join-Path $userHome '.gemini/antigravity/scratch' }
    $scratchRoot = Resolve-ScratchPath ($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($scratchRoot))
    if ($scratchTask) { $TargetPath = Join-Path $scratchRoot 'codex-tasks' }
    $target = Resolve-ScratchPath ($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TargetPath))
    $separator = [IO.Path]::DirectorySeparatorChar
    $comparison = if ($separator -eq '\') { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
    $scratchPrefix = $scratchRoot.TrimEnd($separator) + $separator
    if (-not ($target.TrimEnd($separator) + $separator).StartsWith($scratchPrefix, $comparison)) {
        throw 'TargetPath must be a directory'
    }
    [IO.Directory]::CreateDirectory($target) | Out-Null
    if ($scratchTask) {
        $stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss', [Globalization.CultureInfo]::InvariantCulture)
        $suffix = [guid]::NewGuid().ToString('N').Substring(0,8)
        $parent = [System.Management.Automation.WildcardPattern]::Escape($target)
        $target = (New-Item -ItemType Directory -Path $parent -Name "task-$stamp-$suffix").FullName
    }
} else {
    $target = (Resolve-Path -LiteralPath $TargetPath).Path
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw 'TargetPath must be a directory' }
}
$root = $null
$isGit = $false
if (-not $scratchTask -and (Get-Command git -ErrorAction SilentlyContinue)) {
    $old = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $root = & git -C $target rev-parse --show-toplevel 2>$null
        $isGit = ($LASTEXITCODE -eq 0 -and [bool]$root)
    } finally { $ErrorActionPreference = $old }
}
if ($isGit) { $target = $root.Trim() }
$config = 'model_reasoning_effort="{0}"' -f $Effort
if ($PSVersionTable.PSVersion -lt [version]'7.3' -or $PSNativeCommandArgumentPassing -eq 'Legacy' -or
    ($PSNativeCommandArgumentPassing -eq 'Windows' -and $codex.Source -match '\.(cmd|bat|ps1)$')) {
    $config = $config.Replace('"', '\"')
}
$arguments = @('exec')
if (-not $ResumeSessionId) { $arguments += @('-C',$target) }
if ($Sandbox -eq 'full-access') { $arguments += '--dangerously-bypass-approvals-and-sandbox' }
else { $arguments += @('-s',$Sandbox) }
$arguments += @('-c',$config)
if (-not $isGit) { $arguments += '--skip-git-repo-check' }
if (-not $ResumeSessionId -and $isGit -and $Mode -eq 'write' -and -not $InPlace) { $arguments += '--worktree' }
if ($Model) { $arguments += @('-m',$Model) }
if ($ResumeSessionId) { $arguments += @('resume',$ResumeSessionId) }
$arguments += @('--json','-')
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $userHome '.codex' }
[IO.Directory]::CreateDirectory($codexHome) | Out-Null
$logPath = Join-Path $codexHome 'codex-worker-dispatches.json'
$entry = @{dispatch_id=$DispatchId; direction='ag_to_codex'; session_id=$ResumeSessionId; target_root=$target; mode=$Mode; sandbox=$Sandbox; model=$Model; effort=$Effort; started_at=[DateTime]::UtcNow.ToString('o'); prompt_preview=$Prompt.Substring(0,[Math]::Min(4000,$Prompt.Length)); status='running'}
function Update-Dispatch {
    $lockPath = "$logPath.lock"
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ($true) {
        try { $lock = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew); break }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw }
            Start-Sleep -Milliseconds 25
        }
    }
    $temp = Join-Path $codexHome ('.dispatch-' + [guid]::NewGuid().ToString() + '.tmp')
    try {
        $items = @()
        if (Test-Path -LiteralPath $logPath) { $items = [array]([IO.File]::ReadAllText($logPath,[Text.Encoding]::UTF8) | ConvertFrom-Json) }
        $items = @($items | Where-Object { $_.dispatch_id -cne $entry.dispatch_id }) + @([pscustomobject]$entry)
        $items = @($items | Select-Object -Last 200)
        [IO.File]::WriteAllText($temp,(ConvertTo-Json -InputObject @($items) -Depth 8),[Text.UTF8Encoding]::new($false))
        for ($attempt = 0; ; $attempt++) {
            try {
                if (Test-Path -LiteralPath $logPath) { [IO.File]::Replace($temp, $logPath, [NullString]::Value) } else { [IO.File]::Move($temp,$logPath) }
                break
            } catch [IO.IOException] {
                if ($attempt -ge 19) { throw }
                Start-Sleep -Milliseconds 25
            }
        }
    } finally {
        try { [IO.File]::Delete($temp) }
        finally { $lock.Dispose(); [IO.File]::Delete($lockPath) }
    }
}
Update-Dispatch
Write-Output "DISPATCH_ID: $DispatchId`nSESSION_ID: $ResumeSessionId`nWORKDIR: $target`nMODEL: $Model`nEFFORT: $Effort"
$old = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $Prompt | & $codex.Source @arguments 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Write-Host $line
        if ($line.StartsWith('{')) {
            $obj = $null
            try { $obj = $line | ConvertFrom-Json -ErrorAction Stop } catch {}
            if ($obj.type -eq 'thread.started' -and $obj.thread_id -and $entry.session_id -cne $obj.thread_id) {
                $entry.session_id = $obj.thread_id
                Update-Dispatch
            }
        }
    }
    $exitCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $old }
$entry.status = if ($exitCode -eq 0) { 'completed' } else { 'failed' }
$entry.exit_code = $exitCode
Update-Dispatch
Write-Output "DISPATCH_ID: $DispatchId`nSESSION_ID: $($entry.session_id)`nWORKDIR: $target`nEXIT_CODE: $exitCode`nMODEL: $Model`nEFFORT: $Effort"
exit $exitCode
