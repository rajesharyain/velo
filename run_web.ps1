# Start Velo from the repo root so `travel_instagram` is this project, not site-packages.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONPATH = $PSScriptRoot
# velo_web.py pins repo root on sys.path so /ad-reels and other routes match this checkout.
python -m uvicorn velo_web:app --reload --host 127.0.0.1 --port 8080
