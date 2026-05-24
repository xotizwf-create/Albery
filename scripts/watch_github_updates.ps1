param(
    [int]$IntervalSeconds = 60,
    [string]$Remote = "origin",
    [string]$Branch = "",
    [switch]$Once
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] $Message"
}

function Invoke-Git {
    param([string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed:`n$output"
    }
    return $output
}

$repoRoot = Invoke-Git @("rev-parse", "--show-toplevel")
Set-Location $repoRoot

if ([string]::IsNullOrWhiteSpace($Branch)) {
    $Branch = (Invoke-Git @("branch", "--show-current")).Trim()
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
    throw "Cannot detect current branch. Pass -Branch explicitly."
}

$remoteRef = "$Remote/$Branch"

function Update-FromGitHub {
    Write-Log "Checking $remoteRef..."
    Invoke-Git @("fetch", $Remote, $Branch) | Out-Null

    $local = (Invoke-Git @("rev-parse", "HEAD")).Trim()
    $remoteHead = (Invoke-Git @("rev-parse", $remoteRef)).Trim()

    if ($local -eq $remoteHead) {
        Write-Log "No changes. Local $Branch is up to date."
        return
    }

    $counts = (Invoke-Git @("rev-list", "--left-right", "--count", "HEAD...$remoteRef")).Trim() -split "\s+"
    $ahead = [int]$counts[0]
    $behind = [int]$counts[1]

    if ($behind -gt 0 -and $ahead -eq 0) {
        Write-Log "Found $behind new commit(s). Pulling fast-forward..."
        Invoke-Git @("pull", "--ff-only", $Remote, $Branch) | ForEach-Object { Write-Host $_ }
        Write-Log "Updated $Branch from GitHub."
        return
    }

    if ($ahead -gt 0 -and $behind -eq 0) {
        Write-Log "Local branch is $ahead commit(s) ahead of $remoteRef. Nothing pulled."
        return
    }

    Write-Log "Local and remote branches diverged: ahead=$ahead, behind=$behind. Resolve manually."
}

do {
    try {
        Update-FromGitHub
    } catch {
        Write-Log "ERROR: $($_.Exception.Message)"
    }

    if ($Once) {
        break
    }

    Start-Sleep -Seconds $IntervalSeconds
} while ($true)
