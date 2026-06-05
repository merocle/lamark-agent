# Build & run Hermes Agent locally
# Uses our own Dockerfile — builds from scratch

$ErrorActionPreference = "Stop"

Write-Host "=== Building Hermes Agent Docker Image ===" -ForegroundColor Cyan

# Build the image from our Dockerfile
docker build -t hermes-agent-local -f Dockerfile.hermes .

# Create config dir
if (-not (Test-Path ".hermes-data")) {
    Write-Host "Creating .hermes-data directory..."
    New-Item -ItemType Directory -Path ".hermes-data"
}

Write-Host ""
Write-Host "=== Starting Hermes Agent ===" -ForegroundColor Cyan
Write-Host "Config: http://spark-11:4000/v1"
Write-Host "Dashboard: http://localhost:9119 (if gateway enabled)"
Write-Host ""

docker run -it --rm --network host `
  -v "${PWD}.hermes-data:/opt/data" `
  -e OPENAI_API_KEY=sk-spark11 `
  -e OPENAI_BASE_URL=http://spark-11:4000/v1 `
  -e HERMES_UID=10000 `
  -e LANG=C.UTF-8 `
  hermes-agent-local
