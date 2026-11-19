# 지식베이스 동기화 파이프라인 연동 규격 (`CONTRACT.md`)

비공개 Google Sites 크롤링 결과(`coway_th_kb/`)를 비공개 Cloud Storage 버킷과 Vertex AI Search DataStore에 적재하고, CX Agent Studio 에이전트(및 선택적 Companion Agent)에서 텍스트 전용으로 안전하게 참조하기 위한 공통 계약서입니다.

## 핵심 용어

| 용어 | 정의 |
| :--- | :--- |
| `Knowledge Snapshot` | 1회 동기화 실행 시 생성되는 시점별 수집본 (`UTC YYYY-MM-DD_HHMMSS`) |
| `Knowledge Page` | Google Sites에서 수집한 개별 웹 문서 (`.md` 원본을 시맨틱 `.html`로 변환해 색인하며, PNG/JPG 이미지는 `Layout Parser` 판독용으로만 인라인 포함) |
| `Manual Document` | 페이지 본문에서 참조되어 로컬로 수집된 Google Drive 첨부 문서 (`pdf`, `docx`, `pptx`, `xlsx`, `xlsm`, 200 MB 이하) |

## 구성 요소

| 구분 | 경로 | 역할 |
| :--- | :--- | :--- |
| 동기화 CLI | `kb_pipeline/sync/` (`sync_kb.py`, `html_render.py`, `sync_kb_windows.bat`) | 로컬 안전 차단기 검사, 원본 스냅샷 보관, 시맨틱 HTML(무손실 PNG/JPG Base64 `<figure>` 인라인) 변환, `ImportDocuments(FULL)` 호출 |
| 인프라 | `kb_pipeline/terraform/` | 비공개 Cloud Storage 버킷, Vertex AI Search DataStore(`Layout Parser` + `CHUNKS`), 최소 권한 IAM |
| 연동 문서 | `kb_pipeline/docs/` | 에이전트 `DataStoreTool` 설정, 텍스트 전용 지시문, 회귀 평가 가이드 ([agent-datastore-instructions.md](docs/agent-datastore-instructions.md)) |

## 비공개 버킷 구조 (`KB_BUCKET`)

```text
snapshots/<snapshot_id>/raw/...          크롤링 원본 폴더 전체 아카이브 (영구 보관)
processed/blobs/<sha256>.<ext>           내용 해시 기반 변환 HTML 페이지 및 Drive 매뉴얼 문서
manifests/<snapshot_id>/documents.jsonl  이번 실행의 Vertex AI Search 가져오기 매니페스트
state/last_success.json                  직전 정상 반영 스냅샷 메타데이터 (차단기 기준값)
```

- `snapshot_id`는 UTC 기준 `YYYY-MM-DD_HHMMSS` 문자열입니다.
- `processed/blobs/`는 내용 해시(SHA-256) 주소로 저장되며 이미 존재하는 객체는 덮어쓰지 않습니다. 따라서 `ReconciliationMode.FULL` 실행 시 변경되지 않은 문서는 재파싱 없이 건너뛰고, 삭제된 문서만 색인에서 자동 제거됩니다.
- 버킷은 `public_access_prevention = "enforced"`가 적용된 완전 비공개 저장소이며, 이미지나 매뉴얼 파일을 외부 URL로 제공하지 않습니다.

## HTML 변환 규칙 (`html_render.py`)

- 상단 YAML 프론트매터는 제거하고 `<body>` 첫 줄에 `[<category_path> | <title>]` 문맥 머리줄을 삽입합니다.
- 제목(`<h1>`..`<h6>`), 표(`<table>`), 목록(`<ul>`/`<ol>`)을 시맨틱 HTML 태그로 변환해 Vertex AI Search `Layout Parser`가 제목 계층 단위로 청크를 나누고(`include_ancestor_headings = true`) 표를 구조화하도록 합니다.
- 본문 내 로컬 PNG 및 JPG/JPEG 이미지는 리사이즈나 압축 없이 원본 바이트 그대로 아래 구조로 인라인 삽입합니다.
  ```html
  <figure><img src="data:image/png;base64,..." alt="..." /></figure>
  ```
  JPG/JPEG 이미지는 `data:image/jpeg;base64,...` 형식을 사용합니다. Vertex AI Search `Layout Parser`(`enable_image_annotation = true`)는 이 인라인 Base64 이미지를 원본 품질 그대로 판독해 이미지 내부의 글자, 표, 도식 의미를 검색 가능한 청크 주석으로 색인합니다. HTML 안에는 `<figcaption>`, `![alt](URL)`, 외부 이미지 URL을 생성하지 않습니다.
- `.gif` 및 기타 미지원 이미지 형식(`.webp`, `.svg` 등)은 Base64 `<figure>`와 URL 태그를 모두 생략하며, 빈 `<p></p>`, 빈 `<li></li>`, 불필요한 `<br />` 간격 없이 주변 단계별 본문 텍스트만 그대로 유지합니다.
- 안전 폴백: 단일 HTML 페이지 크기가 `9.5 MB`(`MAX_HTML_BYTES = 9_500_000`, Discovery Engine 10 MB 한도 미만)를 초과하면 별도 이미지 압축 라이브러리 없이 해당 페이지의 Base64 `<figure>` 태그만 생략하고 본문 텍스트 전체를 보존합니다.
- 정상 다운로드된 Drive 첨부 링크는 `<라벨> (매뉴얼 문서: <파일명>)` 텍스트로 치환하고, 미수집 링크(`no_access`, `not_found`, `folder_skipped`)와 하단 Drive 요약 섹션은 제거합니다. 변환 결과에는 `drive.google.com`, `docs.google.com`, `gs://` 주소가 남지 않습니다.

## 매뉴얼 문서 및 매니페스트 (`documents.jsonl`)

- `_drive_files.json`에서 `status == "downloaded"`이고 확장자가 `pdf`, `docx`, `pptx`, `xlsx`, `xlsm`이며 크기가 200 MB 이하인 파일을 `processed/blobs/<sha256>.<ext>`에 업로드합니다.
- 문서 ID는 결정적 형식을 사용합니다 (`page-<32자 해시>`, `drive-<file_id>`). `structData`에는 `{title, uri, doc_type, category_path}`만 기록하며 실행 시각이나 스냅샷 ID는 넣지 않습니다.
- `ImportDocuments`는 `data_schema = "document"`, `reconciliation_mode = FULL`로 호출합니다.

## 로컬 안전 차단기 (`sync_kb.py`)

GCS 업로드 및 DataStore 반영 전에 로컬에서 다음 조건을 검사합니다.

- 수집된 페이지가 0건인 경우: 항상 `BLOCKED`(종료 코드 `2`) 처리합니다.
- 홈 페이지(`--root-page-url`)가 누락되었거나 본문이 50자 미만인 경우: `--force-sync` 없이는 `BLOCKED` 처리합니다.
- 직전 성공 기록(`state/last_success.json`) 대비 페이지 수가 `min_page_ratio`(기본 `0.8`, 80%) 미만으로 급감했거나, 기존 매뉴얼 문서가 1건 이상이었는데 0건으로 떨어진 경우(Drive 권한 만료 등): `--force-sync` 없이는 `BLOCKED` 처리합니다.

## 고객 응답 원칙 (텍스트 전용)

- 고객과 상담사에게 전달되는 에이전트 답변은 텍스트 전용입니다. HTML 본문, 표, `Layout Parser` 이미지 주석, Drive 매뉴얼 문서에서 검색된 사실만 자연스러운 텍스트로 안내합니다.
- 이미지 URL, Markdown 이미지 문법(`![...](...)`), 내부 `snippets[].uri`, `gs://`, `drive.google.com`, `docs.google.com`, 매뉴얼 원본 파일 다운로드 링크는 어떤 경로로도 외부에 출력하거나 제공하지 않습니다. 상세 프롬프트 및 회귀 평가 규칙은 [docs/agent-datastore-instructions.md](docs/agent-datastore-instructions.md)를 따릅니다.

## 최소 권한 IAM (`iam.tf`)

| 주체 | 부여 권한 |
| :--- | :--- |
| 운영 담당자 (`operator_members`) | 버킷 `roles/storage.objectUser` + 프로젝트 커스텀 역할 `kbSyncImporter` (`discoveryengine.documents.import`, `operations.get` 등) |
| CX Agent Studio 서비스 에이전트 (`@gcp-sa-ces`) | `roles/discoveryengine.viewer` |
| Dialogflow 서비스 에이전트 (`@gcp-sa-dialogflow`) | `roles/discoveryengine.viewer` |
