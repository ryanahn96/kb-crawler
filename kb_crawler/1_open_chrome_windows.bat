@echo off
chcp 65001 >nul
setlocal

set "TARGET_URL=https://sites.google.com/coway.co.th/call-center-kb/%%E0%%B8%%AB%%E0%%B8%%99%%E0%%B8%%B2%%E0%%B9%%81%%E0%%B8%%A3%%E0%%B8%%81"
if not "%~1"=="" set "TARGET_URL=%~1"

set "PROFILE_DIR=%USERPROFILE%\chrome-coway-profile"
set "CHROME_EXE="

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
  set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
)

if "%CHROME_EXE%"=="" (
  echo [오류] Google Chrome 실행 파일(chrome.exe)을 찾을 수 없습니다.
  echo Google Chrome을 먼저 설치한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)

if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

echo [1/2] 크롤링 전용 Chrome 창을 실행합니다...
echo   - 프로필 경로: %PROFILE_DIR%
echo   - 디버깅 포트: 127.0.0.1:9222
echo.
echo [안내]
echo   1. 새로 열린 Chrome 창에서 사내 Google 계정으로 로그인해 KB 첫 화면이 보이는지 확인하세요.
echo   2. 최초 1회만 로그인하면 다음 실행부터는 로그인 상태가 유지됩니다.
echo   3. 크롤링이 끝날 때까지 새로 열린 Chrome 창을 닫지 마세요.
echo   4. 로그인이 완료되면 2_run_crawler_windows.bat 파일을 실행하세요.
echo.

start "" "%CHROME_EXE%" ^
  --remote-debugging-port=9222 ^
  --remote-allow-origins=http://127.0.0.1:9222 ^
  --no-first-run ^
  --no-default-browser-check ^
  --user-data-dir="%PROFILE_DIR%" ^
  "%TARGET_URL%"

endlocal
