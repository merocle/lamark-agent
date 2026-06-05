# Run Hermes Agent in Docker (official image)
# Configures litellm endpoint at spark-11:4000

# Create config directory if it doesn't exist
if (-not (Test-Path ".hermes-data")) {
    New-Item -ItemType Directory -Path ".hermes-data"
}

# Pull the official image first
Write-Host "Pulling hermes-agent image..."
docker pull ghcr.io/nousresearch/hermes-agent:latest

# Run the container interactively
Write-Host "Starting Hermes Agent..."
docker run -it --rm --network host `
  -v "${PWD}.hermes-data:/opt/data" `
  -e OPENAI_API_KEY=sk-spark11 `
  -e OPENAI_BASE_URL=http://spark-11:4000/v1 `
  -e HERMES_UID=10000 `
  -e LANG=C.UTF-8 `
  --name hermes-agent `
  ghcr.io/nousresearch/hermes-agent `
  hermes
