$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (!(Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Требуется Python 3.13. Установите его и повторите запуск.' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Не установлены зависимости Python' }
& ./.venv/Scripts/python.exe scripts/download_tools.py
if ($LASTEXITCODE -ne 0) { throw 'Не установлены FFmpeg и ffprobe' }
Push-Location frontend
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Не установлены зависимости интерфейса' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Не собран интерфейс' }
} finally { Pop-Location }
Write-Host 'Установка завершена. Откройте Start SurvivalFlow.cmd.'
