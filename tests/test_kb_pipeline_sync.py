"""Tests for the simplified HTML-inline KB sync pipeline."""

from __future__ import annotations

import base64
import datetime
import json
import pathlib
from typing import Any
from urllib import parse

import pytest

from kb_pipeline.sync import html_render
from kb_pipeline.sync import sync_kb

BUCKET = "kb-bucket"
PROJECT = "test-proj"
ROOT_URL = "https://sites.google.com/coway.co.th/call-center-kb/home"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"x" * 64
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"j" * 64
GIF_BYTES = b"GIF89a" + b"g" * 16
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP"
SVG_BYTES = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
START = datetime.datetime(
    2026, 11, 17, 2, 0, 0, tzinfo=datetime.timezone.utc
)


class FakeStore:
  """In-memory ObjectStore recording writes and preserving existing blobs."""

  def __init__(self) -> None:
    self.objects: dict[str, bytes] = {}
    self.content_types: dict[str, str] = {}
    self.write_counts: dict[str, int] = {}

  def read_bytes(self, name: str) -> bytes | None:
    return self.objects.get(name)

  def write_bytes(self, name: str, data: bytes, content_type: str) -> None:
    self.objects[name] = data
    self.content_types[name] = content_type
    self.write_counts[name] = self.write_counts.get(name, 0) + 1

  def write_if_absent(
      self, name: str, data: bytes, content_type: str
  ) -> bool:
    if name in self.objects:
      return False
    self.write_bytes(name, data, content_type)
    return True

  def upload_file_if_absent(
      self, name: str, local_path: str, content_type: str
  ) -> bool:
    if name in self.objects:
      return False
    data = pathlib.Path(local_path).read_bytes()
    self.write_bytes(name, data, content_type)
    return True


class FakeImporter:
  """In-memory Discovery Engine importer."""

  def __init__(self, result: sync_kb.ImportResult | None = None) -> None:
    self.requests: list[dict[str, Any]] = []
    self.result = result or sync_kb.ImportResult(
        operation="projects/test-proj/operations/1", success_count=3
    )

  def import_documents(
      self, request: Any, timeout_seconds: float
  ) -> sync_kb.ImportResult:
    del timeout_seconds
    self.requests.append(dict(request))
    return self.result


def _make_crawl(
    root: pathlib.Path,
    *,
    extra_pages: int = 0,
    include_root: bool = True,
    include_manual: bool = True,
) -> pathlib.Path:
  src = root / "coway_th_kb"
  src.mkdir(parents=True, exist_ok=True)
  cat = "02_AS Defense"
  png_rel = f"{cat}/_assets/01_เครื่องกรองน้ำ/img_01.png"
  jpg_rel = f"{cat}/_assets/01_เครื่องกรองน้ำ/img_02.jpeg"
  gif_rel = f"{cat}/_assets/01_เครื่องกรองน้ำ/anim_01.gif"
  (src / png_rel).parent.mkdir(parents=True, exist_ok=True)
  (src / png_rel).write_bytes(PNG_BYTES)
  (src / jpg_rel).write_bytes(JPG_BYTES)
  (src / gif_rel).write_bytes(GIF_BYTES)

  pdf_rel = "_drive_files/1eGSeRK6Ux6quHRZhqEfQAMpQUZkfZO_OCT 2026 PRICE.pdf"
  drive_url = (
      "https://drive.google.com/file/d/1eGSeRK6Ux6quHRZhqEfQAMpQUZkfZO/view"
  )
  gone_url = (
      "https://drive.google.com/file/d/1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn/view"
  )
  drive_records: list[dict[str, Any]] = [
      {
          "file_id": "1r1pwlcy_gMdWY8NI5HXA3kyEapFLRkHn",
          "kind": "file",
          "status": "no_access",
          "filename": None,
          "local_file": None,
          "source_urls": [gone_url],
          "referenced_by": [f"C:/coway_th_kb/{cat}/01_เครื่องกรองน้ำ.md"],
      }
  ]
  if include_manual:
    (src / pdf_rel).parent.mkdir(parents=True, exist_ok=True)
    (src / pdf_rel).write_bytes(b"%PDF-1.4 price table")
    drive_records.insert(
        0,
        {
            "file_id": "1eGSeRK6Ux6quHRZhqEfQAMpQUZkfZO",
            "kind": "file",
            "status": "downloaded",
            "filename": "OCT 2026 PRICE.pdf",
            "local_file": pdf_rel,
            "source_urls": [drive_url],
            "referenced_by": [f"C:/coway_th_kb/{cat}/01_เครื่องกรองน้ำ.md"],
        },
    )

  manifest: list[dict[str, Any]] = []
  if include_root:
    (src / "01_home.md").write_text(
        '---\ntitle: "Home"\ncategory_path: "Home"\n---\n\n'
        "# Home\n\n"
        + "Welcome to Coway Thailand Call Center Knowledge Base. " * 3
        + "\n",
        encoding="utf-8",
    )
    manifest.append({
        "title": "Home",
        "breadcrumbs": ["Home"],
        "url": ROOT_URL,
        "file": "C:/coway_th_kb/01_home.md",
    })

  png_ref = parse.quote("_assets/01_เครื่องกรองน้ำ/img_01.png", safe="/")
  jpg_ref = parse.quote("_assets/01_เครื่องกรองน้ำ/img_02.jpeg", safe="/")
  gif_ref = parse.quote("_assets/01_เครื่องกรองน้ำ/anim_01.gif", safe="/")
  pdf_link = parse.quote(f"../{pdf_rel}", safe="/.")
  page_md = (
      '---\ntitle: "เครื่องกรองน้ำ"\n'
      'category_path: "AS > เครื่องกรองน้ำ"\n---\n\n'
      "# เครื่องกรองน้ำ\n\n"
      "## ไฟแสดงสถานะ\n\n"
      f"![ไฟ LED]({png_ref})\n\n"
      f"![รูปสินค้า]({jpg_ref})\n\n"
      f"บรรทัดแรก\n![แอนิเมชัน]({gif_ref})\nบรรทัดสอง\n\n"
      "| รุ่น | ราคา |\n| --- | --- |\n| NEON | 590 |\n\n"
      f"- [OCT 2026 PRICE.pdf]({pdf_link}) ([Drive]({drive_url}))\n"
      f"- [NOV 2026 PRICE.pdf]({gone_url}) (Drive: no_access)\n\n"
      "## Embedded Files (Drive / Docs / Sheets)\n"
      f"- [OCT]({drive_url})\n\n"
      "## Drive Files (Local Mapping)\n\n"
      "| Drive file | Status | Local file |\n| --- | --- | --- |\n"
      f"| [1eGSe]({drive_url}) | downloaded | [OCT]({pdf_link}) |\n"
  )
  page_path = src / f"{cat}/01_เครื่องกรองน้ำ.md"
  page_path.parent.mkdir(parents=True, exist_ok=True)
  page_path.write_text(page_md, encoding="utf-8")
  manifest.append({
      "title": "เครื่องกรองน้ำ",
      "breadcrumbs": ["AS", "เครื่องกรองน้ำ"],
      "url": f"{ROOT_URL}/water",
      "file": f"C:/coway_th_kb/{cat}/01_เครื่องกรองน้ำ.md",
  })

  for i in range(extra_pages):
    rel = f"extra_{i}.md"
    (src / rel).write_text(f"# Extra {i}\n\nBody text {i}\n", encoding="utf-8")
    manifest.append({
        "title": f"Extra {i}",
        "breadcrumbs": ["Extra", f"Extra {i}"],
        "url": f"{ROOT_URL}/extra/{i}",
        "file": f"C:/coway_th_kb/{rel}",
    })

  (src / "_manifest.json").write_text(
      json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
  )
  (src / "_drive_files.json").write_text(
      json.dumps({"summary": {}, "files": drive_records}, ensure_ascii=False),
      encoding="utf-8",
  )
  return src


def _run_sync(
    src: pathlib.Path,
    store: FakeStore,
    importer: FakeImporter,
    *extra_args: str,
    now: datetime.datetime = START,
) -> tuple[int, list[str]]:
  lines: list[str] = []
  args = sync_kb.parse_args([
      "--source-dir",
      str(src),
      "--bucket",
      BUCKET,
      "--project",
      PROJECT,
      "--root-page-url",
      ROOT_URL,
      *extra_args,
  ])
  code = sync_kb.sync(
      args,
      store_factory=lambda b, p: store,
      importer_factory=lambda loc: importer,
      now=lambda: now,
      out=lines.append,
  )
  return code, lines


def test_encode_data_uri_raw_png_and_jpeg_and_omits_other_formats() -> None:
  png_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
  jpg_b64 = base64.b64encode(JPG_BYTES).decode("ascii")

  assert (
      html_render.encode_data_uri(PNG_BYTES, "png")
      == f"data:image/png;base64,{png_b64}"
  )
  assert (
      html_render.encode_data_uri(JPG_BYTES, "jpg")
      == f"data:image/jpeg;base64,{jpg_b64}"
  )
  assert (
      html_render.encode_data_uri(JPG_BYTES, "jpeg")
      == f"data:image/jpeg;base64,{jpg_b64}"
  )
  assert html_render.encode_data_uri(GIF_BYTES, "gif") is None
  assert html_render.encode_data_uri(WEBP_BYTES, "webp") is None
  assert html_render.encode_data_uri(SVG_BYTES, "svg") is None


def test_html_render_embeds_uncompressed_png_jpg_no_figcaption_or_url() -> None:
  png_img = html_render.InlineImage(raw_bytes=PNG_BYTES, ext="png")
  jpg_img = html_render.InlineImage(raw_bytes=JPG_BYTES, ext="jpg")
  png_uri = (
      "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
  )
  jpg_uri = (
      "data:image/jpeg;base64," + base64.b64encode(JPG_BYTES).decode("ascii")
  )
  md = (
      "# Title\n\n"
      "## Section\n\n"
      "![LED](_assets/p/img_01.png)\n\n"
      "![Photo](_assets/p/img_02.jpg)\n\n"
      "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
      "- [Price](../_drive_files/id_price.pdf) "
      "([Drive](https://drive.google.com/file/d/id/view))\n"
      "- [Denied](https://drive.google.com/file/d/x/view) "
      "(Drive: no_access)\n\n"
      "## Drive Files (Local Mapping)\n\n"
      "| Drive file | Status | Local file |\n| --- | --- | --- |\n"
      "| [id](https://drive.google.com/file/d/id/view) | downloaded | a |\n"
  )

  rendered = html_render.render_page_html(
      md,
      page_path="dir/p.md",
      title="Title",
      category_path="Cat > Title",
      images={
          "dir/_assets/p/img_01.png": png_img,
          "dir/_assets/p/img_02.jpg": jpg_img,
      },
      manuals={"_drive_files/id_price.pdf": "price.pdf"},
  ).decode("utf-8")

  assert "<p>[Cat &gt; Title | Title]</p>" in rendered
  assert "<h1>Title</h1>" in rendered
  assert "<h2>Section</h2>" in rendered
  assert "<table><thead><tr><th>A</th><th>B</th></tr></thead>" in rendered
  assert f'<figure><img src="{png_uri}" alt="LED" /></figure>' in rendered
  assert f'<figure><img src="{jpg_uri}" alt="Photo" /></figure>' in rendered
  assert "<figcaption>" not in rendered
  assert "![LED]" not in rendered
  assert "![Photo]" not in rendered
  assert "https://" not in rendered
  assert "Price (매뉴얼 문서: price.pdf)" in rendered
  assert "drive.google.com" not in rendered
  assert "docs.google.com" not in rendered
  assert "gs://" not in rendered
  assert "Denied" not in rendered


def test_html_render_omits_gif_svg_webp_without_empty_tags_or_gaps() -> None:
  md = (
      "# Media\n\n"
      "![Only gif](_assets/p/anim.gif)\n\n"
      "Line 1\n"
      "![Middle gif](_assets/p/anim.gif)\n"
      "![Middle svg](_assets/p/icon.svg)\n"
      "Line 2\n\n"
      "- ![Bullet gif](_assets/p/anim.gif)\n"
      "- Keep bullet\n\n"
      "1. ![Numbered webp](_assets/p/photo.webp)\n"
      "2. Keep numbered\n\n"
      "- ![Only bullet svg](_assets/p/icon.svg)\n\n"
      "![External](https://example.com/pic.png)\n"
  )
  images = {
      "_assets/p/anim.gif": html_render.InlineImage(
          raw_bytes=GIF_BYTES, ext="gif"
      ),
      "_assets/p/icon.svg": html_render.InlineImage(
          raw_bytes=SVG_BYTES, ext="svg"
      ),
      "_assets/p/photo.webp": html_render.InlineImage(
          raw_bytes=WEBP_BYTES, ext="webp"
      ),
  }

  rendered = html_render.render_page_html(
      md,
      page_path="p.md",
      title="Media",
      category_path="Media",
      images=images,
      manuals={},
  ).decode("utf-8")

  assert "<figure>" not in rendered
  assert "<figcaption>" not in rendered
  assert "data:image/" not in rendered
  assert "<p></p>" not in rendered
  assert "<li></li>" not in rendered
  assert "<ul></ul>" not in rendered
  assert "<ol></ol>" not in rendered
  assert "<br /><br />" not in rendered
  assert "https://example.com" not in rendered
  assert "<p>Line 1<br />Line 2</p>" in rendered
  assert "<ul><li>Keep bullet</li></ul>" in rendered
  assert "<ol><li>Keep numbered</li></ol>" in rendered


def test_html_render_falls_back_when_over_size_cap() -> None:
  img = html_render.InlineImage(raw_bytes=PNG_BYTES, ext="png")
  rendered = html_render.render_page_html(
      "Keep this text\n\n![Big](_assets/p/big.png)\n",
      page_path="p.md",
      title="Big",
      category_path="Big",
      images={"_assets/p/big.png": img},
      manuals={},
      encoder=lambda raw, ext: "data:image/png;base64," + ("A" * 5000),
      max_html_bytes=1000,
  ).decode("utf-8")

  assert "data:image/" not in rendered
  assert "<figure>" not in rendered
  assert "<figcaption>" not in rendered
  assert "<p></p>" not in rendered
  assert "<p>Keep this text</p>" in rendered
  assert "<p>[Big | Big]</p>" in rendered
  assert len(rendered.encode("utf-8")) <= 1000


def test_parse_args_rejects_removed_asset_base_url_flag() -> None:
  with pytest.raises(SystemExit) as exc_info:
    sync_kb.parse_args([
        "--source-dir",
        "/tmp/src",
        "--bucket",
        BUCKET,
        "--project",
        PROJECT,
        "--asset-base-url",
        "https://mw.example.com/api/kb-assets",
    ])
  assert exc_info.value.code == sync_kb.EXIT_BAD_INPUT


def test_sync_end_to_end_and_idempotent_second_run(
    tmp_path: pathlib.Path,
) -> None:
  src = _make_crawl(tmp_path, extra_pages=2)
  store = FakeStore()
  importer = FakeImporter()

  code, lines = _run_sync(src, store, importer)

  assert code == sync_kb.EXIT_SUCCEEDED
  assert lines[-1] == "[성공] 페이지 4건 / 매뉴얼 1건 반영"
  assert len(importer.requests) == 1
  req = importer.requests[0]
  assert req["reconciliation_mode"] == "FULL"
  assert req["gcs_source"]["data_schema"] == "document"

  assert not any(key.startswith("assets/") for key in store.objects)
  assert "snapshots/2026-11-17_020000/raw/_manifest.json" in store.objects
  assert (
      "snapshots/2026-11-17_020000/raw/"
      "02_AS Defense/_assets/01_เครื่องกรองน้ำ/anim_01.gif"
      in store.objects
  )

  last_success = json.loads(
      store.objects[sync_kb.LAST_SUCCESS_OBJECT].decode("utf-8")
  )
  assert last_success["snapshot_id"] == "2026-11-17_020000"
  assert last_success["pages"] == 4
  assert last_success["manuals"] == 1

  jsonl1 = store.objects["manifests/2026-11-17_020000/documents.jsonl"]
  docs = [json.loads(line) for line in jsonl1.decode("utf-8").splitlines()]
  assert len(docs) == 5
  page_doc = next(
      d for d in docs if d["structData"]["title"] == "เครื่องกรองน้ำ"
  )
  assert page_doc["content"]["mimeType"] == "text/html"
  html_blob = page_doc["content"]["uri"].split("/", 3)[3]
  assert html_blob.startswith("processed/blobs/")
  page_html = store.objects[html_blob].decode("utf-8")
  png_uri = (
      "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
  )
  jpg_uri = (
      "data:image/jpeg;base64," + base64.b64encode(JPG_BYTES).decode("ascii")
  )
  assert f'<figure><img src="{png_uri}" alt="ไฟ LED" /></figure>' in page_html
  assert (
      f'<figure><img src="{jpg_uri}" alt="รูปสินค้า" /></figure>' in page_html
  )
  assert "<p>บรรทัดแรก<br />บรรทัดสอง</p>" in page_html
  assert "<figcaption>" not in page_html
  assert "data:image/gif" not in page_html
  assert "<br /><br />" not in page_html
  assert "<p></p>" not in page_html
  assert "drive.google.com" not in page_html

  manual_doc = next(d for d in docs if d["structData"]["doc_type"] == "manual")
  assert manual_doc["id"] == "drive-1eGSeRK6Ux6quHRZhqEfQAMpQUZkfZO"
  assert manual_doc["content"]["mimeType"] == "application/pdf"
  assert manual_doc["structData"]["uri"] == f"{ROOT_URL}/water"

  # Second run with identical crawl produces byte-identical manifest and
  # never rewrites content-addressed blobs or uploads assets/.
  t2 = START + datetime.timedelta(minutes=5)
  code2, _ = _run_sync(src, store, importer, now=t2)
  assert code2 == sync_kb.EXIT_SUCCEEDED
  jsonl2 = store.objects["manifests/2026-11-17_020500/documents.jsonl"]
  assert jsonl2 == jsonl1
  assert store.write_counts[html_blob] == 1
  assert not any(key.startswith("assets/") for key in store.objects)


def test_circuit_breaker_blocks_page_and_manual_drops_and_allows_force(
    tmp_path: pathlib.Path,
) -> None:
  store = FakeStore()
  store.write_bytes(
      sync_kb.LAST_SUCCESS_OBJECT,
      json.dumps({"snapshot_id": "prev", "pages": 10, "manuals": 5}).encode(),
      "application/json",
  )
  importer = FakeImporter()

  small_src = _make_crawl(
      tmp_path / "small", extra_pages=2, include_manual=False
  )
  code, lines = _run_sync(small_src, store, importer)
  assert code == sync_kb.EXIT_BLOCKED
  assert "[차단됨]" in lines[-1]
  assert "매뉴얼 파일이" in lines[-1]
  assert not importer.requests

  code_force, lines_force = _run_sync(
      small_src, store, importer, "--force-sync"
  )
  assert code_force == sync_kb.EXIT_SUCCEEDED
  assert lines_force[-1] == "[성공] 페이지 4건 / 매뉴얼 0건 반영"


def test_import_failure_preserves_last_success(tmp_path: pathlib.Path) -> None:
  src = _make_crawl(tmp_path)
  store = FakeStore()
  failing_importer = FakeImporter(
      sync_kb.ImportResult(
          operation="op/fail", failure_count=1, error="bad doc"
      )
  )

  code, lines = _run_sync(src, store, failing_importer)

  assert code == sync_kb.EXIT_FAILED
  assert "[실패]" in lines[-1]
  assert sync_kb.LAST_SUCCESS_OBJECT not in store.objects


def test_normalize_url_matches_decoded_and_encoded_thai_paths(
    tmp_path: pathlib.Path,
) -> None:
  thai_encoded = (
      "https://sites.google.com/coway.co.th/call-center-kb/"
      "%E0%B8%AB%E0%B8%99%E0%B8%B2%E0%B9%81%E0%B8%A3%E0%B8%81"
  )
  thai_decoded = "https://sites.google.com/coway.co.th/call-center-kb/หนาแรก/"
  assert sync_kb._normalize_url(thai_encoded) == sync_kb._normalize_url(
      thai_decoded
  )
  del tmp_path
