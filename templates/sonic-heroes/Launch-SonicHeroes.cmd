@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 (
  echo Extract the archive to a local folder, then run this script again.
  pause
  exit /b 1
)
if not exist "game\default.xbe" (
  echo.
  echo Missing game\default.xbe. This archive ships no game assets.
  echo Copy your own game files into the "game" folder first.
  echo See HOW-TO-ADD-GAME-FILES.md in this folder.
  echo.
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
