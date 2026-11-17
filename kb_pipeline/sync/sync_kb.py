"""Syncs a local crawl output folder directly into GCS and Vertex AI Search.

Runs on the operator PC (step 3 after crawling). No Cloud Run Job, Eventarc,
Workflows, or external VLM calls are needed:
  1. Checks crawl folder and circuit breaker against state/last_success.json.
  2. Archives raw crawl files to gs://<bucket>/snapshots/<snapshot_id>/raw/.
  3. Uploads original images to gs://<bucket>/assets/<sha256>.<ext> (if absent).
  4. Renders each Knowledge Page to HTML with inline Base64 images and
     <figcaption> middleware URLs, and uploads content-addressed blobs.
  5. Copies accessible Drive manuals (pdf/docx/pptx/xlsx/xlsm <= 200 MB) to
     gs://<bucket>/processed/blobs/<sha256>.<ext>.
  6. Writes manifests/<snapshot_id>/documents.jsonl and runs
     ImportDocuments(data_schema="document", reconciliation_mode=FULL).
  7. Updates state/last_success.json on zero-failure success.

Exit codes:
  0: SUCCEEDED
  1: FAILED (import or runtime error)
  2: BLOCKED (circuit breaker; DataStore untouched)
  4: Invalid local folder or CLI arguments (nothing changed)
  5: Missing or expired Google credentials (nothing changed)
  130: Interrupted by user
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import dataclasses
import datetime
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import sys
import unicodedata
from typing import Any, NoReturn, Protocol
from urllib import parse
import warnings

from kb_pipeline.sync import html_render

_UTC = datetime.timezone.utc  # noqa: UP017

EXIT_SUCCEEDED = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_BAD_INPUT = 4
EXIT_CREDENTIALS = 5
EXIT_INTERRUPTED = 130

MIN_ROOT_BODY_CHARS = 50
MAX_MANUAL_BYTES = 200 * 1024 * 1024
LAST_SUCCESS_OBJECT = "state/last_success.json"
DEFAULT_IMPORT_TIMEOUT_SECONDS = 3600.0
DEFAULT_MIN_PAGE_RATIO = 0.8
DEFAULT_ROOT_PAGE_URL = (
    "https://sites.google.com/coway.co.th/call-center-kb/"
    "%E0%B8%AB%E0%B8%99%E0%B8%B2%E0%B9%81%E0%B8%A3%E0%B8%81"
)

IMAGE_MIME: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "svg": "image/svg+xml",
}
MANUAL_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
}
_DOC_ID_SAFE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,56}$")


class SyncError(Exception):
  """Expected operational failure with a Korean operator-facing message."""


class CredentialsError(SyncError):
  """Google Application Default Credentials are missing or expired."""


@dataclasses.dataclass(frozen=True)
class ImportResult:
  """Outcome of one Discovery Engine ImportDocuments operation."""

  operation: str
  success_count: int = 0
  failure_count: int = 0
  error: str = ""


class ObjectStore(Protocol):
  """Minimal GCS interface so unit tests run with an in-memory fake."""

  def read_bytes(self, name: str) -> bytes | None:
    """Returns object bytes, or None if missing."""

  def write_bytes(self, name: str, data: bytes, content_type: str) -> None:
    """Creates or overwrites an object."""

  def write_if_absent(
      self, name: str, data: bytes, content_type: str
  ) -> bool:
    """Creates an object only when missing; returns True if created."""

  def upload_file_if_absent(
      self, name: str, local_path: str, content_type: str
  ) -> bool:
    """Uploads a local file only when missing; returns True if created."""


class DocumentImporter(Protocol):
  """Minimal Vertex AI Search import interface."""

  def import_documents(
      self, request: Mapping[str, Any], timeout_seconds: float
  ) -> ImportResult:
    """Starts ImportDocuments and waits until it finishes."""


class GcsObjectStore:
  """ObjectStore backed by google-cloud-storage (lazy import)."""

  def __init__(self, bucket_name: str, project: str | None = None) -> None:
    try:
      # pylint: disable=g-import-not-at-top
      from google.api_core import exceptions as api_exceptions
      from google.auth import exceptions as auth_exceptions
      from google.cloud import storage
      # pylint: enable=g-import-not-at-top
    except ImportError as exc:
      raise SyncError(
          "google-cloud-storage 패키지가 설치되어 있지 않습니다. "
          "'pip install -r requirements.txt' 를 먼저 실행해 주세요."
      ) from exc
    self._api_exc = api_exceptions
    self._auth_exc = auth_exceptions
    warnings.filterwarnings(
        "ignore", message=".*end user credentials.*", category=UserWarning
    )
    warnings.filterwarnings(
        "ignore", message=".*quota project.*", category=UserWarning
    )
    try:
      client = storage.Client(project=project)
    except auth_exceptions.DefaultCredentialsError as exc:
      raise CredentialsError(
          "Google 로그인 정보(ADC)가 없습니다. "
          "'gcloud auth application-default login' 을 실행해 주세요."
      ) from exc
    self._bucket = client.bucket(bucket_name)

  def _handle(self, exc: Exception, name: str) -> NoReturn:
    if isinstance(
        exc,
        (
            self._api_exc.Unauthorized,
            self._auth_exc.RefreshError,
            self._auth_exc.DefaultCredentialsError,
        ),
    ):
      raise CredentialsError(
          "Google 로그인이 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요."
      ) from exc
    raise SyncError(f"GCS 처리 오류 ({name}): {exc}") from exc

  def read_bytes(self, name: str) -> bytes | None:
    try:
      return self._bucket.blob(name).download_as_bytes()
    except self._api_exc.NotFound:
      return None
    except Exception as exc:  # pylint: disable=broad-exception-caught
      self._handle(exc, name)

  def write_bytes(self, name: str, data: bytes, content_type: str) -> None:
    try:
      self._bucket.blob(name).upload_from_string(
          data, content_type=content_type
      )
    except Exception as exc:  # pylint: disable=broad-exception-caught
      self._handle(exc, name)

  def write_if_absent(
      self, name: str, data: bytes, content_type: str
  ) -> bool:
    try:
      self._bucket.blob(name).upload_from_string(
          data, content_type=content_type, if_generation_match=0
      )
      return True
    except self._api_exc.PreconditionFailed:
      return False
    except Exception as exc:  # pylint: disable=broad-exception-caught
      self._handle(exc, name)

  def upload_file_if_absent(
      self, name: str, local_path: str, content_type: str
  ) -> bool:
    try:
      self._bucket.blob(name).upload_from_filename(
          local_path, content_type=content_type, if_generation_match=0
      )
      return True
    except self._api_exc.PreconditionFailed:
      return False
    except Exception as exc:  # pylint: disable=broad-exception-caught
      self._handle(exc, name)


class DiscoveryEngineImporter:
  """DocumentImporter backed by google-cloud-discoveryengine (lazy import)."""

  def __init__(self, location: str) -> None:
    try:
      # pylint: disable=g-import-not-at-top
      from google.api_core import client_options as api_client_options
      from google.api_core import exceptions as api_exceptions
      from google.auth import exceptions as auth_exceptions
      from google.cloud import discoveryengine_v1 as discoveryengine
      # pylint: enable=g-import-not-at-top
    except ImportError as exc:
      raise SyncError(
          "google-cloud-discoveryengine 패키지가 설치되어 있지 않습니다."
      ) from exc
    self._api_exc = api_exceptions
    self._auth_exc = auth_exceptions
    self._discoveryengine = discoveryengine
    opts = None
    if location != "global":
      opts = api_client_options.ClientOptions(
          api_endpoint=f"{location}-discoveryengine.googleapis.com"
      )
    try:
      self._client = discoveryengine.DocumentServiceClient(client_options=opts)
    except auth_exceptions.DefaultCredentialsError as exc:
      raise CredentialsError(
          "Google 로그인 정보(ADC)가 없습니다. 다시 로그인해 주세요."
      ) from exc

  def import_documents(
      self, request: Mapping[str, Any], timeout_seconds: float
  ) -> ImportResult:
    req = self._discoveryengine.ImportDocumentsRequest(request)
    try:
      operation = self._client.import_documents(request=req)
      op_name = operation.operation.name
      response = operation.result(timeout=timeout_seconds)
    except (
        self._api_exc.Unauthorized,
        self._auth_exc.RefreshError,
        self._auth_exc.DefaultCredentialsError,
    ) as exc:
      raise CredentialsError(
          "Google 로그인이 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요."
      ) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
      return ImportResult(operation="", error=str(exc))

    meta = operation.metadata
    success = int(getattr(meta, "success_count", 0) or 0)
    failure = int(getattr(meta, "failure_count", 0) or 0)
    samples = list(getattr(response, "error_samples", []) or [])
    first_err = str(samples[0].message) if samples else ""
    return ImportResult(
        operation=op_name,
        success_count=success,
        failure_count=failure,
        error=first_err,
    )


def _normalize_url(url: str) -> str:
  clean, _ = parse.urldefrag(url.strip())
  parts = parse.urlsplit(clean)
  norm_path = parse.quote(parse.unquote(parts.path), safe="/").rstrip("/")
  return parse.urlunsplit((
      parts.scheme.lower(),
      parts.netloc.lower(),
      norm_path,
      "",
      "",
  ))


def _extension(path: str) -> str:
  ext = posixpath.splitext(path)[1].lower().lstrip(".")
  return "jpg" if ext == "jpeg" else ext


def _sha256_file(local_path: str) -> str:
  digest = hashlib.sha256()
  with open(local_path, "rb") as f:
    while chunk := f.read(1024 * 1024):
      digest.update(chunk)
  return digest.hexdigest()


def _page_doc_id(url: str) -> str:
  return "page-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _content_type(rel_path: str) -> str:
  lowered = rel_path.lower()
  if lowered.endswith(".md"):
    return "text/markdown; charset=utf-8"
  if lowered.endswith(".html"):
    return "text/html; charset=utf-8"
  if lowered.endswith(".json"):
    return "application/json"
  guessed, _ = mimetypes.guess_type(rel_path)
  return guessed or "application/octet-stream"


@dataclasses.dataclass(frozen=True)
class _LocalFile:
  rel_path: str
  abs_path: str
  size: int


class _LocalIndex:
  """Indexes every file in the crawl output folder by normalised suffix."""

  def __init__(self, source_dir: str) -> None:
    self.files: list[_LocalFile] = []
    self._by_nfc: dict[str, _LocalFile] = {}
    for root, _, names in os.walk(source_dir):
      for name in sorted(names):
        abs_path = os.path.join(root, name)
        rel = os.path.relpath(abs_path, source_dir).replace(os.sep, "/")
        entry = _LocalFile(
            rel_path=rel, abs_path=abs_path, size=os.path.getsize(abs_path)
        )
        self.files.append(entry)
        self._by_nfc[unicodedata.normalize("NFC", rel)] = entry
    self.files.sort(key=lambda item: item.rel_path)

  def get(self, rel_path: str) -> _LocalFile | None:
    return self._by_nfc.get(unicodedata.normalize("NFC", rel_path))

  def resolve(self, file_ref: str) -> _LocalFile | None:
    clean = unicodedata.normalize(
        "NFC", file_ref.replace("\\", "/").strip("/")
    )
    parts = [p for p in clean.split("/") if p]
    for idx in range(len(parts)):
      candidate = "/".join(parts[idx:])
      if candidate in self._by_nfc:
        return self._by_nfc[candidate]
    return None


@dataclasses.dataclass(frozen=True)
class _Page:
  url: str
  title: str
  category_path: str
  rel_path: str
  body: str


def _root_body_length(body: str) -> int:
  cleaned = html_render.strip_trailing_sections(body)
  lines = [
      line for line in cleaned.split("\n")
      if not line.lstrip().startswith("# ")
  ]
  return len("".join(line.strip() for line in lines))


def _check_circuit_breaker(
    *,
    pages: Sequence[_Page],
    manuals_count: int,
    previous: Mapping[str, Any] | None,
    root_page_url: str,
    min_page_ratio: float,
    force_sync: bool,
) -> str:
  """Returns a Korean blocking reason, or an empty string if safe to import."""
  if not pages:
    return (
        "수집된 페이지가 0건이어서 동기화를 차단했습니다. "
        "크롤링 결과 폴더를 확인하세요."
    )
  problems: list[str] = []
  norm_root = _normalize_url(root_page_url)
  root = next((p for p in pages if _normalize_url(p.url) == norm_root), None)
  if root is None:
    problems.append("홈 페이지가 수집 결과에 없습니다.")
  else:
    length = _root_body_length(root.body)
    if length < MIN_ROOT_BODY_CHARS:
      problems.append(
          f"홈 페이지 본문이 {length}자로 {MIN_ROOT_BODY_CHARS}자 미만입니다."
      )
  if previous:
    prev_pages = int(previous.get("pages") or 0)
    prev_manuals = int(previous.get("manuals") or 0)
    if prev_pages and len(pages) < min_page_ratio * prev_pages:
      problems.append(
          f"페이지 수가 직전 성공 {prev_pages}건에서 {len(pages)}건으로 "
          f"줄었습니다 (기준 {min_page_ratio:.0%})."
      )
    if prev_manuals > 0 and manuals_count == 0:
      problems.append(
          f"매뉴얼 파일이 직전 성공 {prev_manuals}건에서 0건으로 줄었습니다 "
          "(Drive 권한 만료 가능성)."
      )
  if not problems or force_sync:
    return ""
  return (
      "동기화를 차단했습니다. "
      + " ".join(problems)
      + " 수집 결과가 정상이라면 --force-sync 옵션으로 다시 실행하세요."
  )


def _load_pages(
    manifest: list[Any], index: _LocalIndex
) -> list[_Page]:
  pages: list[_Page] = []
  seen_urls: set[str] = set()
  for entry in manifest:
    if not isinstance(entry, dict):
      continue
    url = str(entry.get("url") or "").strip()
    local = index.resolve(str(entry.get("file") or ""))
    if not url or local is None or url in seen_urls:
      continue
    seen_urls.add(url)
    with open(local.abs_path, encoding="utf-8", errors="replace") as f:
      parsed = html_render.parse_page(f.read())
    crumbs = entry.get("breadcrumbs")
    if isinstance(crumbs, list) and crumbs:
      category_path = " > ".join(str(c) for c in crumbs)
    else:
      category_path = parsed.meta.get("category_path", "")
    pages.append(
        _Page(
            url=url,
            title=str(entry.get("title") or parsed.meta.get("title", "")),
            category_path=category_path,
            rel_path=local.rel_path,
            body=parsed.body,
        )
    )
  return pages


def _select_manuals(
    drive_records: list[Any],
    pages: Sequence[_Page],
    index: _LocalIndex,
) -> tuple[list[tuple[str, str, _LocalFile, _Page]], int]:
  """Selects importable Drive manuals and counts skipped downloaded files."""
  pages_by_rel = {
      unicodedata.normalize("NFC", p.rel_path): p for p in pages
  }
  selected: list[tuple[str, str, _LocalFile, _Page]] = []
  seen_ids: set[str] = set()
  skipped = 0
  downloaded = [
      rec for rec in drive_records
      if isinstance(rec, dict) and rec.get("status") == "downloaded"
  ]
  for rec in sorted(downloaded, key=lambda r: str(r.get("file_id") or "")):
    file_id = str(rec.get("file_id") or "")
    local_ref = str(rec.get("local_file") or "").replace("\\", "/")
    local_file = index.get(local_ref)
    ext = _extension(local_ref)
    owner = next(
        (
            pages_by_rel[unicodedata.normalize("NFC", resolved.rel_path)]
            for ref in rec.get("referenced_by") or []
            if (resolved := index.resolve(str(ref))) is not None
            and unicodedata.normalize("NFC", resolved.rel_path) in pages_by_rel
        ),
        None,
    )
    if (
        ext not in MANUAL_MIME
        or local_file is None
        or local_file.size > MAX_MANUAL_BYTES
        or owner is None
        or file_id in seen_ids
        or not _DOC_ID_SAFE_RE.match(file_id)
    ):
      skipped += 1
      continue
    seen_ids.add(file_id)
    filename = str(
        rec.get("filename")
        or posixpath.basename(local_ref).removeprefix(f"{file_id}_")
    )
    selected.append((file_id, filename, local_file, owner))
  return selected, skipped


def sync(
    args: argparse.Namespace,
    *,
    store_factory: Callable[[str, str | None], ObjectStore] = GcsObjectStore,
    importer_factory: Callable[[str], DocumentImporter] = (
        DiscoveryEngineImporter
    ),
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(_UTC),
    out: Callable[[str], None] = lambda line: print(line, flush=True),
) -> int:
  """Runs the full local-to-DataStore HTML inline sync."""
  source_dir = args.source_dir
  if not os.path.isdir(source_dir):
    out(f"[오류] 크롤링 결과 폴더를 찾을 수 없습니다: {source_dir}")
    return EXIT_BAD_INPUT

  manifest_path = os.path.join(source_dir, "_manifest.json")
  drive_path = os.path.join(source_dir, "_drive_files.json")
  for req_path in (manifest_path, drive_path):
    if not os.path.isfile(req_path):
      out(f"[오류] 필수 파일이 없습니다: {os.path.basename(req_path)}")
      return EXIT_BAD_INPUT

  try:
    with open(manifest_path, encoding="utf-8-sig") as f:
      manifest = json.load(f)
    with open(drive_path, encoding="utf-8-sig") as f:
      drive_report = json.load(f)
  except (OSError, ValueError) as exc:
    out(f"[오류] JSON 파일을 읽을 수 없습니다: {exc}")
    return EXIT_BAD_INPUT

  if not isinstance(manifest, list) or not isinstance(drive_report, dict):
    out("[오류] _manifest.json 또는 _drive_files.json 형식이 올바르지 않습니다.")
    return EXIT_BAD_INPUT

  index = _LocalIndex(source_dir)
  pages = _load_pages(manifest, index)
  selected_manuals, manuals_skipped = _select_manuals(
      drive_report.get("files") or [], pages, index
  )

  try:
    store = store_factory(args.bucket, args.project)
    raw_prev = store.read_bytes(LAST_SUCCESS_OBJECT)
    previous = json.loads(raw_prev.decode("utf-8")) if raw_prev else None
  except CredentialsError as exc:
    out(f"[로그인 필요] {exc}")
    return EXIT_CREDENTIALS
  except (SyncError, ValueError) as exc:
    out(f"[실패] 직전 성공 기록을 읽을 수 없습니다: {exc}")
    return EXIT_FAILED

  blocked = _check_circuit_breaker(
      pages=pages,
      manuals_count=len(selected_manuals),
      previous=previous,
      root_page_url=args.root_page_url,
      min_page_ratio=args.min_page_ratio,
      force_sync=args.force_sync,
  )
  if blocked:
    out(f"[차단됨] {blocked}")
    return EXIT_BLOCKED

  snapshot_id = now().astimezone(_UTC).strftime("%Y-%m-%d_%H%M%S")
  out(f"[1/4] 원본 스냅샷 아카이브 업로드 중... (스냅샷 ID: {snapshot_id})")
  try:
    for item in index.files:
      store.upload_file_if_absent(
          f"snapshots/{snapshot_id}/raw/{item.rel_path}",
          item.abs_path,
          _content_type(item.rel_path),
      )

    out("[2/4] 이미지 자산 업로드 및 HTML 페이지 변환 중...")
    asset_base = args.asset_base_url.rstrip("/")
    inline_images: dict[str, html_render.InlineImage] = {}
    for page in pages:
      for img_rel in html_render.local_image_paths(page.body, page.rel_path):
        if img_rel in inline_images:
          continue
        img_file = index.get(img_rel)
        ext = _extension(img_rel)
        if img_file is None or ext not in IMAGE_MIME:
          continue
        with open(img_file.abs_path, "rb") as f:
          raw_bytes = f.read()
        sha = hashlib.sha256(raw_bytes).hexdigest()
        store.write_if_absent(
            f"assets/{sha}.{ext}", raw_bytes, IMAGE_MIME[ext]
        )
        inline_images[img_rel] = html_render.InlineImage(
            asset_url=f"{asset_base}/{sha}.{ext}",
            raw_bytes=raw_bytes,
            ext=ext,
        )

    manual_names: dict[str, str] = {}
    manual_docs: list[dict[str, Any]] = []
    for file_id, filename, local_file, owner in selected_manuals:
      ext = _extension(local_file.rel_path)
      sha = _sha256_file(local_file.abs_path)
      blob = f"processed/blobs/{sha}.{ext}"
      store.upload_file_if_absent(blob, local_file.abs_path, MANUAL_MIME[ext])
      manual_names[unicodedata.normalize("NFC", local_file.rel_path)] = (
          filename
      )
      manual_docs.append({
          "id": f"drive-{file_id}",
          "structData": {
              "title": filename,
              "uri": owner.url,
              "doc_type": "manual",
              "category_path": owner.category_path,
          },
          "content": {
              "mimeType": MANUAL_MIME[ext],
              "uri": f"gs://{args.bucket}/{blob}",
          },
      })

    page_docs: list[dict[str, Any]] = []
    for page in pages:
      html_bytes = html_render.render_page_html(
          page.body,
          page_path=page.rel_path,
          title=page.title,
          category_path=page.category_path,
          images=inline_images,
          manuals=manual_names,
      )
      sha = hashlib.sha256(html_bytes).hexdigest()
      blob = f"processed/blobs/{sha}.html"
      store.write_if_absent(blob, html_bytes, "text/html; charset=utf-8")
      page_docs.append({
          "id": _page_doc_id(page.url),
          "structData": {
              "title": page.title,
              "uri": page.url,
              "doc_type": "page",
              "category_path": page.category_path,
          },
          "content": {
              "mimeType": "text/html",
              "uri": f"gs://{args.bucket}/{blob}",
          },
      })

    manifest_obj = f"manifests/{snapshot_id}/documents.jsonl"
    jsonl_lines = [
        json.dumps(doc, ensure_ascii=False) for doc in page_docs + manual_docs
    ]
    store.write_bytes(
        manifest_obj,
        ("\n".join(jsonl_lines) + "\n").encode("utf-8"),
        "application/x-ndjson",
    )

    out(
        f"[3/4] Vertex AI Search FULL 동기화 요청 중... "
        f"(페이지 {len(page_docs)}건, 매뉴얼 {len(manual_docs)}건)"
    )
    parent = (
        f"projects/{args.project}/locations/{args.datastore_location}"
        f"/collections/default_collection/dataStores/{args.datastore_id}"
        "/branches/default_branch"
    )
    request = {
        "parent": parent,
        "gcs_source": {
            "input_uris": [f"gs://{args.bucket}/{manifest_obj}"],
            "data_schema": "document",
        },
        "reconciliation_mode": "FULL",
    }
    importer = importer_factory(args.datastore_location)
    result = importer.import_documents(request, args.import_timeout_seconds)
    if result.error or result.failure_count > 0:
      out(
          f"[실패] DataStore 가져오기에 실패했습니다 "
          f"(실패 {result.failure_count}건"
          + (f", {result.error}" if result.error else "")
          + "). 직전 성공 기록은 유지됩니다."
      )
      return EXIT_FAILED

    finished_at = now().astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.write_bytes(
        LAST_SUCCESS_OBJECT,
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "pages": len(page_docs),
                "manuals": len(manual_docs),
                "finished_at": finished_at,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        "application/json",
    )
  except CredentialsError as exc:
    out(f"[로그인 필요] {exc}")
    return EXIT_CREDENTIALS
  except SyncError as exc:
    out(f"[실패] {exc}")
    return EXIT_FAILED

  out(f"[4/4] 완료 (스냅샷 ID: {snapshot_id}, 이미지 {len(inline_images)}개"
      + (f", 제외 매뉴얼 {manuals_skipped}건" if manuals_skipped else "")
      + ")")
  out(f"[성공] 페이지 {len(page_docs)}건 / 매뉴얼 {len(manual_docs)}건 반영")
  return EXIT_SUCCEEDED


class _KoreanArgumentParser(argparse.ArgumentParser):
  """Never exits with 2, which is reserved for BLOCKED."""

  def error(self, message: str) -> NoReturn:
    self.print_usage(sys.stderr)
    print(f"[오류] 실행 옵션이 올바르지 않습니다: {message}", file=sys.stderr)
    sys.exit(EXIT_BAD_INPUT)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
  """Parses CLI arguments."""
  parser = _KoreanArgumentParser(
      description="크롤링 폴더를 HTML 인라인 변환 후 DataStore에 직접 동기화합니다."
  )
  parser.add_argument("--source-dir", required=True, help="크롤링 결과 폴더")
  parser.add_argument("--bucket", required=True, help="KB 버킷 이름")
  parser.add_argument("--project", required=True, help="GCP 프로젝트 ID")
  parser.add_argument(
      "--datastore-location",
      default="us",
      choices=("us", "eu", "global"),
      help="DataStore 리전 (기본 us)",
  )
  parser.add_argument("--datastore-id", default="coway-kb", help="DataStore ID")
  parser.add_argument(
      "--asset-base-url",
      required=True,
      help="미들웨어 이미지 기본 URL (예: https://mw.example.com/api/kb-assets)",
  )
  parser.add_argument(
      "--root-page-url",
      default=DEFAULT_ROOT_PAGE_URL,
      help="지식 사이트 홈 페이지 원본 URL (기본: 태국 콜센터 KB 홈)",
  )
  parser.add_argument(
      "--min-page-ratio",
      type=float,
      default=DEFAULT_MIN_PAGE_RATIO,
      help="차단기 최소 페이지 비율 (기본 0.8)",
  )
  parser.add_argument(
      "--force-sync",
      action="store_true",
      help="안전 점검 차단을 건너뛰고 강제 반영",
  )
  parser.add_argument(
      "--import-timeout-seconds",
      type=float,
      default=DEFAULT_IMPORT_TIMEOUT_SECONDS,
      help="ImportDocuments 완료 대기 제한 시간(초)",
  )
  args = parser.parse_args(argv)
  args.bucket = args.bucket.removeprefix("gs://").strip().strip("/")
  if not args.bucket:
    parser.error("--bucket 값이 비어 있습니다")
  return args


def main(argv: Sequence[str] | None = None) -> int:
  """CLI entrypoint."""
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8", errors="replace")
  args = parse_args(argv)
  try:
    return sync(args)
  except KeyboardInterrupt:
    print("\n[중단] 사용자가 실행을 중단했습니다.", flush=True)
    return EXIT_INTERRUPTED


if __name__ == "__main__":
  sys.exit(main())
