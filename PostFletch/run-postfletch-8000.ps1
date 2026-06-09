param(
    [int]$Port = 8010
)

Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force -Path "cache", "cache\tmdb_posters", "cache\tmdb_logos" | Out-Null
$env:DB_PATH = Join-Path $PSScriptRoot "cache\cache.db"
$env:BADGE_DIR = Join-Path $PSScriptRoot "badges"
$env:TMDB_POSTER_CACHE_DIR = Join-Path $PSScriptRoot "cache\tmdb_posters"
$env:TMDB_LOGO_CACHE_DIR = Join-Path $PSScriptRoot "cache\tmdb_logos"
$env:PREFETCH_STATE_PATH = Join-Path $PSScriptRoot "cache\postfletch_prefetch_state.json"
$env:FAKE_TEXTLESS_POSTERS_PATH = Join-Path $PSScriptRoot "cache\fake_textless_posters.txt"
& "C:\Users\Gilles\Documents\posterplusfetch\PostersPlus-integrated\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port $Port *> "postfletch-$Port.combined.log"
