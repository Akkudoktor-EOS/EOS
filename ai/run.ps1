# ai/run.ps1
# Entry point for the AkkudoktorEOS AI development sandbox (Windows PowerShell).
#
# Launcher:
#   - Sanity-checks ai/compose.yaml for the main sandbox controls. This is not
#     a complete validation; the protection of the sandbox definition is the
#     read-only mount of ai/ in the container.
#   - Forwards all arguments as the in-container command, never as docker
#     compose options. PowerShell consumes an unquoted `--` itself, so `--`
#     is optional: .\ai\run.ps1 claude  or  .\ai\run.ps1 -- claude
#   - Optional write access to ai/skills (AI_SANDBOX_SKILLS_RW=1).
#   - Requires every published port to bind to 127.0.0.1 (loopback on the host).
#   - Optional rebuild (AI_SANDBOX_REBUILD=1); cached image otherwise.
#   - Optional publishing of the compose ports (AI_SANDBOX_PORTS=1).
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
# 0. Arguments: all of them form the in-container command. A leading '--'
#    (passed quoted, or kept by some PowerShell versions) is dropped.
#    Cast to string: PowerShell passes numbers (e.g. `-n 4`) as Int32.
# ---------------------------------------------------------------------------
$ExtraCmd = @($args | ForEach-Object { [string]$_ })
if ($ExtraCmd.Count -gt 0 -and $ExtraCmd[0] -eq '--') {
    $ExtraCmd = @($ExtraCmd | Select-Object -Skip 1)
}
# A command never starts with '-': such an argument is a docker compose option
# (e.g. --build), which the launcher does not forward.
if ($ExtraCmd.Count -gt 0 -and $ExtraCmd[0].StartsWith('-')) {
    Fail ("'$($ExtraCmd[0])' is not a command. Docker compose options cannot be passed; " +
        "all arguments form the in-container command.`n       " +
        "Use AI_SANDBOX_REBUILD=1 to rebuild and AI_SANDBOX_PORTS=1 to publish the ports.") 2
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
# 2. Sanity-check the sandbox controls in compose.yaml.
#    This catches accidental edits, not every weakening edit. The agent
#    cannot edit compose.yaml from inside the sandbox (ai/ is read-only there);
#    review changes to ai/ before you start the sandbox.
# ---------------------------------------------------------------------------
Write-Host "==> Checking sandbox controls in $ComposeFile ..."

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
Require-InCompose '\.\./ai:/workspace/ai:ro' 'ai/ must be mounted read-only.'

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
        '(?m)^[ \t]*ports:[ \t]*\r?\n((?:^[ \t]*-[^\r\n]*(?:\r?\n|\z))+)'
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
# 5. Port publishing is opt-in. `docker compose run` publishes the ports of
#    compose.yaml (loopback-only, verified above) only with --service-ports.
#    Off by default, so several sandboxes can run in parallel.
# ---------------------------------------------------------------------------
$PortsFlag = @()
if ($env:AI_SANDBOX_PORTS -eq '1') {
    Write-Host "==> Publishing the compose ports on 127.0.0.1 (AI_SANDBOX_PORTS=1)"
    $PortsFlag = @('--service-ports')
} else {
    Write-Host "==> Ports not published (set AI_SANDBOX_PORTS=1 to publish them)"
}

# ---------------------------------------------------------------------------
# 6. ai/skills is read-only in the container unless AI_SANDBOX_SKILLS_RW=1.
#    Always set the mode explicitly, so a value in ai/.env cannot change it.
# ---------------------------------------------------------------------------
# $env: changes outlive the script in the calling session; restored below.
$PrevSkillsMode = $env:AI_SANDBOX_SKILLS_MODE
if ($env:AI_SANDBOX_SKILLS_RW -eq '1') {
    Write-Host "==> ai/skills is writable in the sandbox (AI_SANDBOX_SKILLS_RW=1)"
    Write-Host "    Review every change to ai/skills before you commit or push it."
    $env:AI_SANDBOX_SKILLS_MODE = 'rw'
} else {
    $env:AI_SANDBOX_SKILLS_MODE = 'ro'
}

# ---------------------------------------------------------------------------
# 7. Run.
# ---------------------------------------------------------------------------
Write-Host "==> Starting AI dev sandbox..."

$dockerArgs = @(
    'compose',
    '-f', $ComposeFile,
    'run', '--rm'
) + $BuildFlag + $PortsFlag + @('ai-dev') + $ExtraCmd

$rc = $null
$PrevErrorAction = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 turns native stderr into error records when the
    # script's output is redirected (e.g. .\ai\run.ps1 2>&1 | Tee-Object log.txt);
    # with "Stop" the first status line of docker compose would abort the script.
    $ErrorActionPreference = "Continue"
    & docker @dockerArgs
    $rc = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $PrevErrorAction
    $env:AI_SANDBOX_SKILLS_MODE = $PrevSkillsMode
}

# Preserve docker compose's exit code, but if docker itself wasn't invoked
# (e.g. Parse error) we still exit non-zero.
if ($null -eq $rc) { $rc = 1 }
exit $rc
