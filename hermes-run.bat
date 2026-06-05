@echo off
REM Run Hermes Agent in Docker (official image)
REM Configures litellm endpoint at spark-11:4000

REM Create config directory if it doesn't exist
if not exist ".hermes-data" mkdir .hermes-data

REM Pull the official image first
docker pull ghcr.io/nousresearch/hermes-agent:latest

REM Run the container interactively
docker run -it --rm --network host ^
  -v "%~dp0.hermes-data:/opt/data" ^
  -e OPENAI_API_KEY=sk-spark11 ^
  -e OPENAI_BASE_URL=http://spark-11:4000/v1 ^
  -e HERMES_UID=10000 ^
  -e LANG=C.UTF-8 ^
  --name hermes-agent ^
  ghcr.io/nousresearch/hermes-agent ^
  hermes
