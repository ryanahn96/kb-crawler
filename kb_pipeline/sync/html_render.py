"""Renders crawled Knowledge Page Markdown into HTML with inline Base64 images.

Vertex AI Search Layout Parser supports HTML with `enable_image_annotation`
and `enable_table_annotation`, and chunks HTML by headings. Because the HTML
parser only decodes inline `data:image/(png|jpeg);base64,...` data URIs and no
image URLs are exposed to customers, each local PNG/JPEG image is embedded
uncompressed as:

  <figure><img src="data:image/(png|jpeg);base64,..." alt="..." /></figure>

No `<figcaption>` or image URL is emitted anywhere in the HTML. Unsupported
image formats (such as `.gif`, `.webp`, and `.svg`) are omitted cleanly while
leaving surrounding page text intact.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import dataclasses
import html
import posixpath
import re
import unicodedata
from urllib import parse

MANUAL_LABEL = "매뉴얼 문서"
MAX_HTML_BYTES = 9_500_000  # Safety margin under Discovery Engine's 10 MB cap.

_URL = r"(?:[^()\s]|\([^()\s]*\))+"
_FORBIDDEN_URL = (
    r"(?:https?://(?:drive|docs)\.google\.com|gs://)"
    r"(?:[^()\s]|\([^()\s]*\))*"
)
_STATUS_SUFFIX = r"(?: \(Drive: [a-z_]+\))?"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SCHEME_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")
_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\((" + _URL + r")\)")
_IMAGE_LINK_RE = re.compile(
    r"\[(!\[[^\]\n]*\]\(" + _URL + r"\))\]\(" + _URL + r"\)" + _STATUS_SUFFIX
)
_DOWNLOADED_RE = re.compile(
    r"\[([^\]\n]*)\]\(([^()\s]+)\) \(\[Drive\]\(" + _URL + r"\)\)"
)
_FORBIDDEN_LINK_RE = re.compile(
    r"(?:(?<=\S)[ \t])?\[[^\]\n]*\]\(" + _FORBIDDEN_URL + r"\)"
    + _STATUS_SUFFIX,
    re.I,
)
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?:https?://)?(?:drive|docs)\.google\.com[^\s)<>|\]\"']*"
    r"|gs://[^\s)<>|\]\"']*",
    re.I,
)
_DRIVE_SECTION_RE = re.compile(
    r"\n*^## Drive Files \(Local Mapping\)[ \t]*\n"
    r"(?:[ \t]*\n|\|.*(?:\n|\Z))*\Z",
    re.M,
)
_EMBED_SECTION_RE = re.compile(
    r"\n*^## Embedded Files \(Drive / Docs / Sheets\)[ \t]*\n"
    r"(?:[ \t]*\n|- .*(?:\n|\Z))*\Z",
    re.M,
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^[ \t]*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^[ \t]*\d+[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


@dataclasses.dataclass(frozen=True)
class ParsedPage:
  """A crawled Markdown page split into frontmatter and body."""

  meta: dict[str, str]
  body: str


@dataclasses.dataclass(frozen=True)
class InlineImage:
  """One local image ready to be embedded inline in HTML for Layout Parser.

  Attributes:
    raw_bytes: Original uncompressed image bytes from disk.
    ext: Normalised lowercase extension without leading dot (`png`, `jpg`).
  """

  raw_bytes: bytes
  ext: str


def parse_page(text: str) -> ParsedPage:
  """Splits a crawled `.md` file into frontmatter and body."""
  text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
  match = _FRONTMATTER_RE.match(text)
  if match is None:
    return ParsedPage(meta={}, body=text)
  meta: dict[str, str] = {}
  for line in match.group(1).split("\n"):
    key, sep, value = line.partition(":")
    if not sep:
      continue
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
      value = value[1:-1].replace('\\"', '"')
    meta[key.strip()] = value
  return ParsedPage(meta=meta, body=text[match.end():].lstrip("\n"))


def resolve_ref(page_path: str, ref: str) -> str | None:
  """Resolves a page-relative Markdown link to a normalised folder path."""
  if not ref or _SCHEME_RE.match(ref):
    return None
  joined = posixpath.normpath(
      posixpath.join(posixpath.dirname(page_path), parse.unquote(ref))
  )
  if joined.startswith(("/", "..")) or joined == ".":
    return None
  return unicodedata.normalize("NFC", joined)


def local_image_paths(body: str, page_path: str) -> list[str]:
  """Lists unique local image paths referenced by a page body, in order."""
  paths: dict[str, None] = {}
  for match in _IMAGE_RE.finditer(body):
    path = resolve_ref(page_path, match.group(2))
    if path is not None:
      paths[path] = None
  return list(paths)


def strip_trailing_sections(body: str) -> str:
  """Removes the crawler's two trailing Drive summary sections."""
  body = _DRIVE_SECTION_RE.sub("", body.rstrip() + "\n")
  body = _EMBED_SECTION_RE.sub("", body.rstrip() + "\n")
  return body.rstrip()


def scrub_forbidden(text: str) -> str:
  """Removes any remaining Drive, Docs, or GCS URI strings."""
  while _FORBIDDEN_TEXT_RE.search(text):
    text = _FORBIDDEN_TEXT_RE.sub("", text)
  return text


def encode_data_uri(raw_bytes: bytes, ext: str) -> str | None:
  """Encodes PNG or JPEG bytes as an uncompressed Base64 data URI.

  Discovery Engine's HTML Layout Parser only decodes inline PNG and JPEG Base64
  data URIs. Raw bytes are encoded without compression or resizing; other
  formats (such as GIF, WebP, and SVG) return None so they are omitted cleanly.

  Args:
    raw_bytes: Original uncompressed image bytes.
    ext: Image extension (`png`, `jpg`, or `jpeg`).

  Returns:
    A `data:image/(png|jpeg);base64,...` URI string, or None if unsupported.
  """
  norm_ext = ext.lower().lstrip(".")
  if norm_ext == "png":
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
  if norm_ext in ("jpg", "jpeg"):
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
  return None


def _render_figure(alt: str, data_uri: str | None) -> str:
  """Builds an inline `<figure>` for Layout Parser, or "" when omitted."""
  if data_uri is None:
    return ""
  clean_alt = alt.strip() or "image"
  escaped_alt = html.escape(clean_alt, quote=True)
  return f'<figure><img src="{data_uri}" alt="{escaped_alt}" /></figure>'


def _inline_html(
    text: str,
    *,
    page_path: str,
    data_uris: Mapping[str, str | None],
) -> str:
  """Converts inline Markdown (images, links, bold) in one line to HTML."""
  placeholders: dict[str, str] = {}

  def stash(fragment: str) -> str:
    key = f"__HTML_FRAG_{len(placeholders)}__"
    placeholders[key] = fragment
    return key

  def swap_image(match: re.Match[str]) -> str:
    path = resolve_ref(page_path, match.group(2))
    if path is None:
      return ""
    fragment = _render_figure(match.group(1), data_uris.get(path))
    if not fragment:
      return ""
    return stash(fragment)

  def swap_link(match: re.Match[str]) -> str:
    label = html.escape(match.group(1))
    href = html.escape(match.group(2), quote=True)
    return stash(f'<a href="{href}">{label}</a>')

  # Preserve explicit <br> emitted inside crawled Markdown table cells.
  out = re.sub(r"<br\s*/?>", lambda _: stash("<br />"), text, flags=re.I)
  out = _IMAGE_RE.sub(swap_image, out)
  out = _MD_LINK_RE.sub(swap_link, out)
  out = html.escape(out)
  out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
  for key, fragment in placeholders.items():
    out = out.replace(key, fragment)
  return out


def _clean_markdown(
    body: str,
    *,
    page_path: str,
    manuals: Mapping[str, str],
) -> str:
  """Strips Drive sections, uncollected links, and forbidden URLs."""
  body = strip_trailing_sections(body)
  body = _IMAGE_LINK_RE.sub(r"\1", body)

  def swap_downloaded(match: re.Match[str]) -> str:
    local = resolve_ref(page_path, match.group(2))
    filename = manuals.get(local or "")
    label = match.group(1).strip()
    if filename is None:
      return label
    return f"{label or filename} ({MANUAL_LABEL}: {filename})"

  cleaned_lines: list[str] = []
  for line in body.split("\n"):
    updated = _DOWNLOADED_RE.sub(swap_downloaded, line)
    updated = _FORBIDDEN_LINK_RE.sub("", updated)
    updated = scrub_forbidden(updated)
    ul_match = _UL_RE.match(updated)
    if ul_match and ul_match.group(1).strip() in ("", "Embedded:"):
      continue
    if re.match(r"^[ \t]*[-*+]\s*$", updated):
      continue
    cleaned_lines.append(updated)
  return "\n".join(cleaned_lines)


def _split_table_cells(line: str) -> list[str]:
  """Splits a Markdown table row into cell texts."""
  parts = _CELL_SPLIT_RE.split(line.strip())
  if len(parts) >= 3 and not parts[0].strip() and not parts[-1].strip():
    parts = parts[1:-1]
  return [cell.replace("\\|", "|").strip() for cell in parts]


def _render_blocks(
    md_text: str,
    *,
    page_path: str,
    data_uris: Mapping[str, str | None],
) -> str:
  """Converts cleaned Markdown lines into semantic HTML body fragments."""
  lines = md_text.split("\n")
  out: list[str] = []
  idx = 0

  def inline(text: str) -> str:
    return _inline_html(text, page_path=page_path, data_uris=data_uris)

  while idx < len(lines):
    line = lines[idx]
    stripped = line.strip()
    if not stripped:
      idx += 1
      continue

    heading = _HEADING_RE.match(stripped)
    if heading:
      level = len(heading.group(1))
      content = inline(heading.group(2).strip()).strip()
      if content:
        out.append(f"<h{level}>{content}</h{level}>")
      idx += 1
      continue

    if (
        stripped.startswith("|")
        and idx + 1 < len(lines)
        and _TABLE_SEP_RE.match(lines[idx + 1].strip())
    ):
      headers = _split_table_cells(stripped)
      idx += 2
      rows: list[list[str]] = []
      while idx < len(lines) and lines[idx].strip().startswith("|"):
        rows.append(_split_table_cells(lines[idx].strip()))
        idx += 1
      th_html = "".join(f"<th>{inline(cell).strip()}</th>" for cell in headers)
      body_rows = []
      for row in rows:
        td_html = "".join(f"<td>{inline(cell).strip()}</td>" for cell in row)
        body_rows.append(f"<tr>{td_html}</tr>")
      out.append(
          f"<table><thead><tr>{th_html}</tr></thead>"
          f"<tbody>{''.join(body_rows)}</tbody></table>"
      )
      continue

    if _UL_RE.match(line):
      items: list[str] = []
      while idx < len(lines) and (m := _UL_RE.match(lines[idx])):
        content = inline(m.group(1).strip()).strip()
        if content:
          items.append(f"<li>{content}</li>")
        idx += 1
      if items:
        out.append(f"<ul>{''.join(items)}</ul>")
      continue

    if _OL_RE.match(line):
      items = []
      while idx < len(lines) and (m := _OL_RE.match(lines[idx])):
        content = inline(m.group(1).strip()).strip()
        if content:
          items.append(f"<li>{content}</li>")
        idx += 1
      if items:
        out.append(f"<ol>{''.join(items)}</ol>")
      continue

    para_lines: list[str] = []
    while idx < len(lines):
      cur = lines[idx]
      cur_s = cur.strip()
      if (
          not cur_s
          or _HEADING_RE.match(cur_s)
          or _UL_RE.match(cur)
          or _OL_RE.match(cur)
          or cur_s.startswith("|")
      ):
        break
      para_lines.append(cur_s)
      idx += 1
    rendered_lines = [
        part for p in para_lines if p and (part := inline(p).strip())
    ]
    if rendered_lines:
      out.append(f"<p>{'<br />'.join(rendered_lines)}</p>")

  return "\n".join(out)


def _build_document(
    *,
    doc_title: str,
    header_label: str,
    body_html: str,
) -> bytes:
  """Wraps rendered HTML body fragments in a complete UTF-8 HTML document."""
  doc = (
      "<!DOCTYPE html>\n"
      '<html lang="th">\n'
      f'<head><meta charset="utf-8" /><title>{doc_title}</title></head>\n'
      "<body>\n"
      f"<p>{header_label}</p>\n"
      f"{body_html}\n"
      "</body>\n"
      "</html>\n"
  )
  return scrub_forbidden(doc).encode("utf-8")


def render_page_html(
    body: str,
    *,
    page_path: str,
    title: str,
    category_path: str,
    images: Mapping[str, InlineImage],
    manuals: Mapping[str, str],
    encoder: Callable[[bytes, str], str | None] = encode_data_uri,
    max_html_bytes: int = MAX_HTML_BYTES,
) -> bytes:
  """Renders one crawled Knowledge Page into UTF-8 HTML under 10 MB.

  Embeds local PNG and JPEG images uncompressed as inline Base64 `<figure>`
  tags for Layout Parser and omits unsupported image formats cleanly. If the
  rendered HTML exceeds `max_html_bytes`, falls back to omitting all inline
  Base64 `<figure>` tags while keeping all page text intact.

  Args:
    body: Page Markdown body without frontmatter.
    page_path: Path of the page relative to the crawl output folder.
    title: Page title.
    category_path: Breadcrumb category path.
    images: Local images keyed by normalised crawl-relative path.
    manuals: Imported manual filenames keyed by normalised `local_file` path.
    encoder: Callable turning `(raw_bytes, ext)` into a Base64 data URI.
    max_html_bytes: Maximum allowed UTF-8 HTML byte length.

  Returns:
    Complete UTF-8 encoded HTML document bytes.
  """
  cleaned = _clean_markdown(body, page_path=page_path, manuals=manuals)
  used_paths = local_image_paths(cleaned, page_path)
  header_label = html.escape(f"[{category_path} | {title}]")
  doc_title = html.escape(title)

  data_uris = {
      path: encoder(images[path].raw_bytes, images[path].ext)
      for path in used_paths
      if path in images
  }
  body_html = _render_blocks(
      cleaned, page_path=page_path, data_uris=data_uris
  )
  encoded_html = _build_document(
      doc_title=doc_title, header_label=header_label, body_html=body_html
  )
  if len(encoded_html) <= max_html_bytes:
    return encoded_html

  # Safety fallback: omit inline Base64 <figure> tags while keeping page text.
  empty_uris: dict[str, str | None] = {
      path: None for path in used_paths if path in images
  }
  body_html = _render_blocks(
      cleaned, page_path=page_path, data_uris=empty_uris
  )
  return _build_document(
      doc_title=doc_title, header_label=header_label, body_html=body_html
  )
