@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 (
  echo Extract the archive to a local folder, then run this script again.
  pause
  exit /b 1
)
if not exist "game\default.xbe" (
  echo Missing game\default.xbe. Download the game artifact, not only the source artifact.
  pause
  exit /b 1
)
echo Starting experimental SonicHeroesPC build. Log: heroes-last-run.log
"SonicHeroesPC.exe" > "heroes-last-run.log" 2>&1
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo Game exited with code %EXITCODE%. See heroes-last-run.log and xbox_kernel.log.
  pause
)
exit /b %EXITCODE%
