# 지식베이스 GCP 인프라 배포 가이드 (`kb_pipeline/terraform/`)

지식베이스 동기화 파이프라인에 필요한 GCP 리소스(비공개 Cloud Storage 버킷, Vertex AI Search DataStore, 최소 권한 IAM)를 Terraform으로 생성합니다. 최초 환경 구축 시 GCP 인프라 담당자가 로컬 PC 또는 Google Cloud Shell에서 1회 실행합니다.

## 생성 리소스

| 파일 | 생성 대상 |
| :--- | :--- |
| `main.tf` | 필수 GCP API(`storage`, `discoveryengine`, `ces`, `dialogflow`, `iam`, `cloudresourcemanager`) 활성화, 비공개 Cloud Storage 버킷(`prevent_destroy = true`, 버전 관리 활성화), Vertex AI Search 비정형 DataStore(`CHUNKS` 청크 검색 모드 + `Layout Parser` 표·이미지 파서 활성화) |
| `iam.tf` | 동기화 실행 계정(`operator_members`)의 버킷 권한 및 `kbSyncImporter` 커스텀 역할, CX Agent Studio(`@gcp-sa-ces`) 및 Dialogflow(`@gcp-sa-dialogflow`) 서비스 에이전트 생성 및 DataStore 조회 권한 |
| `outputs.tf` | `kb_bucket_name`, `datastore_id`, `datastore_location`, `datastore_name` |

## IAM 권한 요약

| 주체 | 부여 역할 | 범위 및 용도 |
| :--- | :--- | :--- |
| 동기화 실행 계정 (`operator_members`) | `roles/storage.objectUser`, 프로젝트 커스텀 역할 `kbSyncImporter` | KB 버킷 오브젝트 읽기/쓰기(스냅샷, 전처리 문서, 매니페스트, 상태 파일) 및 Vertex AI Search `ImportDocuments` 실행/상태 조회 |
| CX Agent Studio 서비스 에이전트 (`service-<PROJECT_NUMBER>@gcp-sa-ces.iam.gserviceaccount.com`) | `roles/discoveryengine.viewer` | Vertex AI Search DataStore 검색 조회 |
| Dialogflow 서비스 에이전트 (`service-<PROJECT_NUMBER>@gcp-sa-dialogflow.iam.gserviceaccount.com`) | `roles/discoveryengine.viewer` | Companion Agent의 Vertex AI Search DataStore 검색 조회 |

## 사전 확인 사항

1. 실행 도구 및 인증: Terraform `1.5` 이상과 `gcloud` CLI가 설치되어 있어야 합니다. 로컬 PC에서 실행할 경우 먼저 아래 명령으로 GCP 인증(ADC)을 수행합니다 (Cloud Shell에서는 자동 인증됨).
   ```bash
   gcloud auth application-default login
   ```
2. 필요 권한: 실행 계정에 대상 GCP 프로젝트의 API 활성화, 리소스 생성, IAM 부여 권한(예: `Owner` 또는 `Editor` + `Project IAM Admin`)이 있어야 합니다.
3. 리전 설정 주의 (`region` vs `datastore_location`):
   - `region`(기본 `asia-northeast3`): 원본 스냅샷, 전처리 문서, 매니페스트, 상태 파일을 보관할 Cloud Storage 버킷의 저장 리전입니다.
   - `datastore_location`(`us`, `eu`, `global`): CX Agent Studio는 `us`, `eu`, `global` 멀티리전의 DataStore만 연결할 수 있으므로 반드시 연결할 CX Agent Studio 앱의 리전과 동일하게 지정해야 합니다. 버킷 리전과 달라도 정상 연동되며, 생성 후 변경하면 기존 색인이 비워지고 새 DataStore가 만들어집니다.
4. 상태 파일(`terraform.tfstate`) 보관: 실수로 원본 아카이브 버킷이 삭제되지 않도록 `prevent_destroy = true`가 설정되어 있습니다. 로컬에서 실행한 경우 생성된 `terraform.tfstate`를 분실하지 않도록 안전하게 보관하거나, 팀 표준 GCS 원격 백엔드(`backend "gcs"`)를 추가해 관리하는 것을 권장합니다.

## 배포 절차

```bash
cd kb_pipeline/terraform
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars` 파일을 열어 환경에 맞게 값을 입력합니다.

```hcl
project_id     = "your-gcp-project-id"
region         = "asia-northeast3"
kb_bucket_name = "your-gcp-project-id-coway-kb"

# 연결할 CX Agent Studio 앱 리전(us, eu, global)과 동일하게 설정합니다.
datastore_location = "us"
datastore_id       = "coway-kb"

# sync_kb_windows.bat 또는 sync_kb.py를 실행할 계정
# 수동 실행은 user:..., 작업 스케줄러 무인 실행(KB_KEY_FILE_DEFAULT)은 serviceAccount:... 를 등록합니다.
operator_members = [
  "user:operator@example.com",
]
```

초기화 및 배포를 실행합니다.

```bash
terraform init
terraform apply
```

## 배포 후 전달 항목 (`terraform output`)

| 출력값 | 예시 형식 | 전달 대상 및 설정 위치 |
| :--- | :--- | :--- |
| `kb_bucket_name`, `datastore_location`, `datastore_id` | `your-project-coway-kb`, `us`, `coway-kb` | KB 동기화 담당자 -> `kb_pipeline/sync/sync_kb_windows.bat` 상단 기본값 ([동기화 가이드](../README.md)) |
| `datastore_name` | `projects/1234567890/locations/us/collections/default_collection/dataStores/coway-kb` | 에이전트 구성 담당자 -> CX Agent Studio `DataStoreTool` 연결 대상 ([에이전트 설정 가이드](../docs/agent-datastore-instructions.md)) |
