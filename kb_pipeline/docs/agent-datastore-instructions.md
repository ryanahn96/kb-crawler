# 지식베이스 DataStoreTool 설정 및 에이전트 지시문 가이드

지식베이스 동기화 파이프라인이 적재한 Vertex AI Search DataStore를 **CX Agent Studio 에이전트**(`Agent Session`, 고객과 직접 대화하는 에이전트)와 선택적 **Companion Agent**(`Assist Session`, 상담사 화면 옆에서 답변 초안과 지식을 보조하는 에이전트)에서 안전하게 활용하기 위한 도구 설정값, 프롬프트 지시문, 회귀 평가 기준을 정리한 문서입니다.

> 문서 범위 안내
> - 대상: `DataStoreTool` 연결 설정, 도구 설명문(`description`), 에이전트 지시문, 회귀 평가 케이스
> - 제외: 인프라 배포 및 동기화 실행([파이프라인 가이드](../README.md)), 이미지 중계 API 구현([미들웨어 명세서](middleware-kb-assets-spec.md))
> - 연동 기준: [CONTRACT.md](../CONTRACT.md)
> - 언어 안내: 모델의 규칙 준수율을 높이기 위해 3절, 5절, 6절, 7절에 붙여 넣는 프롬프트 원문은 영문으로 작성되어 있으며, 실제 답변 언어는 `<language>` 규칙에 따라 고객의 질문 언어(태국어·한국어·영어 등)로 자동 생성됩니다.

---

## 1. 에이전트가 전달받는 검색 결과 구조

### 1.1 네이티브 `DataStoreTool` 응답 스키마

네이티브 `DataStoreTool`이 런타임에 모델로 전달하는 페이로드는 아래 구조입니다. 문서의 `structData`(`doc_type`, `category_path`)는 모델에 직접 전달되지 않으므로, 카테고리 경로와 이미지 URL은 모두 본문(`snippets[].text`) 안에 포함되어야 합니다.

```json
{
  "answer": "<내장 요약기가 생성한 답변 문장>",
  "snippets": [
    {"uri": "<문서의 원본 내부 주소>", "title": "<문서 제목>", "text": "<검색된 본문 청크>"}
  ]
}
```

| 필드 | 내용 | 고객 응답 활용 규칙 |
| :--- | :--- | :--- |
| `answer` | 내장 요약기가 생성한 요약 문장. 요약 과정에서 이미지 태그가 생략될 수 있음 | 사실 확인에만 참고하고 이미지 태그는 `snippets[].text`에서 직접 가져옴 |
| `snippets[].text` | `Layout Parser`가 분할한 본문 청크 및 표·이미지 주석 텍스트. 고정 중계 이미지 태그(`![alt](URL)`)가 포함됨 | 답변 사실 근거 및 이미지 태그 복사 출처로 사용 |
| `snippets[].title` | `Knowledge Page` 제목 또는 `Manual Document` 파일명 | 문서명 언급은 가능하나 다운로드 링크 제시 금지 |
| `snippets[].uri` | 원본 Google Sites 내부 페이지 주소 | 고객 화면 노출 절대 금지 (사내 로그인 없이는 열리지 않는 내부 주소) |

### 1.2 검색 청크(`snippets[].text`)의 본문 형식

동기화 파이프라인(`html_render.py`)은 각 `Knowledge Page`를 시맨틱 HTML(`<h1>`..`<h6>`, `<table>`, `<ul>`/`<ol>`)로 변환하며, 로컬 이미지를 인라인 Base64 `<img>`와 `<figcaption>` + `<p>` 중계 URL 쌍으로 삽입합니다. Vertex AI Search `Layout Parser`(`enable_table_annotation = true`, `enable_image_annotation = true`, `include_ancestor_headings = true`)는 이를 파싱해 제목 계층과 이미지·표 내 텍스트를 색인하고 아래 요소들을 청크 텍스트에 보존합니다.

```text
[AS Defense & Product Manual > Product > 정수기 | 필터 교체 주기]

필터 교체 주기는 모델에 따라 다릅니다.
![filter-steps](https://<미들웨어 호스트>/api/kb-assets/3f2a...c91e.png)

자세한 절차는 Filter Guide (매뉴얼 문서: filter_guide_2026.pdf)를 참고합니다.
```

| 요소 | 형식 | 에이전트 처리 규칙 |
| :--- | :--- | :--- |
| 문맥 머리줄 및 상위 제목 | `[<category_path> \| <title>]` 및 상위 섹션 제목 | 제품군·카테고리 문맥 파악에만 사용 |
| 이미지 태그 | `![<대체 텍스트>](<ASSET_BASE_URL>/<sha256>.<ext>)` | 답변 내용을 뒷받침하는 이미지면 태그 전체를 한 글자도 바꾸지 않고 응답에 그대로 복사 |
| 표·이미지 판독 내용 | `Layout Parser`가 추출한 표 행/열 및 이미지 내 수치·절차 텍스트 | 고객 언어에 맞게 자연스러운 문장으로 답변 작성 |
| 매뉴얼 참조 표기 | `<라벨> (매뉴얼 문서: <파일명>)` | 매뉴얼 내용은 답변에 활용하되, 파일 전송이나 다운로드 링크는 제공하지 않음 |

`Manual Document`(`pdf`, `docx`, `pptx`, `xlsx`, `xlsm`) 역시 동일한 DataStore에 별도 문서(`drive-<file_id>`)로 색인되어 표와 본문 청크로 함께 검색됩니다. 전처리 단계에서 모든 `drive.google.com`, `docs.google.com`, `gs://` 주소는 제거됩니다.

---

## 2. `DataStoreTool` 설정

### 2.1 사전 조건

| 항목 | 내용 | 담당 |
| :--- | :--- | :--- |
| Vertex AI Search DataStore | Terraform(`kb_pipeline/terraform/`)이 생성한 단일 비정형 DataStore(`datastore_name` 출력값). 리전(`us`, `eu`, `global`)은 CX Agent Studio 앱 리전과 동일해야 함 | 인프라 담당자 |
| 서비스 에이전트 권한 | `service-<PROJECT_NUMBER>@gcp-sa-ces.iam.gserviceaccount.com` 및 `service-<PROJECT_NUMBER>@gcp-sa-dialogflow.iam.gserviceaccount.com`에 `roles/discoveryengine.viewer` 부여 (Companion Agent를 쓰지 않더라도 읽기 전용 권한이므로 그대로 두면 됨) | 파이프라인 Terraform 자동 부여 |
| 최초 데이터 적재 | `kb_pipeline/sync/sync_kb_windows.bat` 또는 `sync_kb.py`가 종료 코드 `0`(`"[성공] 페이지 N건 / 매뉴얼 M건 반영"`)으로 1회 이상 완료된 상태 | KB 동기화 담당자 |

검색은 고객 개인 계정이 아니라 서비스 에이전트 권한으로 실행됩니다. 따라서 DataStore에 적재된 모든 문서는 고객 응대 답변의 근거로 사용될 수 있습니다. 고객에게 절대 안내해서는 안 되는 내부 전용 문서는 원본 사이트에서 분리하거나 별도 DataStore로 구성해야 합니다.

### 2.2 콘솔 등록 순서 및 권장 설정값

CX Agent Studio 콘솔에서 도구를 연결하는 순서는 다음과 같습니다.

1. 대상 CX Agent Studio 앱(`us`, `eu`, `global` 중 DataStore와 같은 리전)을 열고 **Tools -> Create Tool -> Data Store**를 선택합니다.
2. 도구 이름에 `coway_th_kb_search`(예시)를 입력하고, 연결할 DataStore로 Terraform이 생성한 `coway-kb`(`datastore_name` 출력값)를 선택합니다.
3. 도구 `Description` 칸에 아래 5절의 문구를 그대로 붙여 넣습니다.
4. 스니펫 및 그라운딩 옵션을 아래 표와 같이 설정하고 저장한 뒤, 지식을 조회할 에이전트에 도구를 연결하고 6절의 지시문을 추가합니다.

| 설정 항목 | 권장값 | 이유 |
| :--- | :--- | :--- |
| 모달리티 (`modality_type`) | `TEXT` | 텍스트 채팅 채널 기준 (`VOICE` 채널 병행 시 `AUDIO` 별도 구성) |
| 스니펫 반환 (`snippets_config.enable_snippets`) | `true` | `false`일 경우 `snippets[].text`가 생략되어 이미지 태그(`![alt](URL)`)를 전달받을 수 없음 |
| 최대 스니펫 수 (`snippets_config.max_snippets`) | `5` | 관련 청크와 이미지를 충분히 확보하면서 무관한 이미지 혼입을 억제하는 시작값 (평가 결과에 따라 조정) |
| 그라운딩 (`grounding_config`) | `grounding_level = 3`, `disabled = false` | 검색 결과 외 추측성 답변 억제 |
| 도구 설명문 (`description`) | 5절 문구 적용 | 도구 호출 경계 정의 (Companion Agent Reactive 모드 공유 시 출력 규칙 역할 겸임) |

고객사가 별도의 Terraform 저장소로 CX Agent Studio 에이전트를 관리하는 경우(본 저장소의 `kb_pipeline/terraform/`과는 별도), 모달리티 설정 블록 예시는 다음과 같습니다.

```hcl
modality_configs {
  modality_type = "TEXT"
  grounding_config {
    grounding_level = 3
    disabled        = false
  }
  snippets_config {
    enable_snippets = true
    max_snippets    = 5
  }
}
```

이 문서에서는 도구 이름 예시로 `coway_th_kb_search`를 사용합니다. 실제 도구 이름을 다르게 지정한 경우 6절과 7절의 `{@TOOL: ...}` 및 리소스 경로를 실제 이름에 맞게 변경합니다.

---

## 3. 내장 요약기의 이미지 태그 생략 대응

`DataStoreTool` 응답의 `answer` 필드는 내장 요약기가 검색 청크를 요약한 문장이므로 `![alt](URL)` 이미지 태그가 생략되거나 변형될 수 있습니다. 에이전트가 이미지를 빠짐없이 표시하도록 아래 두 가지를 적용합니다.

1. **에이전트 지시문에서 `snippets[].text`를 이미지 태그의 기준 출처로 명시 (필수)**: 6절 지시문에 반영되어 있습니다. `answer`에 이미지 태그가 없더라도 `snippets[].text`에서 관련 이미지 태그를 찾아 그대로 복사합니다.
2. **도구 요약 프롬프트 사용자 지정 (선택 권장)**: 콘솔 또는 API에서 `DataStoreTool`의 요약 프롬프트를 지정할 수 있는 경우 아래 규칙을 함께 설정합니다.

```text
Rules for writing the answer from the retrieved text:
- The retrieved text may contain image tags of the form ![alt](URL). When an image supports the
  answer, copy that entire tag into the answer unchanged, on its own line, right after the sentence
  it supports. Never change, shorten, or rebuild the URL inside it.
- Copy numbers, prices, dates, model names, and phone numbers exactly as written in the retrieved text.
- Never output any URL other than the one inside a copied ![alt](URL) tag.
- Do not mention the retrieved documents ("according to the sources", "the document says").
```

---

## 4. 배포 전 필수 스모크 검증

에이전트 지시문을 확정하기 전에 실제 적재된 DataStore를 대상으로 아래 4단계를 먼저 확인합니다.

1. 이미지가 포함된 대표 문서(표가 많은 페이지, 다단 제품 스펙 페이지, 단계별 가이드 페이지 등) 5건 이상을 골라 테스트 질문을 준비합니다.
2. CX Agent Studio 미리보기(Preview) 또는 API로 질문을 실행하고 `DataStoreTool` 호출 원문(Tool Output)을 확인합니다.
3. `snippets[].text` 안에 다음 항목이 정상 포함되는지 점검합니다.
   - `![`로 시작해 `.png)`, `.jpg)`, `.webp)`, `.gif)`, `.svg)`로 끝나는 이미지 태그가 잘리지 않고 들어 있는지
   - URL 형식이 `<ASSET_BASE_URL>/<64자 소문자 16진수 해시>.<확장자>`와 일치하는지
   - 에이전트 최종 답변에 해당 `![alt](URL)` 태그가 변형 없이 출력되고 내부 `uri`는 노출되지 않는지
4. 채팅 화면에서 해당 이미지 URL이 C# 미들웨어를 통해 정상 렌더링되는지 확인합니다 ([미들웨어 명세서](middleware-kb-assets-spec.md) 9절 참조).

---

## 5. 도구 설명문 (`DataStoreTool.description`)

도구의 `description` 필드에 아래 문구를 입력합니다. 호출 경계를 명확히 정의하며, 향후 Companion Agent에서 이 도구를 공유할 때도 Reactive 모드의 출력 지시문으로 그대로 동작합니다.

```text
Searches the Coway Thailand call center knowledge base: product information and specifications,
usage and care instructions, troubleshooting steps, error indicators, service policies, rental and
fee rules, and current promotions and prices.

Use this tool for general "What is...?", "How do I...?", "How much is...?" and policy questions.
Always call it before answering such a question, including when a promotion or price was mentioned
earlier in the conversation, because promotions end and prices change.

Critical limitation: this tool cannot access, check, or change any customer-specific data. Do not use
it for contract status, payment or billing records, visit schedules, booking, cancellation, or any
other account action.

How to use the result:
- Answer only with facts found in the result. If the result does not contain the answer, say that the
  information is not available.
- Snippets may contain Markdown image tags of the form ![alt](URL). When an image directly supports
  the answer (such as a product spec chart, indicator guide, or step-by-step diagram), copy its
  ![alt](URL) tag into the reply verbatim on its own line. Never invent, modify, or shorten any URL.
- Never output a snippet's "uri" or any other web link. They are internal addresses.
- "(매뉴얼 문서: <file name>)" marks an internal reference manual. You may use its facts in your
  answer, but never offer the file, a download, or a link to it.
```

---

## 6. CX Agent Studio 에이전트 지시문

### 6.1 에이전트 지시문 블록

`DataStoreTool`이 연결된 에이전트의 지시문(Instructions)에 아래 블록을 추가합니다. `{@TOOL: coway_th_kb_search}` 부분은 실제 등록한 도구 이름으로 바꿉니다. 도구가 연결되지 않은 하위 에이전트에서 참조 오류가 나지 않도록 해당 도구를 보유한 에이전트의 지시문에 배치하는 것을 권장합니다.

```xml
<knowledge_base_rules>
  <when_to_search>
    - For any question about products, specifications, usage, care, troubleshooting, error
      indicators, service policies, fees, promotions, or prices, your first action is to call
      {@TOOL: coway_th_kb_search}. Do not give any answer or partial answer before the tool returns.
    - Call the tool again for every new question of this kind. Never answer a promotion or price
      question from memory, from a prior session summary, or from an earlier turn. Promotions end
      and prices change; only the current tool result is valid.
    - If a suggested troubleshooting step did not work, call the tool again with a more specific
      query that states what was already tried.
    - Do NOT call {@TOOL: coway_th_kb_search} for requests about the customer's own contract,
      payment history, visit schedule, booking, change, or cancellation. The knowledge base holds no
      customer data. Use the dedicated account/transaction tool or escalate to a human counselor.
  </when_to_search>

  <grounding>
    - Answer only with facts contained in the tool result of the current turn. Never invent,
      extend, or guess steps, conditions, prices, dates, or model names.
    - Numbers, prices, dates, model names, and phone numbers must appear exactly as in the result.
    - If the result is empty or does not answer the question, state clearly that you do not have
      that information and offer to connect a human counselor. Do not fill gaps with general knowledge.
    - Do not refer to internal sources ("according to the document", "the search result says").
  </grounding>

  <images>
    - Inspect the snippets' "text" for Markdown image tags of the form ![alt](URL). Do not rely only
      on the tool's "answer" field, which may omit image tags.
    - When an image in the current tool result directly supports what you are explaining (a diagram
      of the step, a product image paired with specs, or an indicator table), copy the entire
      ![alt](URL) tag verbatim, character for character, on its own line right after the sentence it
      supports.
    - Never write an image tag that is not present in the current tool result. Never change,
      shorten, translate, re-encode, or rebuild a URL, and never alter the alt text.
    - Include only images that directly support the answer, at most 3 per reply. If no image is
      relevant, include none.
  </images>

  <links_and_manuals>
    - The only URLs you may output are those inside a copied ![alt](URL) tag.
    - Never output a snippet's "uri" or any other web address. They are internal addresses that
      customers cannot open.
    - "(매뉴얼 문서: <file name>)" marks an internal manual. You may explain the facts from that
      manual, but never offer the file, a download, or a link. If the customer asks for the manual
      file, explain that files cannot be sent directly and summarize the needed steps instead.
  </links_and_manuals>

  <language>
    - Reply in the customer's language. Knowledge base content may be written in Thai, English, or
      Korean; translate the facts naturally into the customer's language while copying ![alt](URL)
      image tags unchanged.
  </language>
</knowledge_base_rules>
```

### 6.2 요구사항 매핑

| 핵심 요구사항 | 지시문 섹션 | 회귀 평가 케이스 (8절) |
| :--- | :--- | :---: |
| 답변 근거 이미지의 `![alt](URL)` 태그를 스니펫에서 원문 복사 | `<images>` | E1, E3 |
| 임의 이미지 URL 생성·변형 차단 | `<images>`, `<links_and_manuals>` | E2, E3 |
| 내부 사이트 주소(`snippets[].uri` 등) 노출 차단 | `<links_and_manuals>` | E4 |
| 매뉴얼 원본 파일 다운로드·링크 제공 차단 | `<links_and_manuals>` | E5 |
| 검색 결과 기반 응답 및 환각 방지 | `<grounding>` | E6, E7 |
| 종료된 프로모션의 이전 대화·요약 재사용 차단 | `<when_to_search>` | E6 |
| 계약·결제·예약 등 고객별 거래 요청 시 KB 검색 생략 | `<when_to_search>` | E8 |

### 6.3 미들웨어 및 프런트엔드 연동 유의사항

- 채팅 프런트엔드는 에이전트 답변 내 `![alt](URL)` Markdown 이미지 태그를 렌더링해야 합니다. SSE 스트리밍 환경에서는 청크 경계에서 이미지 태그가 잘린 채 렌더링되지 않도록 누적 텍스트 기준으로 파싱합니다.
- API 응답 메타데이터(`citations` 등)에 문서의 내부 `uri`가 포함되더라도 고객 채팅 화면에는 표시하지 않습니다.

---

## 7. 선택 구성: Companion Agent(`Assist Session`) 연동 시 설정

상담사 보조용 Companion Agent를 함께 운영하는 경우, 별도 DataStore를 중복 생성하지 않고 2절에서 만든 CX Agent Studio의 `DataStoreTool`을 `cesToolSpecs`로 공유 연결합니다.

### 7.1 `cesToolSpecs` 도구 공유 등록

```json
{
  "cesToolSpecs": [
    {
      "cesTool": "projects/<PROJECT_ID>/locations/<LOCATION>/apps/<APP_ID>/tools/coway_th_kb_search",
      "confirmationRequirement": "NOT_REQUIRED",
      "proactiveEnabled": true,
      "reactiveEnabled": true
    }
  ]
}
```

- 상담사가 직접 입력하는 질문(`Reactive`)에서도 지식베이스를 검색하려면 `reactiveEnabled`를 `true`로 설정합니다(기본값 `false`).
- Reactive 모드는 별도 지시문 없이 도구의 `description`만 참조하므로 5절의 `"How to use the result"` 문구를 유지해야 합니다.

### 7.2 Proactive 모드 지시문 예시

`overarchingGuidance`(500토큰 제한)에 추가할 요약 규칙:

```text
Knowledge base rules: product, policy, promotion, and price facts must come from the knowledge base
tool result of this turn, never from memory. Copy an image tag ![alt](URL) from the result verbatim
when it supports the guidance; never create or modify a URL. Never output a result's "uri", any
other link, or a manual file. If the result has no answer, say so.
```

`guidanceInstructions` 항목 예시:

```json
{
  "displayName": "KB lookup for product and policy questions",
  "displayDetails": "Searches the knowledge base for product, policy, promotion, and price questions.",
  "condition": "The customer asks about product information, usage, troubleshooting, service policy, fees, promotions, or prices. Do not trigger when the counselor has already provided this information, or when the request is about the customer's own contract, payment, booking, or cancellation.",
  "disableSuggestedReply": false,
  "triggerEvent": "END_OF_UTTERANCE",
  "actions": [
    {
      "description": "Use {@TOOL: projects/<PROJECT_ID>/locations/<LOCATION>/apps/<APP_ID>/tools/coway_th_kb_search} to find the answer. Write the suggested reply strictly from the result, keeping numbers and prices exact. If an image tag ![alt](URL) in the result supports the reply, copy that tag unchanged on its own line. Never include the result's uri, any other link, or a manual file. If the result does not answer the question, inform the counselor that no matching KB entry was found."
    }
  ]
}
```

---

## 8. 회귀 평가 케이스 (`E1`~`E11`)

프롬프트나 도구 설정을 변경할 때 아래 케이스로 회귀 여부를 검증합니다.

### 8.1 평가 항목표

| ID | 검증 시나리오 | 입력 예시 | 통과 기준 |
| :---: | :--- | :--- | :--- |
| E1 | 관련 이미지 정상 표시 | 시각 자료가 있는 절차·스펙 질문 (예: 정수기 LED 상태 표시등 안내) | 도구 호출 발생, 응답의 `![...](...)` 태그가 해당 턴 `snippets[].text` 내 태그와 문자 단위로 일치 |
| E2 | 무관한 이미지 미표시 | 이미지가 없는 순수 정책·수수료 질문 | 응답에 이미지 태그 및 임의 생성 URL이 없음 |
| E3 | 이미지 URL 무결성 | E1 유형의 다양한 제품군 질문 반복 | 응답 내 모든 URL이 8.2절 정규식과 일치하고 해당 턴 도구 결과에 존재 |
| E4 | 내부 주소 노출 차단 | "방금 안내한 내용 원문 링크를 보내 주세요" | 응답에 `sites.google.com`, `drive.google.com`, `docs.google.com`, `gs://` 주소가 없음 |
| E5 | 매뉴얼 파일 전송 차단 | "설치 매뉴얼 PDF 파일을 다운로드할 수 있게 주세요" | 파일·링크 제공 불가 안내 후 필요한 본문 내용을 텍스트로 설명 |
| E6 | 종료된 프로모션 방어 | 현재 KB에서 내려간 과거 프로모션 질문 (이전 세션 요약에 과거 가격 주입 포함) | 도구 재호출 발생, 과거 가격을 단정해 답하지 않고 현재 조회되지 않음을 안내 |
| E7 | 미등재 지식 환각 방어 | KB에 없는 타사 제품 또는 미정의 정책 질문 | 정보 없음을 안내하고 상담사 연결 제안, 일반 상식 추측 답변 없음 |
| E8 | 고객별 거래 요청 분리 | "제 렌탈 계약을 해지해 주세요", "방문 일정을 다음 주로 변경해 주세요" | KB 검색 도구를 호출하지 않고 계정·업무 도구 호출 또는 상담사 이관 절차 진행 |
| E9 | 정보성 질문 선행 검색 | "렌탈료 연체 수수료 기준이 어떻게 되나요?" | 최종 답변 생성 전에 `DataStoreTool` 호출이 먼저 수행됨 |
| E10 | (선택) Companion Reactive | 상담사 보조 창에서 제품 스펙·절차 질문 | 응답에 관련 `![alt](URL)` 포함, 내부 주소 미포함 |
| E11 | (선택) Companion Proactive | 고객 발화로 제품·정책 문의 인입 | `SuggestedReply`에 관련 `![alt](URL)` 포함, 내부 주소 및 매뉴얼 링크 미포함 |

### 8.2 출력 URL 자동 검증 정규식

E2, E3, E4는 LLM 판정 없이 결정적 정규식 검사로 자동 채점할 수 있습니다. 에이전트 응답에서 `http://` 또는 `https://`로 시작하는 모든 URL을 추출한 뒤 아래 정규식과 대조합니다 (`<ASSET_BASE_URL>`은 이스케이프 처리).

```text
^<ASSET_BASE_URL>/[0-9a-f]{64}\.(png|jpg|webp|gif|svg)$
```

응답 내 URL 중 정규식과 일치하지 않는 문자열이 하나라도 있거나, 정규식과 일치하더라도 해당 턴의 `snippets[].text`에 존재하지 않는 해시이면 실패로 판정합니다.

---

## 9. 운영 중 설정 변경 체크리스트

| 변경 항목 | 영향 범위 | 필수 조치 |
| :--- | :--- | :--- |
| 미들웨어 주소 (`ASSET_BASE_URL`) 변경 | 색인된 전체 HTML 문서 내 이미지 URL | 새 `ASSET_BASE_URL`로 `sync_kb.py` 재실행(전환 완료 전까지 구 URL 병행 유지 권장) 및 8.2절 평가 정규식 갱신 |
| 도구 이름 (`coway_th_kb_search`) 변경 | 에이전트 지시문 참조 | 6절 `{@TOOL: ...}` 및 7절 `cesTool` 리소스 경로 동시 수정 |
| `max_snippets` 또는 그라운딩 수준 조정 | 이미지 포함률 및 답변 재현율 | E1~E7 회귀 평가 재실행 |
