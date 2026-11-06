# ai/run.ps1
# Entry point for the AkkudoktorEOS AI development sandbox (Windows PowerShell).
#
# Hardened launcher:
#   - Fails closed if compose.yaml is missing any sandbox control.
#   - Rejects command-line flags that would weaken isolation
#     (--privileged, --cap-add, --device, --pid=host,
#      -v /var/run/docker.sock:..., -v //./pipe/docker_engine:...).
#   - Only forwards arguments after `--` as the in-container command.
#   - Requires every published port to bind to 127.0.0.1 (loopback on the host).
#   - Optional rebuild (AI_SANDBOX_REBUILD=1); cached image otherwise.
#   - Windows: UID/GID bind-mount mapping does not apply, HOST_UID/HOST_GID
#     fall back to the defaults from ai/.env.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

$ComposeFile = "ai/compose.yaml"
$EnvFile     = "ai/.env"
$EnvExample  = "ai/.env.example"

# ---------------------------------------------------------------------------
# Helper: hard exit with a message on stderr.
# ---------------------------------------------------------------------------
function Fail([string]$Message, [int]$Code = 1) {
    [Console]::Error.WriteLine("ERROR: $Message")
    exit $Code
}

# ---------------------------------------------------------------------------
# 0. Reject CLI flags that weaken isolation.
#    Forwarded *container* commands must come after `--`.
# ---------------------------------------------------------------------------
$ForbiddenFlags = @(
    '--privileged',
    '--cap-add',
    '--device',
    '--security-opt',
    '--pid',
    '--ipc',
    '--network',
    '--net',
    '--userns',
    '--uts',
    '--volume',
    '-v',
    '--mount',
    '--volumes-from',
    '--group-add',
    '--runtime',
    '--publish',
    '-p'
)

foreach ($arg in $args) {
    foreach ($flag in $ForbiddenFlags) {
        if ($arg -eq $flag -or $arg.StartsWith("$flag=")) {
            Fail "refusing to pass '$arg' to docker compose run. This flag can weaken the sandbox and is not allowed." 2
        }
    }
}

$ExtraCmd = @()
if ($args.Count -gt 0) {
    if ($args[0] -ne '--') {
        Fail "extra arguments must be supplied after '--' as the in-container command.`n       Example: .\ai\ai.ps1 -- bash -lc 'pytest -q'" 2
    }
    # Skip the leading `--`.
    $ExtraCmd = @($args | Select-Object -Skip 1)
}

# ---------------------------------------------------------------------------
# 1. Prerequisites.
# ---------------------------------------------------------------------------
Write-Host "==> Checking prerequisites..."

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "docker is not installed."
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "docker compose v2 is not available."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "docker daemon is not reachable.`n       - Is Docker Desktop running?`n       - Try 'docker info' manually."
}

if (-not (Test-Path "pyproject.toml")) {
    Fail "pyproject.toml not found; run from inside the AkkudoktorEOS repository."
}

if (-not (Test-Path $ComposeFile)) {
    Fail "$ComposeFile missing."
}

# ---------------------------------------------------------------------------
# 2. Verify sandbox controls in compose.yaml. Fails closed.
# ---------------------------------------------------------------------------
Write-Host "==> Verifying sandbox hardening in $ComposeFile ..."

$composeText = Get-Content -Raw -LiteralPath $ComposeFile

function Require-InCompose([string]$Pattern, [string]$Why) {
    if ($composeText -notmatch $Pattern) {
        Fail "compose.yaml does not contain: $Pattern`n       $Why" 3
    }
}

Require-InCompose '(?m)^\s*cap_drop:\s*$|(?m)^\s*cap_drop:\s*\[' 'Missing cap_drop (must drop ALL).'
Require-InCompose '(?m)^\s*-\s*"?ALL"?\s*$' 'cap_drop must include ALL.'
Require-InCompose 'no-new-privileges:\s*true' 'Missing no-new-privileges:true.'
Require-InCompose 'read_only:\s*true' 'Missing read_only:true.'

# The docker socket (Linux path or Windows named pipe) must never be mounted.
if ($composeText -match '/var/run/docker\.sock') {
    Fail "compose.yaml mounts the Docker socket. This is an escape vector." 3
}
if ($composeText -match '//\./pipe/docker_engine|\\\\\.\\pipe\\docker_engine') {
    Fail "compose.yaml mounts the Docker named pipe. This is an escape vector." 3
}

# privileged must be false or absent.
if ($composeText -match 'privileged:\s*true') {
    Fail "compose.yaml sets privileged:true; refusing to start." 3
}

# network_mode: host must never be used.
if ($composeText -match 'network_mode:\s*"?host"?') {
    Fail "compose.yaml sets network_mode: host; refusing to start." 3
}

# Every published port MUST bind to 127.0.0.1.
if ($composeText -match '(?m)^\s*ports:\s*$') {
    $portBlock = [regex]::Match(
        $composeText,
        '(?ms)^\s*ports:\s*$\s*((?:^\s*-\s*.+\s*$\s*)+)'
    )
    if ($portBlock.Success) {
        $entries = [regex]::Matches($portBlock.Groups[1].Value, '(?m)^\s*-\s*(.+?)\s*$')
        foreach ($m in $entries) {
            $raw = $m.Groups[1].Value.Trim().Trim('"').Trim("'")
            if (-not $raw.StartsWith('127.0.0.1:')) {
                Fail "compose.yaml publishes a port on a non-loopback interface: '$raw'.`n       Every entry under 'ports:' must start with '127.0.0.1:'." 3
            }
        }
    }
}

# ---------------------------------------------------------------------------
# 3. Ensure ai/.env exists. HOST_UID/HOST_GID do not apply on Windows (bind
#    mounts via Docker Desktop ignore them); the defaults in ai/.env stand.
# ---------------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Host "==> Creating $EnvFile from $EnvExample"
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -Force
    } else {
        Write-Host "==> Creating empty $EnvFile"
        New-Item -ItemType File -Path $EnvFile -Force | Out-Null
    }
}

# ---------------------------------------------------------------------------
# 4. Rebuild is opt-in.
# ---------------------------------------------------------------------------
$BuildFlag = @()
if ($env:AI_SANDBOX_REBUILD -eq '1') {
    Write-Host "==> Rebuilding image (AI_SANDBOX_REBUILD=1)"
    $BuildFlag = @('--build')
} else {
    Write-Host "==> Using cached image (set AI_SANDBOX_REBUILD=1 to rebuild)"
}

# ---------------------------------------------------------------------------
# 5. Run.
# ---------------------------------------------------------------------------
Write-Host "==> Starting AI dev sandbox..."

$dockerArgs = @(
    'compose',
    '-f', $ComposeFile,
    'run', '--rm'
) + $BuildFlag + @('ai-dev') + $ExtraCmd

& docker @dockerArgs
$rc = $LASTEXITCODE

# Preserve docker compose's exit code, but if docker itself wasn't invoked
# (e.g. Parse error) we still exit non-zero.
if ($null -eq $rc) { $rc = 1 }
exit $rc
