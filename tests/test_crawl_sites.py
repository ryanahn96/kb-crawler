"""Tests for pure URL, filename, and Drive link helpers in crawl_sites."""

from __future__ import annotations

from kb_crawler import crawl_sites


def test_normalize_url_and_derive_site_prefix() -> None:
  raw = (
      "https://sites.google.com/coway.co.th/call-center-kb/"
      "หนาแรก/?pli=1#section"
  )
  norm = crawl_sites.normalize_url(raw)
  assert norm == (
      "https://sites.google.com/coway.co.th/call-center-kb/"
      "%E0%B8%AB%E0%B8%99%E0%B8%B2%E0%B9%81%E0%B8%A3%E0%B8%81"
  )
  assert (
      crawl_sites.derive_site_prefix(raw)
      == "https://sites.google.com/coway.co.th/call-center-kb"
  )


def test_sanitize_name_handles_windows_reserved_and_illegal_chars() -> None:
  assert crawl_sites.sanitize_name('AS / Product: "Manual"?') == (
      "AS _ Product_ _Manual_"
  )
  assert crawl_sites.sanitize_name("CON") == "_CON"
  assert crawl_sites.sanitize_name("...") == "untitled"


def test_drive_file_ref_parses_files_docs_and_folders() -> None:
  file_id = "1eGSeRK6Ux6quHRZhqEfQAMpQUZkfZO"
  assert crawl_sites.drive_file_ref(
      f"https://drive.google.com/file/d/{file_id}/preview?authuser=0"
  ) == ("file", file_id)
  assert crawl_sites.drive_file_ref(
      f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
  ) == ("spreadsheets", file_id)
  assert crawl_sites.drive_file_ref(
      f"https://drive.google.com/drive/folders/{file_id}"
  ) == ("folder", file_id)
