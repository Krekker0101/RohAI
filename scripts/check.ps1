$ErrorActionPreference = 'Stop'
$env:UV_CACHE_DIR = Join-Path (Split-Path $PSScriptRoot -Parent) '.uv-cache'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    uv run --locked ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
    uv run --locked ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Formatting failed' }
    uv run --locked mypy
    if ($LASTEXITCODE -ne 0) { throw 'Type checking failed' }
    uv run --locked pytest
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
} finally {
    Pop-Location
}
