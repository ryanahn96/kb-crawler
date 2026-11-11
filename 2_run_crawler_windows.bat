@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

set "PY_CMD="
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 --version >nul 2>nul
  if %ERRORLEVEL%==0 set "PY_CMD=py -3"
)
if "%PY_CMD%"=="" (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 set "PY_CMD=python"
)

if "%PY_CMD%"=="" (
  echo [오류] Python 3.10 이상이 설치되어 있지 않거나 PATH에 등록되지 않았습니다.
  echo https://www.python.org/downloads/windows/ 에서 설치 시 "Add python.exe to PATH"를 체크해 주세요.
  if not "%KB_UNATTENDED%"=="1" pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [준비] Python 가상환경(.venv)을 생성하고 필수 패키지(websockets)를 설치합니다...
  %PY_CMD% -m venv .venv
  if errorlevel 1 (
    echo [오류] 가상환경 생성에 실패했습니다.
    if not "%KB_UNATTENDED%"=="1" pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [오류] 필수 패키지 설치에 실패했습니다. 사내 네트워크/프록시 설정을 확인해 주세요.
    if not "%KB_UNATTENDED%"=="1" pause
    exit /b 1
  )
)

echo [2/2] Google Sites KB 크롤링을 시작합니다...
".venv\Scripts\python.exe" crawl_sites.py --output-dir ".\coway_th_kb" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if %EXIT_CODE%==0 (
  echo [완료] 크롤링이 정상 완료되었습니다.
  echo   - 결과 폴더: %CD%\coway_th_kb
  echo   - 페이지 목록: %CD%\coway_th_kb\_manifest.json
  echo   - Drive 파일 권한/다운로드 리포트: %CD%\coway_th_kb\_drive_files.json
) else (
  echo [실패] 크롤링 중 오류가 발생했습니다. (종료 코드: %EXIT_CODE%)
  echo   - 1_open_chrome_windows.bat 으로 실행한 Chrome 창이 켜져 있는지, Google 로그인이 완료되었는지 확인해 주세요.
)

if not "%KB_UNATTENDED%"=="1" pause
exit /b %EXIT_CODE%
