@echo off
cd /d "%~dp0"
if not exist "cache" mkdir "cache"
if not exist "cache\tmdb_posters" mkdir "cache\tmdb_posters"
if not exist "cache\tmdb_logos" mkdir "cache\tmdb_logos"
set "DB_PATH=%CD%\cache\cache.db"
set "BADGE_DIR=%CD%\badges"
set "TMDB_POSTER_CACHE_DIR=%CD%\cache\tmdb_posters"
set "TMDB_LOGO_CACHE_DIR=%CD%\cache\tmdb_logos"
set "PREFETCH_STATE_PATH=%CD%\cache\postfletch_prefetch_state.json"
set "FAKE_TEXTLESS_POSTERS_PATH=%CD%\cache\fake_textless_posters.txt"
"C:\Users\Gilles\Documents\posterplusfetch\PostersPlus-integrated\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 > postfletch-8000.out.log 2> postfletch-8000.err.log
