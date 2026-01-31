# docker-compose.ps1
# EOS Docker Compose launcher for Windows

$ErrorActionPreference = "Stop"

function Is-WSL2 {
    try {
        docker info --format '{{.OperatingSystem}}' 2>$null | Select-String -Pattern "WSL2"
    } catch {
        return $false
    }
}

if (Is-WSL2) {
    Write-Host "Detected Docker running on WSL2"

    # Linux path inside WSL
    $User = $env:USERNAME.ToLower()
    $DockerComposeDataDir = "/home/$User/.local/share/net.akkudoktor.eos"
}
else {
    Write-Host "Detected native Windows Docker"

    $HomeDir = [Environment]::GetFolderPath("UserProfile")
    $DockerComposeDataDir = Join-Path $HomeDir "AppData\Local\net.akkudoktor.eos"
    $DockerComposeDataDir = $DockerComposeDataDir.Replace("\", "/")

    if (-not (Test-Path $DockerComposeDataDir)) {
        New-Item -ItemType Directory -Path $DockerComposeDataDir -Force | Out-Null
    }
}

$env:DOCKER_COMPOSE_DATA_DIR = $DockerComposeDataDir

Write-Host "EOS data dir: '$env:DOCKER_COMPOSE_DATA_DIR'"

docker compose -f docker-compose.yml up -d
