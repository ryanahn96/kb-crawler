# Google Sites 콜센터 KB 크롤러 사용자 가이드

사내 Google Workspace 계정 인증이 필요한 비공개 Google Sites 지식베이스(KB)를 상단 메뉴 및 하위 드롭다운 계층 구조 그대로 로컬 폴더에 수집하는 도구입니다.

일반 웹 크롤러는 사내 SSO 및 2단계 인증(2FA) 화면을 통과할 수 없기 때문에, 사용자가 로그인한 Chrome 브라우저의 디버깅 포트(Chrome DevTools Protocol, 로컬 `127.0.0.1:9222`)에 연결해 문서를 추출합니다. 별도의 Google Cloud OAuth 앱 승인이나 관리자 API 키 발급 없이 본인 계정의 열람 권한만으로 바로 실행할 수 있습니다.

---

## 1. 수집 범위

1회 실행 시 아래 항목을 자동으로 수집하고 정리합니다.

- 상단 탭과 마우스 오버(Hover) 하위 메뉴 구조를 그대로 반영한 번호순 폴더 및 Markdown(`.md`) 문서 생성
- 페이지 내 접혀 있는 모든 토글(아코디언) 자동 펼침, 표(`<table>`) 변환, 다단 제품 이미지와 하단 상세 스펙 1:1 매칭
- 로그인 세션이 있어야만 보이는 비공개 본문 이미지를 `_assets/<페이지명>/` 폴더에 내려받고 문서 내 이미지 경로를 로컬 상대 경로로 연결
- 본문에 링크되거나 삽입된 Google Drive 파일(PDF, 이미지 등)과 Google Docs / Sheets / Slides 문서를 `_drive_files/` 폴더에 원본 다운로드(Docs는 `.docx`, Sheets는 `.xlsx`, Slides는 `.pptx`로 변환 저장)한 뒤, 본문 링크를 로컬 상대 경로 + 원본 `[Drive]` 링크로 매핑하고 페이지 하단에 `## Drive Files (Local Mapping)` 표 추가
- 접근 권한이 없는 Google Drive 파일은 다운로드를 건너뛰고 원본 링크 옆에 `(Drive: no_access)`처럼 상태를 표시하며, 전체 파일의 권한 보유 여부와 응답 사유를 `_drive_files.json` 리포트로 생성

---

## 2. 사전 준비 사항 (Windows 기준)

1. Google Chrome 브라우저가 설치되어 있어야 합니다.
2. Python 3.10 이상이 설치되어 있어야 합니다.
   - 설치 파일 다운로드: https://www.python.org/downloads/windows/
   - 설치 첫 화면 하단의 **`Add python.exe to PATH`** 체크박스를 반드시 선택한 뒤 `Install Now`를 클릭하세요.
3. 대상 Google Sites KB 열람 권한이 있는 사내 Google 계정을 준비합니다.
   - 본문 내 Google Drive 첨부파일까지 함께 내려받으려면 해당 계정으로 Drive 파일 열람 및 다운로드가 가능해야 합니다.
4. 폴더 위치 권장 사항
   - Windows의 기본 파일 경로 길이 제한(260자)을 피하기 위해 본 크롤러 폴더를 바탕화면 깊은 하위 폴더 대신 `C:\kb_crawler`처럼 짧은 경로에 두는 것을 권장합니다.

### 폴더 구성 파일

| 파일명 | 설명 |
| :--- | :--- |
| `1_open_chrome_windows.bat` | (Windows용) 크롤링 전용 프로필로 디버깅 모드 Chrome 실행 |
| `2_run_crawler_windows.bat` | (Windows용) 가상환경 자동 구성 및 크롤러 실행 |
| `crawl_sites.py` | 크롤러 메인 Python 스크립트 |
| `requirements.txt` | Python 필수 라이브러리 목록 (`websockets` 단일 패키지) |

---

## 3. 빠른 실행 방법 (Windows 배치 파일 사용, 권장)

터미널 명령어를 직접 입력할 필요 없이 두 개의 배치 파일을 순서대로 더블클릭해 실행합니다.

### 1단계. 전용 Chrome 실행 및 로그인 (`1_open_chrome_windows.bat`)

1. `1_open_chrome_windows.bat` 파일을 더블클릭합니다.
2. 크롤링 전용 프로필(`%USERPROFILE%\chrome-coway-profile`)로 새 Chrome 창이 열립니다.
   - 기존에 띄워 둔 업무용 Chrome 창과 완전히 분리된 별도 프로필이므로 기존 Chrome을 닫을 필요가 없습니다.
3. 새로 열린 Chrome 창에서 사내 Google 계정으로 로그인하고 KB 메인 화면이 정상적으로 표시되는지 확인합니다.
   - 최초 1회만 로그인하면 프로필 폴더에 세션이 저장되어 다음 실행부터는 자동 로그인됩니다.
4. **크롤링이 끝날 때까지 이 Chrome 창을 닫지 말고 켜 둡니다** (최소화해 두어도 무방합니다).

### 2단계. 크롤러 실행 (`2_run_crawler_windows.bat`)

1. `2_run_crawler_windows.bat` 파일을 더블클릭합니다.
2. 최초 실행 시 자동으로 `.venv` 가상환경을 만들고 `websockets` 패키지를 설치한 뒤 크롤링을 시작합니다.
3. 검은색 콘솔 창에 `[CRAWL] 상위메뉴 > 하위메뉴` 진행 로그와 Google Drive 파일 다운로드 상태(`downloaded`, `no_access` 등)가 실시간으로 출력됩니다.
4. 약 3~10분 후 `[완료] 크롤링이 정상 완료되었습니다.` 메시지가 뜨면 `coway_th_kb` 폴더에서 수집 결과를 확인합니다.

---

## 4. 터미널 명령어로 직접 실행하는 방법 (고급 / 개발자용)

배치 파일 대신 명령 프롬프트(CMD), PowerShell, 또는 macOS 터미널에서 직접 실행하거나 옵션을 변경하고 싶을 때 사용합니다.

### Windows PowerShell

**1) 전용 Chrome 실행**

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --remote-allow-origins=http://127.0.0.1:9222 `
  --no-first-run `
  --no-default-browser-check `
  --user-data-dir="$env:USERPROFILE\chrome-coway-profile" `
  "https://sites.google.com/coway.co.th/call-center-kb/%E0%B8%AB%E0%B8%99%E0%B8%B2%E0%B9%81%E0%B8%A3%E0%B8%81"
```

*(Chrome이 사용자 폴더에 설치된 경우 첫 줄 경로를 `"$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"`로 변경하세요.)*

**2) 가상환경 구성 및 크롤러 실행**

```powershell
cd C:\kb_crawler
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe crawl_sites.py --output-dir .\coway_th_kb
```

### Windows 명령 프롬프트 (CMD)

**1) 전용 Chrome 실행**

```cmd
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=http://127.0.0.1:9222 --no-first-run --no-default-browser-check --user-data-dir="%USERPROFILE%\chrome-coway-profile" "https://sites.google.com/coway.co.th/call-center-kb/%%E0%%B8%%AB%%E0%%B8%%99%%E0%%B8%%B2%%E0%%B9%%81%%E0%%B8%%A3%%E0%%B8%%81"
```

*(대화형 CMD 창에 직접 붙여넣을 때는 URL의 `%%` 대신 단일 `%`를 사용합니다.)*

**2) 가상환경 구성 및 크롤러 실행**

```cmd
chcp 65001
cd /d C:\kb_crawler
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
set PYTHONUTF8=1
.venv\Scripts\python.exe crawl_sites.py --output-dir .\coway_th_kb
```

### macOS 터미널

```bash
# 1) 전용 Chrome 실행 및 로그인
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins=http://127.0.0.1:9222 \
  --no-first-run --no-default-browser-check \
  --user-data-dir="$HOME/chrome-coway-profile" \
  "https://sites.google.com/coway.co.th/call-center-kb/%E0%B8%AB%E0%B8%99%E0%B8%B2%E0%B9%81%E0%B8%A3%E0%B8%81"

# 2) 새 터미널 탭에서 가상환경 생성 및 크롤러 실행
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python crawl_sites.py --output-dir ./coway_th_kb
```

### CLI 실행 옵션

다른 국가나 부서의 Google Sites KB를 수집할 때는 `--start-url`과 `--output-dir` 옵션만 변경해 실행하면 됩니다.

| 옵션 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `--start-url` | 태국 콜센터 KB 홈 URL | 수집을 시작할 Google Sites 첫 페이지 URL |
| `--output-dir` | `./coway_th_kb` | 크롤링 결과를 저장할 로컬 폴더 경로 |
| `--site-prefix` | `--start-url`에서 자동 추출 | 수집 범위를 제한할 기준 URL 접두사 (예: `https://sites.google.com/coway.co.th/call-center-kb`) |
| `--cdp-url` | `http://127.0.0.1:9222` | 실행 중인 Chrome 디버깅 포트 주소 |

`2_run_crawler_windows.bat` 뒤에 옵션을 붙여 실행할 수도 있습니다.

```cmd
2_run_crawler_windows.bat --start-url "https://sites.google.com/..." --output-dir "C:\kb_output"
```

---

## 5. 출력 폴더 구조 및 권한 리포트 확인 방법

### 출력 디렉터리 구조

```text
coway_th_kb/
├── _manifest.json                # 전체 수집 페이지 목록, 원본 URL, 저장된 이미지/Drive 파일 수
├── _drive_files.json             # 본문에 연결된 모든 Google Drive 파일의 권한 및 다운로드 결과 리포트
├── _drive_files/                 # 접근 권한이 확인되어 다운로드된 Drive 원본 파일 모음
│   └── 1r1pwlcy_..._NOV 2026 PRICE CARD.pdf
├── _assets/                      # 최상위 단일 페이지의 본문 이미지 폴더
├── 01_หน้าแรก.md                 # 하위 메뉴가 없는 상단 탭 문서
├── 02_AS Defense & Product Manual/
│   ├── 00_overview.md            # 탭 자체에 연결된 페이지
│   └── 01_Product/
│       ├── _assets/              # 하위 페이지별 본문 이미지 폴더
│       │   └── 01_เครื่องกรองน้ำ/
│       │       └── img_01.png
│       └── 01_เครื่องกรองน้ำ.md  # 이미지 및 Drive 파일 로컬 상대 경로가 연결된 본문
└── 99_unlisted_pages/            # 상단 메뉴에는 없지만 본문 내부 링크로 발견된 페이지
```

### Markdown 본문 내 Drive 파일 매핑 방식

다운로드된 파일과 권한이 없어 건너뛴 파일 모두 각 `.md` 문서 안에서 바로 구분하고 찾아볼 수 있도록 기록합니다.

- **다운로드 성공 시 본문 링크**: `[파일명](_drive_files/...) ([Drive](원본 URL))` 형식으로 바뀝니다. 원래 링크 텍스트가 `https://...` 주소였던 경우에도 다운로드된 실제 파일명으로 자동 변경됩니다. (하단 `## Embedded Files (Drive / Docs / Sheets)` 목록에는 참고용 원본 URL이 그대로 유지됩니다.)
- **다운로드 실패 / 건너뜀 시 본문 링크**: 원본 링크 뒤에 `(Drive: no_access)`, `(Drive: not_found)`, `(Drive: folder_skipped)`처럼 상태가 함께 표시됩니다.
- **페이지 하단 매핑 표 (`## Drive Files (Local Mapping)`)**: 해당 페이지에서 참조한 모든 Drive 파일의 원본 ID/링크, 상태, 저장된 로컬 파일 링크를 표로 정리합니다.

```markdown
| Drive file | Status | Local file |
| --- | --- | --- |
| [1eGSe-RK6Ux-6quHRZhqEfQAMpQUZkfZO](https://drive.google.com/file/d/1eGSe-RK6Ux-6quHRZhqEfQAMpQUZkfZO/preview?authuser=0) | downloaded | [OCT 2026 PRICE CARD.pdf](_drive_files/1eGSe-RK6Ux-6quHRZhqEfQAMpQUZkfZO_OCT%202026%20PRICE%20CARD.pdf) |
| [1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn](https://drive.google.com/file/d/1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn/preview?authuser=0) | no_access | - |
```

### `_drive_files.json` (Google Drive 권한 및 다운로드 리포트) 읽는 법

크롤러는 본문에서 발견된 모든 Google Drive / Docs / Sheets / Slides 파일에 대해 현재 로그인한 계정의 접근 가능 여부를 판별해 `_drive_files.json`에 기록합니다. 다운로드가 거부된(`no_access`) 파일에는 Drive 서버가 반환한 안내 문구 또는 HTTP 상태가 `detail` 필드에 함께 저장됩니다.

```json
{
  "summary": {
    "total": 120,
    "downloaded": 95,
    "no_access": 18,
    "not_found": 2,
    "folder_skipped": 5
  },
  "files": [
    {
      "file_id": "1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn",
      "kind": "file",
      "status": "downloaded",
      "accessible": true,
      "filename": "NOV 2026 PRICE CARD.pdf",
      "local_file": "_drive_files/1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn_NOV 2026 PRICE CARD.pdf",
      "size_bytes": 1048576,
      "http_status": 200,
      "source_urls": [
        "https://drive.google.com/file/d/1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn/preview?authuser=0"
      ],
      "referenced_by": [
        "coway_th_kb/07_Promotion.md"
      ]
    },
    {
      "file_id": "1abcDEF...",
      "kind": "spreadsheets",
      "status": "no_access",
      "accessible": false,
      "filename": null,
      "local_file": null,
      "size_bytes": 0,
      "http_status": 200,
      "source_urls": [
        "https://docs.google.com/spreadsheets/d/1abcDEF.../edit"
      ],
      "referenced_by": [
        "coway_th_kb/05_Billing & Payment/02_Fee.md"
      ],
      "detail": "Google Drive - Can't download file Sorry, the owner hasn't given you permission to download this file."
    }
  ]
}
```

#### `status` 값별 의미 및 조치 방법

| `status` 값 | 의미 | 본문 링크 처리 | 권장 조치 |
| :--- | :--- | :--- | :--- |
| `downloaded` | 열람 및 다운로드 권한이 있어 `_drive_files/`에 정상 저장됨 | `[파일명](_drive_files/...) ([Drive](원본 URL))` 치환 및 하단 매핑 표 연결 | 정상 완료 (추가 조치 불필요) |
| `no_access` | 현재 계정에 파일 열람 권한이 없거나, 문서 소유자가 '뷰어의 다운로드 금지' 옵션을 설정함 | 다운로드 건너뛰고 원본 URL 유지 + `(Drive: no_access)` 표기 | `_drive_files.json`의 `detail` 및 `source_urls` 확인 후 소유자에게 **뷰어 권한 및 다운로드 허용** 요청 후 재크롤링 |
| `not_found` | 원본 파일이 Drive에서 삭제되어 404 응답이 반환됨 | 원본 Drive URL 유지 + `(Drive: not_found)` 표기 | `referenced_by`에 적힌 KB 페이지에서 깨진 링크 정리 |
| `folder_skipped` | 개별 파일이 아닌 Google Drive 폴더 전체 링크임 | 원본 폴더 URL 유지 + `(Drive: folder_skipped)` 표기 | 폴더 내부 파일이 필요할 경우 KB 페이지에 개별 파일로 링크하거나 별도 공유 드라이브 동기화 사용 |
| `error` | 일시적 네트워크 오류로 다운로드 실패 | 원본 Drive URL 유지 + `(Drive: error)` 표기 | 네트워크 확인 후 재실행 |

---

## 6. Windows 작업 스케줄러를 이용한 주기적 자동 실행

Windows 서버나 상시 켜져 있는 운영 PC에서 매일 새벽 자동으로 최신 KB를 수집하려면 아래 순서로 등록합니다.

1. 해당 Windows 계정에 로그인한 상태에서 `1_open_chrome_windows.bat`을 실행해 사내 Google 계정 로그인을 마치고 Chrome 창을 켜 둡니다.
   - 재부팅 시에도 Chrome이 자동으로 뜨도록 하려면 `Win + R` -> `shell:startup` 입력 후 열리는 시작프로그램 폴더에 `1_open_chrome_windows.bat`의 바로가기를 넣어 둡니다.
2. Windows 검색창에서 **작업 스케줄러(Task Scheduler)**를 실행하고 우측의 **기본 작업 만들기**(또는 **작업 만들기**)를 클릭합니다.
3. 일반 탭에서 **`사용자가 로그온할 때만 실행`**을 선택합니다 (켜져 있는 Chrome 세션과 통신해야 하므로 백그라운드 비로그온 세션이 아닌 현재 사용자 세션에서 실행되어야 합니다).
4. 트리거 탭에서 실행 주기를 설정합니다 (예: 매일 오전 03:00).
5. 동작 탭에서 **프로그램 시작**을 선택하고 아래와 같이 입력합니다.
   - 프로그램/스크립트: `cmd.exe`
   - 인수 추가: `/c "set KB_UNATTENDED=1 && C:\kb_crawler\2_run_crawler_windows.bat"`
   - 시작 위치: `C:\kb_crawler`
   - (`KB_UNATTENDED=1`을 지정하면 완료 후 키 입력을 기다리는 `pause` 없이 자동 종료됩니다.)
6. 보안 정책상 일정 기간(예: 14일 또는 30일)마다 Google 세션 재인증이 요구될 수 있습니다. 작업이 종료 코드 `1`(`Chrome session is not logged into Google Sites yet`)로 실패하면 켜져 있는 Chrome 창에서 한 번만 다시 로그인해 주면 됩니다.

---

## 7. 문제 해결 (Troubleshooting)

### `RuntimeError: Could not open CDP tab at http://127.0.0.1:9222`
- 원인: `1_open_chrome_windows.bat`으로 띄운 전용 Chrome 창이 닫혀 있거나, 디버깅 포트 옵션 없이 바탕화면의 일반 Chrome 아이콘으로 실행한 경우입니다.
- 해결: `1_open_chrome_windows.bat`을 먼저 실행해 전용 Chrome 창을 띄운 뒤 다시 `2_run_crawler_windows.bat`을 실행하세요.

### `RuntimeError: Chrome session is not logged into Google Sites yet.`
- 원인: 전용 Chrome 창에서 아직 사내 Google 계정 로그인이 완료되지 않았거나 로그인 세션이 만료되었습니다.
- 해결: 열려 있는 전용 Chrome 창에서 Google 계정 로그인을 완료하고 KB 첫 페이지가 정상 표시되는 것을 확인한 뒤 다시 실행하세요.

### `[오류] Python 3.10 이상이 설치되어 있지 않거나 PATH에 등록되지 않았습니다.`
- 원인: Python이 미설치 상태이거나 설치 시 PATH 환경변수 등록이 누락되었습니다.
- 해결: Python 설치 프로그램을 다시 실행해 `Modify` -> `Next` -> `Add Python to environment variables`를 체크하거나, 새로 설치할 때 `Add python.exe to PATH`를 체크하세요.

### 브라우저에서는 보이는 Google Drive 문서인데 `no_access`로 분류되는 경우
- 원인 1: 평소 쓰던 업무용 Chrome에는 권한 있는 계정으로 로그인되어 있지만, 크롤링 전용 Chrome(`chrome-coway-profile`)에는 다른 계정으로 로그인된 경우입니다. 전용 Chrome 창에서 해당 `source_urls` 주소를 직접 열어 계정을 확인하세요.
- 원인 2: 문서 소유자가 Google Drive 공유 설정에서 **뷰어 및 댓글 작성자의 다운로드, 인쇄, 복사 옵션 사용 중지**를 켜 둔 경우입니다. 이 경우 화면 열람만 가능하고 파일 내보내기가 차단되므로 문서 소유자에게 다운로드 허용을 요청해야 합니다.

### 파일 저장 중 경로 오류(`FileNotFoundError` / `WinError 206`)가 발생하는 경우
- 원인: 크롤러 폴더가 너무 깊은 하위 경로(예: `C:\Users\홍길동\OneDrive - ...\문서\프로젝트\...\kb_crawler_sample`)에 있어 전체 파일 경로가 Windows 260자 제한을 넘은 경우입니다.
- 해결: 크롤러 폴더를 `C:\kb_crawler`처럼 짧은 루트 경로로 옮긴 뒤 실행하거나, `--output-dir C:\coway_kb` 옵션을 지정하세요.
