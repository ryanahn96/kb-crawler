"""Crawls a private Google Sites KB via Chrome CDP into Markdown and assets."""

import argparse
import base64
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib import parse
from urllib import request

from websockets.exceptions import WebSocketException
from websockets.sync import client as ws_client

DEFAULT_START_URL = (
    "https://sites.google.com/coway.co.th/call-center-kb/"
    "%E0%B8%AB%E0%B8%99%E0%B8%B2%E0%B9%81%E0%B8%A3%E0%B8%81"
)
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_OUTPUT_DIR = Path("./coway_th_kb")

MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}

DRIVE_DIR_NAME = "_drive_files"
DRIVE_LINK_RE = re.compile(
    r"\[([^\]\n]*)\]\((https://(?:drive|docs)\.google\.com/[^)\s]+)\)"
)
DRIVE_ID_RE = re.compile(r"/d/([\w-]{20,})|[?&]id=([\w-]{20,})")
FOLDER_ID_RE = re.compile(r"/folders/([\w-]{20,})")
EXPORT_FMT: dict[str, str] = {
    "document": "docx",
    "spreadsheets": "xlsx",
    "presentation": "pptx",
}
WIN_RESERVED_RE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.I)

NAV_EXTRACT_JS = """
async () => {
  const topHover = document.querySelectorAll('header nav li, nav > ul > li');
  for (const el of topHover) {
    el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
    el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
  }
  await new Promise(r => setTimeout(r, 150));

  const navs = Array.from(document.querySelectorAll('nav'));
  if (!navs.length) return [];
  navs.sort((a, b) => b.querySelectorAll('a[href]').length -
                      a.querySelectorAll('a[href]').length);
  const rootNav = navs[0];

  function parseLi(li) {
    const ownLink = Array.from(li.querySelectorAll('a[href]')).find(
      a => a.closest('li') === li
    ) || null;
    let title = ownLink ? ownLink.textContent.trim() : '';
    if (!title) {
      const clone = li.cloneNode(true);
      const drop = 'ul, [role="menu"], button, svg, [aria-hidden="true"]';
      clone.querySelectorAll(drop).forEach(el => el.remove());
      title = clone.textContent.trim();
    }
    const childLis = Array.from(li.querySelectorAll('li')).filter(
      sub => sub.parentElement && sub.parentElement.closest('li') === li
    );
    return {
      title: title || 'Untitled',
      url: ownLink ? ownLink.href : null,
      children: childLis.map(parseLi),
    };
  }

  const topLis = Array.from(rootNav.querySelectorAll('li')).filter(
    li => !li.parentElement.closest('li')
  );
  const rawTop = topLis.map(parseLi);
  const OVERFLOW = new Set(['More', 'เพิ่มเติม', '더보기']);
  const seen = new Set();
  const result = [];
  for (const item of rawTop) {
    const items = (!item.url && OVERFLOW.has(item.title)) ?
                  item.children : [item];
    for (const sub of items) {
      const key = `${sub.title}||${sub.url || ''}`;
      if (!seen.has(key)) {
        seen.add(key);
        result.push(sub);
      }
    }
  }
  return result;
}
"""

EXTRACT_PAGE_JS = r"""
async (sitePrefix) => {
  // 1. Click all Google Sites toggles & collapsed accordions outside nav.
  const toggleSel = '[role="button"][data-tooltip="Expand"], ' +
                    '[aria-expanded="false"]';
  const collapsed = Array.from(document.querySelectorAll(toggleSel))
    .filter(btn => !btn.closest('nav, header, footer'));
  collapsed.forEach(btn => btn.click());
  if (collapsed.length) await new Promise(r => setTimeout(r, 300));

  // 2. Scroll incrementally top-to-bottom to hydrate lazy images & iframes.
  const maxScroll = () => Math.max(
    document.body.scrollHeight,
    document.documentElement.scrollHeight
  );
  for (let y = 0; y <= maxScroll(); y += 800) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 60));
  }
  window.scrollTo(0, maxScroll());
  await new Promise(r => setTimeout(r, 200));

  // 3. Pick full page container (.UtePc holds all sections in Google Sites).
  const root = document.querySelector('.UtePc') ||
               document.querySelector('main, [role="main"]') ||
               document.body;

  const embedMap = new Map();
  function recordEmbed(url, label) {
    if (!url || url.startsWith('about:') || url.startsWith('javascript:')) {
      return;
    }
    if (!embedMap.has(url) || (!embedMap.get(url) && label)) {
      embedMap.set(url, label || '');
    }
  }

  const links = Array.from(root.querySelectorAll('a[href]'))
    .filter(a => a.getAttribute('aria-label') !== 'Copy heading link')
    .map(a => a.href)
    .filter(href => href.startsWith(sitePrefix));

  const imageUrls = [];
  const imageIndex = new Map();
  function registerImage(src) {
    if (!src || src.startsWith('data:') ||
        src.includes('gstatic.com/images/icons/')) {
      return null;
    }
    if (!imageIndex.has(src)) {
      const token = `__IMG_TOKEN_${imageUrls.length}__`;
      imageIndex.set(src, token);
      imageUrls.push(src);
    }
    return imageIndex.get(src);
  }

  const SKIP = new Set([
    'SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'NAV', 'HEADER', 'FOOTER',
  ]);
  const CONTENT_SEL = 'img, p, h1, h2, h3, h4, h5, h6, table, ul, ol, ' +
                      'iframe, [data-embed-doc-id], [data-embed-open-url]';

  function tableToMd(table) {
    const rows = Array.from(table.querySelectorAll('tr')).map(tr =>
      Array.from(tr.children)
        .filter(c => c.tagName === 'TD' || c.tagName === 'TH')
        .map(c => inlineMd(c).replace(/\|/g, '\\|')
                             .replace(/\n+/g, '<br>').trim())
    ).filter(r => r.length > 0);
    if (!rows.length) return '';
    const cols = Math.max(...rows.map(r => r.length));
    const pad = r => r.concat(Array(cols - r.length).fill(''));
    const header = '| ' + pad(rows[0]).join(' | ') + ' |';
    const sep = '| ' + Array(cols).fill('---').join(' | ') + ' |';
    const body = rows.slice(1).map(r => '| ' + pad(r).join(' | ') + ' |');
    return '\n\n' + [header, sep, ...body].join('\n') + '\n\n';
  }

  function listToMd(listEl, depth) {
    const ordered = listEl.tagName === 'OL';
    const items = Array.from(listEl.children).filter(c => c.tagName === 'LI');
    return '\n' + items.map((li, idx) => {
      const bullet = ordered ? `${idx + 1}. ` : '- ';
      const indent = '  '.repeat(depth);
      const parts = [];
      let subLists = '';
      for (const ch of li.childNodes) {
        const isList = ch.nodeType === 1 &&
                       (ch.tagName === 'UL' || ch.tagName === 'OL');
        if (isList) {
          subLists += listToMd(ch, depth + 1);
        } else {
          parts.push(nodeToMd(ch, depth));
        }
      }
      const line = parts.join('').replace(/\n+/g, ' ').trim();
      return `${indent}${bullet}${line}${subLists}`;
    }).join('\n') + (depth === 0 ? '\n\n' : '');
  }

  function inlineMd(node) {
    return Array.from(node.childNodes).map(c => nodeToMd(c, 0)).join('');
  }

  function toggleHeaderToMd(toggleEl) {
    const title = inlineMd(toggleEl)
      .replace(/\*\*/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    return title ? `\n\n### ${title}\n\n` : '';
  }

  function nodeToMd(node, depth) {
    if (node.nodeType === 3) {
      return node.nodeValue.replace(/[ \t]+/g, ' ');
    }
    if (node.nodeType !== 1) return '';
    const tag = node.tagName;
    if (SKIP.has(tag)) return '';
    if (node.getAttribute('aria-label') === 'Copy heading link') return '';
    const tip = node.getAttribute('data-tooltip');
    if (node.getAttribute('role') === 'button' &&
        (tip === 'Expand' || tip === 'Collapse')) {
      return '';
    }
    if (node.getAttribute('aria-hidden') === 'true' &&
        !node.querySelector('img, iframe, p, table, a')) {
      return '';
    }

    if (node.classList && node.classList.contains('vhaaFf')) {
      return toggleHeaderToMd(node);
    }

    const docId = node.getAttribute('data-embed-doc-id');
    const openUrl = node.getAttribute('data-embed-open-url') ||
                    node.getAttribute('data-url') ||
                    (docId ? `https://drive.google.com/file/d/${docId}/view`
                           : '');
    if (openUrl && !node.querySelector('iframe')) {
      const label = (node.getAttribute('aria-label') || docId || 'Embedded')
        .replace(/[\[\]\n]/g, ' ').trim();
      recordEmbed(openUrl, label);
      return `\n\n- Embedded: [${label}](${openUrl})\n\n`;
    }

    if (tag === 'IFRAME') {
      const src = node.src || node.getAttribute('data-src') || '';
      if (!src || src.startsWith('about:')) return '';
      const label = (node.getAttribute('aria-label') || 'Embedded Frame')
        .replace(/[\[\]\n]/g, ' ').trim();
      recordEmbed(src, label);
      return `\n\n- Embedded: [${label}](${src})\n\n`;
    }

    if (/^H[1-6]$/.test(tag)) {
      const text = inlineMd(node).replace(/\s+/g, ' ').trim();
      return text ? `\n\n${'#'.repeat(Number(tag[1]))} ${text}\n\n` : '';
    }
    if (tag === 'BR') return '\n';
    if (tag === 'HR') return '\n\n---\n\n';
    if (tag === 'TABLE') return tableToMd(node);
    if (tag === 'UL' || tag === 'OL') return listToMd(node, depth);
    if (tag === 'PRE') {
      return `\n\n\`\`\`\n${node.textContent.trim()}\n\`\`\`\n\n`;
    }
    if (tag === 'CODE') {
      const t = node.textContent.trim();
      return t ? `\`${t}\`` : '';
    }
    if (tag === 'STRONG' || tag === 'B') {
      const t = inlineMd(node).trim();
      return t ? `**${t}**` : '';
    }
    if (tag === 'SPAN' &&
        (node.style.fontWeight === '700' || node.style.fontWeight === 'bold')) {
      const t = inlineMd(node).replace(/\*\*/g, '').trim();
      return t ? `**${t}**` : '';
    }
    if (tag === 'EM' || tag === 'I') {
      const t = inlineMd(node).trim();
      return t ? `*${t}*` : '';
    }
    if (tag === 'A') {
      const href = node.href || '';
      const t = inlineMd(node).replace(/\s+/g, ' ').trim();
      if (!href || href.startsWith('javascript:')) return t;
      return `[${t || href}](${href})`;
    }
    if (tag === 'IMG') {
      const src = node.currentSrc || node.src ||
                  node.getAttribute('data-src') || '';
      const token = registerImage(src);
      if (!token) return '';
      const alt = (node.alt || 'image').replace(/[\[\]\n]/g, ' ').trim();
      return `\n\n![${alt}](${token})\n\n`;
    }
    const kids = Array.from(node.childNodes);
    const inner = kids.map(c => nodeToMd(c, depth)).join('');
    if (['P', 'DIV', 'SECTION', 'ARTICLE', 'BLOCKQUOTE'].includes(tag)) {
      return `\n\n${inner.trim()}\n\n`;
    }
    return inner;
  }

  function sectionCols(sec) {
    const row = sec.querySelector('.LS81yb');
    if (!row) return null;
    const cols = Array.from(row.children).filter(
      c => c.querySelector(CONTENT_SEL)
    );
    return cols.length ? cols : null;
  }

  function isImageOnlyCols(cols) {
    return cols && cols.every(
      c => c.querySelector('img') && c.textContent.trim() === ''
    );
  }

  // 4. Walk sections, pairing N-column image rows with N-column detail rows.
  const sections = Array.from(root.querySelectorAll('section')).filter(
    s => !s.closest('nav, header, footer') &&
         !s.parentElement.closest('section')
  );
  let rawMd = '';
  if (sections.length) {
    for (let i = 0; i < sections.length; i++) {
      const colsA = sectionCols(sections[i]);
      const colsB = (i + 1 < sections.length) ?
                    sectionCols(sections[i + 1]) : null;
      if (colsA && colsB && colsA.length === colsB.length &&
          isImageOnlyCols(colsA) && !isImageOnlyCols(colsB)) {
        for (let k = 0; k < colsA.length; k++) {
          rawMd += nodeToMd(colsA[k], 0) + '\n\n' + nodeToMd(colsB[k], 0) +
                   '\n\n---\n\n';
        }
        i += 1;
      } else {
        rawMd += nodeToMd(sections[i], 0) + '\n\n';
      }
    }
  } else {
    rawMd = nodeToMd(root, 0);
  }

  // 5. Download authenticated same-session images in batches of 8.
  async function fetchAsBase64(url) {
    try {
      const resp = await fetch(url, {credentials: 'include'});
      if (!resp.ok) return null;
      const blob = await resp.blob();
      if (!blob.size || blob.size > 15 * 1024 * 1024) return null;
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result || '');
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      const comma = String(dataUrl).indexOf(',');
      return comma > 0 ? {
        mime: blob.type || 'image/png',
        b64: String(dataUrl).slice(comma + 1),
      } : null;
    } catch (err) {
      return null;
    }
  }

  const images = [];
  for (let i = 0; i < imageUrls.length; i += 8) {
    const batch = imageUrls.slice(i, i + 8);
    const fetched = await Promise.all(batch.map(fetchAsBase64));
    for (let j = 0; j < batch.length; j++) {
      images.push({
        token: `__IMG_TOKEN_${i + j}__`,
        url: batch[j],
        data: fetched[j],
      });
    }
  }

  const embeds = Array.from(embedMap.entries()).map(
    ([url, label]) => ({url, label})
  );
  const md = rawMd
    .replace(/\*{4}/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return {markdown: md, embeds, links, images};
}
"""


@dataclass
class NavNode:
  """Represents a navigation tab or hover dropdown item in Google Sites."""

  title: str
  url: str | None = None
  children: list["NavNode"] = field(default_factory=list)


@dataclass
class CrawlState:
  """Mutable crawl bookkeeping shared across pages."""

  output_dir: Path
  visited: set[str] = field(default_factory=set)
  discovered_pool: set[str] = field(default_factory=set)
  manifest: list[dict[str, Any]] = field(default_factory=list)
  drive_files: dict[str, dict[str, Any]] = field(default_factory=dict)


class CdpSession:
  """Minimal Chrome DevTools Protocol tab session over stdlib + websockets."""

  def __init__(self, cdp_url: str) -> None:
    self._cdp_url = cdp_url.rstrip("/")
    self._target_id = ""
    self._ws: ws_client.ClientConnection | None = None
    self._msg_id = 0

  def __enter__(self) -> "CdpSession":
    candidates = [self._cdp_url]
    if "127.0.0.1:" in self._cdp_url:
      candidates.append(self._cdp_url.replace("127.0.0.1:", "[::1]:"))
    info: dict[str, Any] = {}
    for base in candidates:
      new_url = f"{base}/json/new"
      for method in ("PUT", "GET"):
        try:
          req = request.Request(new_url, method=method)
          with request.urlopen(req, timeout=3) as resp:
            raw = resp.read().decode("utf-8").strip()
            if raw:
              info = json.loads(raw)
              self._cdp_url = base
              break
        except OSError:
          continue
      if info:
        break
    if not info:
      raise RuntimeError(f"Could not open CDP tab at {self._cdp_url}")
    self._target_id = info["id"]
    self._ws = ws_client.connect(
        info["webSocketDebuggerUrl"],
        origin=self._cdp_url.replace("[::1]:", "127.0.0.1:"),
        max_size=None,
    )
    self.call("Page.enable")
    self.call("Network.enable")
    self.call(
        "Emulation.setDeviceMetricsOverride",
        width=2560,
        height=1440,
        deviceScaleFactor=1,
        mobile=False,
    )
    return self

  def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
    if self._ws is not None:
      self._ws.close()
    if self._target_id:
      try:
        close_url = f"{self._cdp_url}/json/close/{self._target_id}"
        with request.urlopen(close_url, timeout=3):
          pass
      except OSError:
        pass

  def call(self, method: str, **params: Any) -> dict[str, Any]:
    """Sends a CDP command and waits for the matching response frame."""
    if self._ws is None:
      raise RuntimeError("CDP session is not open.")
    self._msg_id += 1
    target_id = self._msg_id
    payload = json.dumps({"id": target_id, "method": method, "params": params})
    self._ws.send(payload)
    while True:
      frame = json.loads(self._ws.recv(timeout=120))
      if frame.get("id") == target_id:
        if "error" in frame:
          raise RuntimeError(f"CDP {method} failed: {frame['error']}")
        return frame.get("result", {})

  def eval_js(self, expression: str, *args: Any) -> Any:
    """Evaluates an async/sync JS function in the page and returns its value."""
    arg_json = ", ".join(json.dumps(a) for a in args)
    wrapped = f"({expression})({arg_json})"
    res = self.call(
        "Runtime.evaluate",
        expression=wrapped,
        awaitPromise=True,
        returnByValue=True,
    )
    if "exceptionDetails" in res:
      raise RuntimeError(f"JS error: {res['exceptionDetails']}")
    return res.get("result", {}).get("value")

  def goto(self, url: str, wait_selector: str) -> str:
    """Navigates to url, waits for wait_selector, and returns landed URL."""
    self.call("Page.navigate", url=url)
    wait_js = """
    async (sel) => {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        const ready = document.readyState === 'complete' ||
                      document.readyState === 'interactive';
        if (ready && (location.hostname.includes('accounts.google.com') ||
                      document.querySelector(sel))) {
          await new Promise(r => setTimeout(r, 350));
          return location.href;
        }
        await new Promise(r => setTimeout(r, 100));
      }
      return location.href;
    }
    """
    return str(self.eval_js(wait_js, wait_selector))

  def fetch_bytes(self, url: str) -> tuple[int, dict[str, str], bytes]:
    """Fetches url with the browser's cookies, bypassing page CORS rules."""
    frame = self.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
    res = self.call(
        "Network.loadNetworkResource",
        frameId=frame,
        url=url,
        options={"disableCache": True, "includeCredentials": True},
    )["resource"]
    status = int(res.get("httpStatusCode") or 0)
    headers = {k.lower(): v for k, v in (res.get("headers") or {}).items()}
    if not res.get("success") or "stream" not in res:
      return status, headers, b""
    chunks: list[bytes] = []
    while True:
      part = self.call("IO.read", handle=res["stream"], size=1 << 20)
      data = part.get("data", "")
      if part.get("base64Encoded"):
        chunks.append(base64.b64decode(data))
      else:
        chunks.append(data.encode("utf-8"))
      if part.get("eof"):
        break
    self.call("IO.close", handle=res["stream"])
    return status, headers, b"".join(chunks)


def sanitize_name(name: str, max_len: int = 50) -> str:
  """Sanitizes a tab or page title into a cross-platform filename."""
  cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]+', "_", name).strip(" .")
  cleaned = cleaned[:max_len].rstrip(" .")
  if WIN_RESERVED_RE.match(cleaned):
    cleaned = f"_{cleaned}"
  return cleaned or "untitled"


def normalize_url(url: str) -> str:
  """Canonicalizes URL path encoding and strips fragments, queries, and '/'."""
  clean, _ = parse.urldefrag(url)
  parsed = parse.urlsplit(clean)
  norm_path = parse.quote(parse.unquote(parsed.path), safe="/").rstrip("/")
  return f"{parsed.scheme}://{parsed.netloc}{norm_path}"


def derive_site_prefix(start_url: str) -> str:
  """Derives the Google Sites base URL prefix from the starting page URL."""
  parsed = parse.urlsplit(normalize_url(start_url))
  parts = [p for p in parsed.path.split("/") if p]
  keep = 2 if len(parts) >= 2 else len(parts)
  prefix_path = "/" + "/".join(parts[:keep]) if keep else ""
  return f"{parsed.scheme}://{parsed.netloc}{prefix_path}"


def dict_to_node(raw: dict[str, Any]) -> NavNode:
  """Converts a raw JS navigation dictionary into a NavNode tree."""
  children = [dict_to_node(c) for c in raw.get("children", [])]
  return NavNode(
      title=raw.get("title", "Untitled"),
      url=raw.get("url"),
      children=children,
  )


def materialize_images(
    body_md: str,
    images: list[dict[str, Any]],
    out_path: Path,
) -> tuple[str, int]:
  """Writes downloaded images to local _assets folder and rewrites MD tokens."""
  if not images:
    return body_md, 0
  asset_rel_dir = Path("_assets") / out_path.stem
  asset_abs_dir = out_path.parent / asset_rel_dir
  saved_count = 0
  for idx, item in enumerate(images, start=1):
    token = item["token"]
    data = item.get("data")
    if data and data.get("b64"):
      ext = MIME_EXT.get(data.get("mime", ""), ".png")
      asset_abs_dir.mkdir(parents=True, exist_ok=True)
      filename = f"img_{idx:02d}{ext}"
      (asset_abs_dir / filename).write_bytes(base64.b64decode(data["b64"]))
      rel_uri = parse.quote(f"{asset_rel_dir.as_posix()}/{filename}", safe="/")
      body_md = body_md.replace(token, rel_uri)
      saved_count += 1
    else:
      body_md = body_md.replace(token, item["url"])
  return body_md, saved_count


def drive_file_ref(url: str) -> tuple[str, str] | None:
  """Returns (kind, file_id) for a Drive/Docs URL, or None if not a file."""
  folder = FOLDER_ID_RE.search(url)
  if folder:
    return "folder", folder.group(1)
  match = DRIVE_ID_RE.search(url)
  if not match:
    return None
  file_id = match.group(1) or match.group(2)
  kind = parse.urlsplit(url).path.split("/")[1]
  if "folderview" in kind:
    return "folder", file_id
  if kind in EXPORT_FMT:
    return kind, file_id
  if kind in ("file", "open", "uc"):
    return "file", file_id
  return None


def download_candidates(kind: str, file_id: str) -> list[str]:
  """Lists download URLs to try in order for a Drive file."""
  urls = [
      "https://drive.usercontent.google.com/download"
      f"?id={file_id}&export=download&confirm=t",
      f"https://drive.google.com/uc?export=download&id={file_id}",
  ]
  if kind in EXPORT_FMT:
    urls.insert(
        0,
        f"https://docs.google.com/{kind}/d/{file_id}/export"
        f"?format={EXPORT_FMT[kind]}",
    )
  return urls


def filename_from_headers(headers: dict[str, str], fallback: str) -> str:
  """Extracts a safe filename from Content-Disposition (RFC 5987 aware)."""
  disp = headers.get("content-disposition", "")
  star = re.search(r"filename\*=[\w-]+'[^']*'([^;]+)", disp)
  plain = re.search(r'filename="?([^";]+)"?', disp)
  if star:
    name = parse.unquote(star.group(1).strip().strip('"'))
  elif plain:
    name = plain.group(1).strip()
  else:
    mime = headers.get("content-type", "").split(";")[0].strip()
    name = fallback + MIME_EXT.get(mime, ".bin")
  stem, suffix = os.path.splitext(name)
  return sanitize_name(stem) + suffix


def fetch_drive_file(
    session: CdpSession,
    kind: str,
    file_id: str,
    drive_dir: Path,
) -> dict[str, Any]:
  """Downloads a Drive file if the session can access it, else records why."""
  record: dict[str, Any] = {
      "file_id": file_id,
      "kind": kind,
      "status": "folder_skipped",
      "accessible": None,
      "filename": None,
      "local_file": None,
      "size_bytes": 0,
      "http_status": None,
      "source_urls": [],
      "referenced_by": [],
  }
  if kind == "folder":
    return record
  record.update(status="error", accessible=False)
  for url in download_candidates(kind, file_id):
    try:
      status, headers, body = session.fetch_bytes(url)
    except (OSError, RuntimeError, WebSocketException) as exc:
      print(f"  ! Drive fetch error {file_id}: {exc}")
      continue
    record["http_status"] = status
    is_html = "text/html" in headers.get("content-type", "")
    if status == 200 and body and not is_html:
      name = f"{file_id}_{filename_from_headers(headers, file_id)}"
      drive_dir.mkdir(parents=True, exist_ok=True)
      (drive_dir / name).write_bytes(body)
      record.update(
          status="downloaded",
          accessible=True,
          filename=name.removeprefix(f"{file_id}_"),
          local_file=f"{DRIVE_DIR_NAME}/{name}",
          size_bytes=len(body),
      )
      return record
    if status == 404:
      record["status"] = "not_found"
    elif status in (200, 401, 403) or is_html:
      record["status"] = "no_access"
      text = re.sub(r"(?s)<(style|script).*?</\1>|<[^>]+>", " ", body.decode(
          "utf-8", errors="replace"
      ))
      record["detail"] = (
          " ".join(text.split())[:200] or f"HTTP {status} (download denied)"
      )
  return record


def drive_local_link(
    rec: dict[str, Any],
    out_path: Path,
    drive_dir: Path,
) -> str:
  """Returns the URL-encoded path from a page to a downloaded Drive file."""
  rel = os.path.relpath(drive_dir.parent / rec["local_file"], out_path.parent)
  return parse.quote(Path(rel).as_posix(), safe="/.")


def materialize_drive_files(
    session: CdpSession,
    body_md: str,
    out_path: Path,
    drive_files: dict[str, dict[str, Any]],
    drive_dir: Path,
) -> tuple[str, int]:
  """Downloads linked Drive files once and points MD links at local copies.

  Args:
    session: Logged-in CDP session used to download with browser cookies.
    body_md: Page Markdown containing original Drive/Docs links.
    out_path: Markdown file the body will be written to.
    drive_files: Crawl-wide records keyed by Drive file id (mutated).
    drive_dir: Folder that stores downloaded Drive files.

  Returns:
    The Markdown with downloaded files linked locally (original URL kept
    beside each link) and the number of distinct local files on the page.
  """
  page_ids: set[str] = set()

  def swap(match: re.Match[str]) -> str:
    label, url = match.group(1), match.group(2)
    ref = drive_file_ref(url)
    if ref is None:
      return match.group(0)
    kind, file_id = ref
    rec = drive_files.get(file_id)
    if rec is None:
      rec = fetch_drive_file(session, kind, file_id, drive_dir)
      drive_files[file_id] = rec
      print(f"  - Drive {file_id}: {rec['status']}")
    if url not in rec["source_urls"]:
      rec["source_urls"].append(url)
    page_ref = out_path.as_posix()
    if page_ref not in rec["referenced_by"]:
      rec["referenced_by"].append(page_ref)
    if rec["status"] != "downloaded":
      return f"{match.group(0)} (Drive: {rec['status']})"
    page_ids.add(file_id)
    if label.strip("* ").startswith("http"):
      label = rec["filename"]
    local = drive_local_link(rec, out_path, drive_dir)
    return f"[{label}]({local}) ([Drive]({url}))"

  return DRIVE_LINK_RE.sub(swap, body_md), len(page_ids)


def write_drive_report(
    drive_files: dict[str, dict[str, Any]],
    report_path: Path,
) -> None:
  """Writes Drive download/access status for every linked Drive file."""
  files = sorted(
      drive_files.values(), key=lambda r: (r["status"], r["file_id"])
  )
  summary: dict[str, int] = {"total": len(files)}
  for rec in files:
    summary[rec["status"]] = summary.get(rec["status"], 0) + 1
  report_path.parent.mkdir(parents=True, exist_ok=True)
  report_path.write_text(
      json.dumps(
          {"summary": summary, "files": files}, ensure_ascii=False, indent=2
      )
      + "\n",
      encoding="utf-8",
  )
  print(f"Drive files: {summary}")


def save_page(
    session: CdpSession,
    url: str,
    site_prefix: str,
    out_path: Path,
    title: str,
    breadcrumbs: list[str],
    state: CrawlState,
) -> None:
  """Fetches an unvisited page, saves local images, and writes Markdown."""
  norm = normalize_url(url)
  if not norm.startswith(site_prefix) or norm in state.visited:
    return
  state.visited.add(norm)

  crumb_str = " > ".join(breadcrumbs)
  print(f"[CRAWL] {crumb_str} ({parse.unquote(norm)})")

  extracted: dict[str, Any] | None = None
  for attempt in range(2):
    try:
      session.goto(norm, "section, [role='main'], main, body")
      extracted = session.eval_js(EXTRACT_PAGE_JS, site_prefix)
      break
    except (OSError, RuntimeError, WebSocketException) as exc:
      if attempt == 1:
        print(f"  ! Failed to crawl {norm}: {exc}")
        return
  if extracted is None:
    return

  body_md, saved_imgs = materialize_images(
      body_md=extracted.get("markdown", ""),
      images=extracted.get("images", []),
      out_path=out_path,
  )
  body_md, saved_drive = materialize_drive_files(
      session=session,
      body_md=body_md,
      out_path=out_path,
      drive_files=state.drive_files,
      drive_dir=state.output_dir / DRIVE_DIR_NAME,
  )
  embeds: list[dict[str, str]] = extracted.get("embeds", [])
  for link in extracted.get("links", []):
    state.discovered_pool.add(normalize_url(link))

  if embeds:
    lines = [
        f"- [{e['label'] or e['url']}]({e['url']})" for e in embeds if e["url"]
    ]
    if lines:
      embed_block = "\n".join(dict.fromkeys(lines))
      body_md += f"\n\n## Embedded Files (Drive / Docs / Sheets)\n{embed_block}"

  page_ref = out_path.as_posix()
  drive_rows = [
      "| Drive file | Status | Local file |",
      "| --- | --- | --- |",
  ]
  for rec in state.drive_files.values():
    if page_ref not in rec["referenced_by"]:
      continue
    local = "-"
    if rec["status"] == "downloaded":
      link = drive_local_link(
          rec, out_path, state.output_dir / DRIVE_DIR_NAME
      )
      name = rec["filename"].replace("|", "\\|")
      local = f"[{name}]({link})"
    drive_rows.append(
        f"| [{rec['file_id']}]({rec['source_urls'][0]}) "
        f"| {rec['status']} | {local} |"
    )
  if len(drive_rows) > 2:
    body_md += "\n\n## Drive Files (Local Mapping)\n\n" + "\n".join(drive_rows)

  safe_title = title.replace('"', '\\"')
  safe_crumbs = crumb_str.replace('"', '\\"')
  header = (
      "---\n"
      f'title: "{safe_title}"\n'
      f'category_path: "{safe_crumbs}"\n'
      f'source_url: "{norm}"\n'
      f'decoded_url: "{parse.unquote(norm)}"\n'
      f"saved_images: {saved_imgs}\n"
      f"saved_drive_files: {saved_drive}\n"
      "---\n\n"
      f"# {title}\n\n"
  )
  out_path.write_text(f"{header}{body_md.strip()}\n", encoding="utf-8")
  state.manifest.append({
      "title": title,
      "breadcrumbs": breadcrumbs,
      "url": norm,
      "decoded_url": parse.unquote(norm),
      "file": out_path.as_posix(),
      "saved_images": saved_imgs,
      "saved_drive_files": saved_drive,
      "embedded_files": len(embeds),
  })


def crawl_tree(
    session: CdpSession,
    nodes: list[NavNode],
    site_prefix: str,
    parent_dir: Path,
    breadcrumbs: list[str],
    state: CrawlState,
) -> None:
  """Recursively traverses top tabs and hover sub-items into folders/files."""
  parent_dir.mkdir(parents=True, exist_ok=True)
  for idx, node in enumerate(nodes, start=1):
    prefix = f"{idx:02d}_{sanitize_name(node.title)}"
    crumbs = [*breadcrumbs, node.title]
    if node.children:
      folder = parent_dir / prefix
      folder.mkdir(parents=True, exist_ok=True)
      if node.url:
        save_page(
            session=session,
            url=node.url,
            site_prefix=site_prefix,
            out_path=folder / "00_overview.md",
            title=node.title,
            breadcrumbs=crumbs,
            state=state,
        )
      crawl_tree(
          session=session,
          nodes=node.children,
          site_prefix=site_prefix,
          parent_dir=folder,
          breadcrumbs=crumbs,
          state=state,
      )
    elif node.url:
      save_page(
          session=session,
          url=node.url,
          site_prefix=site_prefix,
          out_path=parent_dir / f"{prefix}.md",
          title=node.title,
          breadcrumbs=crumbs,
          state=state,
      )


def crawl_site(
    start_url: str,
    cdp_url: str,
    output_dir: Path,
    site_prefix: str | None = None,
) -> list[dict[str, Any]]:
  """Connects to Chrome over CDP and exports the Google Sites hierarchy."""
  resolved_prefix = (site_prefix or derive_site_prefix(start_url)).rstrip("/")
  with CdpSession(cdp_url) as session:
    landed_url = session.goto(start_url, "nav")
    if "accounts.google.com" in landed_url:
      raise RuntimeError(
          "Chrome session is not logged into Google Sites yet. "
          "Log in inside the open Chrome window first, then re-run."
      )

    raw_tree = session.eval_js(NAV_EXTRACT_JS) or []
    nav_tree = [dict_to_node(item) for item in raw_tree]
    print(f"Found {len(nav_tree)} top-level tabs under {resolved_prefix}")

    state = CrawlState(output_dir=output_dir)

    crawl_tree(
        session=session,
        nodes=nav_tree,
        site_prefix=resolved_prefix,
        parent_dir=output_dir,
        breadcrumbs=[],
        state=state,
    )

    # ponytail: single-pass unlisted sweep; loop until empty if deep chains hide
    unlisted = sorted(state.discovered_pool - state.visited)
    if unlisted:
      unlisted_dir = output_dir / "99_unlisted_pages"
      unlisted_dir.mkdir(parents=True, exist_ok=True)
      for idx, url in enumerate(unlisted, start=1):
        rel = parse.unquote(url.removeprefix(resolved_prefix).strip("/"))
        slug = rel or f"page_{idx}"
        save_page(
            session=session,
            url=url,
            site_prefix=resolved_prefix,
            out_path=unlisted_dir / f"{idx:03d}_{sanitize_name(slug)}.md",
            title=slug,
            breadcrumbs=["Unlisted", slug],
            state=state,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps(state.manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_drive_report(state.drive_files, output_dir / "_drive_files.json")
    print(f"Saved {len(state.manifest)} pages to {output_dir.resolve()}")
    return state.manifest


def parse_args() -> argparse.Namespace:
  """Parses CLI flags for the Google Sites KB crawler."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--start-url", default=DEFAULT_START_URL)
  parser.add_argument("--site-prefix", default=None)
  parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
  parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
  return parser.parse_args()


def main() -> None:
  """CLI entrypoint."""
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8", errors="replace")
  args = parse_args()
  crawl_site(
      start_url=args.start_url,
      cdp_url=args.cdp_url,
      output_dir=args.output_dir,
      site_prefix=args.site_prefix,
  )


if __name__ == "__main__":
  main()
