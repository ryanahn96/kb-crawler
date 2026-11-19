@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  설정 (관리자가 최초 1회만 입력합니다)
rem  - 주의: 배치 파일 안에서는 퍼센트 인코딩 URL의 % 기호를 %% 두 개로 적어야 합니다.
rem ============================================================
set "KB_PROJECT_DEFAULT="
set "KB_BUCKET_DEFAULT="
set "KB_DATASTORE_LOCATION_DEFAULT=us"
set "KB_DATASTORE_ID_DEFAULT=coway-kb"
set "KB_ROOT_PAGE_URL_DEFAULT=https://sites.google.com/coway.co.th/call-center-kb/%%E0%%B8%%AB%%E0%%B8%%99%%E0%%B8%%B2%%E0%%B9%%81%%E0%%B8%%A3%%E0%%B8%%81"
set "KB_SOURCE_DEFAULT="
set "KB_KEY_FILE_DEFAULT="

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

if "%KB_PROJECT%"=="" set "KB_PROJECT=%KB_PROJECT_DEFAULT%"
if "%KB_BUCKET%"=="" set "KB_BUCKET=%KB_BUCKET_DEFAULT%"
if "%KB_DATASTORE_LOCATION%"=="" set "KB_DATASTORE_LOCATION=%KB_DATASTORE_LOCATION_DEFAULT%"
if "%KB_DATASTORE_ID%"=="" set "KB_DATASTORE_ID=%KB_DATASTORE_ID_DEFAULT%"
if "%KB_ROOT_PAGE_URL%"=="" set "KB_ROOT_PAGE_URL=%KB_ROOT_PAGE_URL_DEFAULT%"
if "%KB_SOURCE_DIR%"=="" set "KB_SOURCE_DIR=%KB_SOURCE_DEFAULT%"
if "%KB_KEY_FILE%"=="" set "KB_KEY_FILE=%KB_KEY_FILE_DEFAULT%"

if "%KB_PROJECT%"=="" goto missing_config
if "%KB_BUCKET%"=="" goto missing_config
if "%KB_ROOT_PAGE_URL%"=="" goto missing_config
goto have_config

:missing_config
echo [오류] 필수 설정(KB_PROJECT, KB_BUCKET, KB_ROOT_PAGE_URL) 중 비어 있는 항목이 있습니다.
echo   sync_kb_windows.bat 파일을 메모장으로 열어 상단 기본값을 입력해 주세요.
set "EXIT_CODE=4"
goto finish

:have_config
if not "%KB_SOURCE_DIR%"=="" goto have_source
set "KB_SOURCE_DIR=%~dp0..\..\kb_crawler\coway_th_kb"
if exist "%KB_SOURCE_DIR%\_manifest.json" goto have_source
if exist "%~dp0..\..\coway_th_kb\_manifest.json" set "KB_SOURCE_DIR=%~dp0..\..\coway_th_kb"
if exist "%~dp0..\coway_th_kb\_manifest.json" set "KB_SOURCE_DIR=%~dp0..\coway_th_kb"
if exist "%~dp0coway_th_kb\_manifest.json" set "KB_SOURCE_DIR=%~dp0coway_th_kb"
:have_source
if "%KB_SOURCE_DIR:~-1%"=="\" set "KB_SOURCE_DIR=%KB_SOURCE_DIR:~0,-1%"
if not "%KB_KEY_FILE%"=="" set "GOOGLE_APPLICATION_CREDENTIALS=%KB_KEY_FILE%"

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
  set "EXIT_CODE=4"
  goto finish
)

if not exist ".venv\kb_sync_ready.txt" (
  echo [준비] Python 가상환경(.venv)을 구성하고 필수 패키지를 설치합니다...
  if not exist ".venv\Scripts\python.exe" (
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
      echo [오류] 가상환경 생성에 실패했습니다.
      set "EXIT_CODE=4"
      goto finish
    )
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [오류] 필수 패키지 설치에 실패했습니다.
    set "EXIT_CODE=4"
    goto finish
  )
  echo ok> ".venv\kb_sync_ready.txt"
)

set "PYTHONPATH=%~dp0..\.."
echo [동기화] GCS 업로드 및 Vertex AI Search DataStore 동기화를 시작합니다...
".venv\Scripts\python.exe" -m kb_pipeline.sync.sync_kb ^
  --source-dir "%KB_SOURCE_DIR%" ^
  --bucket "%KB_BUCKET%" ^
  --project "%KB_PROJECT%" ^
  --datastore-location "%KB_DATASTORE_LOCATION%" ^
  --datastore-id "%KB_DATASTORE_ID%" ^
  --root-page-url "%KB_ROOT_PAGE_URL%" %*
set "EXIT_CODE=%ERRORLEVEL%"

:finish
if not "%KB_UNATTENDED%"=="1" pause
exit /b %EXIT_CODE%
