"""
disk_analyzer.py
Analyzes disk space usage across all system drives and uses Gemini AI
to generate personalized cleanup recommendations.

Requirements:
    pip install google-genai psutil

Setup:
    Replace "YOUR_API_KEY_HERE" with your Google AI Studio key,
    or set the GEMINI_API_KEY environment variable.
    Get a free key at: https://aistudio.google.com/apikey
"""

import os
import sys
import json
import shutil
import platform
import stat as _stat
import html as _html
import webbrowser
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── Configuration ──────────────────────────────────────────────────────────────
API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_API_KEY_HERE")

# Minimum folder size to include in the analysis (in MB)
MIN_FOLDER_SIZE_MB = 100

# Top N largest folders per drive
TOP_N_FOLDERS = 20

# Top N largest files in the system
TOP_N_FILES = 30

# Extensions typically safe to review/delete
CLEANUP_EXTENSIONS = {
    "temporary":   [".tmp", ".temp", ".bak", ".old", ".orig"],
    "logs":        [".log", ".log1", ".log2"],
    "dumps":       [".dmp", ".mdmp", ".hdmp"],
    "cache":       [".cache"],
    "archives":    [".zip", ".rar", ".7z", ".tar", ".gz"],
}

# Paths to skip completely during scanning
SKIP_PATHS = {
    "Windows", "System Volume Information", "$Recycle.Bin",
    "pagefile.sys", "hiberfil.sys", "swapfile.sys",
}

# Pre-compute lowercased skip set for fast membership tests in the hot loop
_SKIP_PATHS_LOWER = frozenset(s.lower() for s in SKIP_PATHS)

# Keywords that flag a directory as a temp/cache/log folder
_TEMP_KEYWORDS = ("temp", "tmp", "cache", "logs")

# Mode mask to detect symbolic links without importing stat repeatedly
_S_ISLNK = _stat.S_ISLNK

# ── Helpers ────────────────────────────────────────────────────────────────────

def bytes_to_human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def get_all_drives() -> list[str]:
    """Returns the list of local drive roots available in Windows."""
    if platform.system() == "Windows":
        import ctypes
        drives = []
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                # 2 = DRIVE_REMOVABLE, 3 = DRIVE_FIXED
                dtype = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                if dtype in (2, 3):
                    drives.append(drive_path)
            bitmask >>= 1
        return drives
    else:
        # Linux / macOS: analyze from root
        return ["/"]


def get_desktop_path() -> str:
    home = os.path.expanduser("~")
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            )
            value, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            expanded = os.path.expandvars(value)
            if os.path.isdir(expanded):
                return expanded
        except Exception:
            pass

    # Common fallbacks
    candidates = [
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "Desktop"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
            
    return home


def should_skip(path_str: str) -> bool:
    path_lower = path_str.lower()
    for skip in _SKIP_PATHS_LOWER:
        if skip in path_lower:
            return True
    return False


# ── Scanning ───────────────────────────────────────────────────────────────────

def _scan_subtree(
    root_path: str,
    drive_root: str,
    # Shared accumulators protected by lock
    top_level_sizes: defaultdict,
    temp_folder_sizes: defaultdict,
    big_files: list,
    ext_sizes: defaultdict,
    lock: Lock,
    executor: ThreadPoolExecutor,
    futures: list,
) -> None:
    """
    Recursively scan *root_path* using os.scandir.
    Subdirectories are submitted as new tasks to the shared executor so that
    different branches of the tree are traversed in parallel.
    Each call accumulates file metrics into the shared (lock-protected) structures.
    """
    # Relative path components from drive_root to root_path (used for top-level
    # folder attribution and temp-ancestor detection).
    try:
        rel = os.path.relpath(root_path, drive_root)
    except ValueError:
        rel = "."

    if rel == ".":
        top_level = None
        temp_ancestor = None
        rel_parts: list[str] = []
    else:
        rel_parts = rel.split(os.sep)
        top_level = os.path.join(drive_root, rel_parts[0])

        # Determine the shallowest temp/cache/logs ancestor for this subtree
        temp_ancestor = None
        for i, part in enumerate(rel_parts):
            part_lower = part.lower()
            if any(k in part_lower for k in _TEMP_KEYWORDS):
                temp_ancestor = os.path.join(drive_root, *rel_parts[: i + 1])
                break

    # Accumulate file sizes from this directory; batch them into the shared
    # structures once (single lock acquisition per directory visit).
    local_top_delta = 0
    local_temp_delta = 0
    local_big: list[tuple[int, str]] = []
    local_ext: dict[str, int] = {}

    try:
        with os.scandir(root_path) as it:
            entries = list(it)
    except (PermissionError, OSError):
        return

    subdirs_to_visit: list[str] = []

    for entry in entries:
        name = entry.name
        full_path = entry.path

        if entry.is_dir(follow_symlinks=False):
            # Skip unwanted directories
            if not should_skip(full_path):
                subdirs_to_visit.append(full_path)
        else:
            # File (or symlink — we skip symlinks just like the original)
            if should_skip(full_path):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except (PermissionError, OSError):
                continue
            if _S_ISLNK(st.st_mode):
                continue
            sz = st.st_size

            if top_level:
                local_top_delta += sz
            if temp_ancestor:
                local_temp_delta += sz

            # Extension (fast: rfind avoids creating a Path object)
            dot = name.rfind(".")
            ext = name[dot:].lower() if dot != -1 else ""
            local_ext[ext] = local_ext.get(ext, 0) + sz

            if sz >= 50_000_000:  # 50 MB
                local_big.append((sz, full_path))

    # Flush local accumulations into shared state with a single lock acquisition
    if local_top_delta or local_temp_delta or local_big or local_ext:
        with lock:
            if top_level and local_top_delta:
                top_level_sizes[top_level] += local_top_delta
            if temp_ancestor and local_temp_delta:
                temp_folder_sizes[temp_ancestor] += local_temp_delta
            if local_big:
                big_files.extend(local_big)
            for ext, sz in local_ext.items():
                ext_sizes[ext] += sz

    # Submit subdirectory scans to the thread pool
    for subdir in subdirs_to_visit:
        fut = executor.submit(
            _scan_subtree,
            subdir,
            drive_root,
            top_level_sizes,
            temp_folder_sizes,
            big_files,
            ext_sizes,
            lock,
            executor,
            futures,
        )
        with lock:
            futures.append(fut)


def scan_drive(drive_root: str) -> dict:
    """Scans a drive and collects metrics in a single pass."""
    print(f"\n  Scanning {drive_root} ...", flush=True)

    usage = shutil.disk_usage(drive_root)
    result = {
        "root": drive_root,
        "total_gb":  round(usage.total / 1e9, 2),
        "used_gb":  round(usage.used  / 1e9, 2),
        "free_gb":  round(usage.free  / 1e9, 2),
        "use_pct":   round(usage.used / usage.total * 100, 1),
        "large_folders": [],
        "large_files": [],
        "by_extension":    {},
        "temp_folders":    [],
    }

    top_level_sizes: defaultdict[str, int] = defaultdict(int)
    temp_folder_sizes: defaultdict[str, int] = defaultdict(int)
    big_files: list[tuple[int, str]] = []
    ext_sizes: defaultdict[str, int] = defaultdict(int)
    lock = Lock()

    # Use a thread pool to scan subdirectories in parallel (I/O-bound)
    # We cap at 32 workers; more threads rarely help beyond SSD queue depth.
    max_workers = min(32, (os.cpu_count() or 4) * 4)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: list = []
        # Seed with the top-level subdirectories of the drive
        try:
            with os.scandir(drive_root) as it:
                top_entries = list(it)
        except (PermissionError, OSError):
            top_entries = []

        for entry in top_entries:
            if entry.is_dir(follow_symlinks=False):
                if not should_skip(entry.path):
                    fut = executor.submit(
                        _scan_subtree,
                        entry.path,
                        drive_root,
                        top_level_sizes,
                        temp_folder_sizes,
                        big_files,
                        ext_sizes,
                        lock,
                        executor,
                        futures,
                    )
                    futures.append(fut)
            else:
                # Files directly in the drive root (edge case, e.g. pagefile.sys)
                if not should_skip(entry.path):
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if not _S_ISLNK(st.st_mode):
                            sz = st.st_size
                            dot = entry.name.rfind(".")
                            ext = entry.name[dot:].lower() if dot != -1 else ""
                            with lock:
                                ext_sizes[ext] += sz
                                if sz >= 50_000_000:
                                    big_files.append((sz, entry.path))
                    except (PermissionError, OSError):
                        pass

        # Wait for all submitted futures, including those dynamically added
        # by child tasks. We loop until no new futures appear.
        seen = 0
        while True:
            with lock:
                current = list(futures)
            pending = current[seen:]
            if not pending:
                break
            for fut in as_completed(pending):
                try:
                    fut.result()
                except Exception:
                    pass  # Individual subtree errors are silently skipped
            seen = len(current)

    # Sort and process results
    sorted_top_folders = sorted(top_level_sizes.items(), key=lambda x: x[1], reverse=True)
    result["large_folders"] = [
        {"path": p, "size": bytes_to_human(s)}
        for p, s in sorted_top_folders[:TOP_N_FOLDERS]
        if s >= MIN_FOLDER_SIZE_MB * 1e6
    ]

    big_files.sort(reverse=True)
    result["large_files"] = [
        {"path": p, "size": bytes_to_human(s)}
        for s, p in big_files[:TOP_N_FILES]
    ]

    sorted_temp_folders = sorted(temp_folder_sizes.items(), key=lambda x: x[1], reverse=True)
    result["temp_folders"] = [
        {"path": p, "size": bytes_to_human(s)}
        for p, s in sorted_temp_folders[:15]
        if s >= 10 * 1e6
    ]

    # Extensions: top 15 by size
    ext_sorted = sorted(ext_sizes.items(), key=lambda x: x[1], reverse=True)[:15]
    result["by_extension"] = {
        ext if ext else "(no extension)": bytes_to_human(sz)
        for ext, sz in ext_sorted
    }

    return result


# ── Gemini API Call ────────────────────────────────────────────────────────────

def ask_gemini(scan_data: dict) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("\n[ERROR] The 'google-genai' package is not installed.")
        print("  Please run: pip install google-genai")
        sys.exit(1)

    try:
        client = genai.Client(api_key=API_KEY)

        system_instruction = """You are an expert in Windows storage optimization.
Always respond in English. Your responses must be brief, direct, and well-structured in markdown.
Use exactly the format requested, without adding filler text or long introductions.
Do not recommend deleting operating system files or critical Windows folders."""

        user_prompt = f"""Analyze this disk report and respond VERY CONCISELY using exactly this markdown format:

## Disk Status
One line per disk: `LETTER:` — X GB used of Y GB (Z% free) — status (OK / Attention / Critical).

## Top 5 space saving actions
Each action in this exact format:
### N. Short action title
- **What:** description in one sentence
- **Impact:** ~X GB
- **Safety:** ✅ Safe | ⚠️ With caution | 🔴 Manual review

## Preventive tips
3 short bullet points, maximum one line each.

---
System data:
{json.dumps(scan_data, ensure_ascii=False, indent=2)}
"""

        print("\n  Querying Gemini AI...", flush=True)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
        )
        return response.text
    except Exception as e:
        print(f"\n[ERROR] Error querying Gemini AI: {e}")
        print("Saving the report without AI recommendations.")
        return "Could not retrieve Gemini AI recommendations due to an API error or quota limit exceeded."


# ── HTML Report (Stitch Design System: Lumina Disk Intelligence) ──────────────

def _esc(s: str) -> str:
    """Escape a string for safe HTML insertion."""
    return _html.escape(str(s))


def _donut_svg(pct: float, disk_label: str) -> str:
    """Return an animated SVG donut ring for the given usage percentage."""
    r = 54
    cx = cy = 64
    circumference = 2 * 3.14159265 * r
    # Colour thresholds matching the Stitch design system semantic colours
    if pct >= 80:
        colour = "#f87171"   # rose / danger
        glow   = "rgba(248,113,113,0.4)"
    elif pct >= 60:
        colour = "#fbbf24"   # amber / warning
        glow   = "rgba(251,191,36,0.4)"
    else:
        colour = "#4edea3"   # emerald / tertiary (healthy)
        glow   = "rgba(78,222,163,0.4)"

    dash_target = circumference * pct / 100
    uid = disk_label.replace("\\", "").replace(":", "").replace("/", "")
    return f"""
<svg width="128" height="128" viewBox="0 0 128 128" class="donut-svg" data-target="{dash_target:.2f}" data-circ="{circumference:.2f}" id="donut-{uid}">
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="12"/>
  <circle class="donut-ring" cx="{cx}" cy="{cy}" r="{r}" fill="none"
    stroke="{colour}" stroke-width="12" stroke-linecap="round"
    stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{circumference:.2f}"
    transform="rotate(-90 {cx} {cy})"
    style="filter: drop-shadow(0 0 6px {glow});"/>
  <text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central"
    fill="{colour}" font-family="Inter,sans-serif" font-size="18" font-weight="700">{pct:.0f}%</text>
</svg>"""


def _table_rows(items: list, key_path: str = "path", key_size: str = "size",
               amber: bool = False) -> str:
    if not items:
        return '<tr><td colspan="3" class="px-4 py-3 text-center text-on-surface-variant text-sm">No data</td></tr>'
    rows = []
    for i, item in enumerate(items, 1):
        bg = "background:rgba(251,191,36,0.07);" if amber else ""
        rows.append(
            f'<tr class="data-table-row border-b border-white/5" style="{bg}">'
            f'<td class="px-4 py-2 text-on-surface-variant font-mono text-xs w-8">{i}</td>'
            f'<td class="px-4 py-2 font-mono text-xs text-on-surface truncate max-w-lg" title="{_esc(item[key_path])}">{_esc(item[key_path])}</td>'
            f'<td class="px-4 py-2 font-mono text-xs text-right whitespace-nowrap" style="color:#adc6ff">{_esc(item[key_size])}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def _bar_chart(ext_dict: dict) -> str:
    if not ext_dict:
        return '<p class="text-on-surface-variant text-sm">No data</p>'
    # Parse human sizes back to a comparable float for bar widths
    def _parse_size(s: str) -> float:
        parts = s.split()
        try:
            val = float(parts[0])
            unit = parts[1] if len(parts) > 1 else "B"
            mult = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}.get(unit, 1)
            return val * mult
        except Exception:
            return 0

    items = list(ext_dict.items())
    max_val = max((_parse_size(v) for _, v in items), default=1) or 1
    gradients = [
        "linear-gradient(90deg,#3b82f6,#8b5cf6)",
        "linear-gradient(90deg,#8b5cf6,#ec4899)",
        "linear-gradient(90deg,#06b6d4,#3b82f6)",
        "linear-gradient(90deg,#10b981,#3b82f6)",
        "linear-gradient(90deg,#f59e0b,#ef4444)",
    ]
    bars = []
    for idx, (ext, sz) in enumerate(items):
        width_pct = min(100, _parse_size(sz) / max_val * 100)
        grad = gradients[idx % len(gradients)]
        bars.append(
            f'<div class="flex items-center gap-3 py-1">'
            f'<span class="font-mono text-xs text-on-surface-variant w-24 text-right shrink-0">{_esc(ext)}</span>'
            f'<div class="flex-1 h-3 rounded-full overflow-hidden" style="background:rgba(255,255,255,0.06)">'
            f'<div class="bar-chart-fill h-full rounded-full" style="background:{grad};width:0" data-width="{width_pct:.1f}%"></div>'
            f'</div>'
            f'<span class="font-mono text-xs w-20 text-right shrink-0" style="color:#adc6ff">{_esc(sz)}</span>'
            f'</div>'
        )
    return "\n".join(bars)


def _disk_section(disk: dict) -> str:
    """Render one full per-disk collapsible section."""
    label = _esc(disk['root'])
    uid   = disk['root'].replace("\\", "").replace(":", "").replace("/", "")
    donut = _donut_svg(disk['use_pct'], disk['root'])
    pct   = disk['use_pct']
    if pct >= 80:
        pct_color = "#f87171"
    elif pct >= 60:
        pct_color = "#fbbf24"
    else:
        pct_color = "#4edea3"

    folders_html   = _table_rows(disk.get("large_folders", []))
    files_html     = _table_rows(disk.get("large_files", []))
    temp_html      = _table_rows(disk.get("temp_folders", []), amber=True)
    ext_html       = _bar_chart(disk.get("by_extension", {}))

    return f"""
<!-- ═══ DRIVE {label} ═══ -->
<section class="fade-in-up delay-200 glass-card rounded-xl overflow-hidden">
  <!-- Disk header -->
  <div class="flex items-center justify-between px-6 py-4 border-b border-white/10 cursor-pointer"
       onclick="toggleSection('{uid}')" id="hdr-{uid}">
    <div class="flex items-center gap-4">
      <span class="material-symbols-outlined" style="color:#adc6ff;font-size:28px">hard_drive_2</span>
      <div>
        <h2 class="font-mono font-bold text-xl text-on-surface">{label}</h2>
        <div class="flex gap-4 mt-1">
          <span class="text-xs font-mono text-on-surface-variant">Total: <b class="text-on-surface">{disk['total_gb']} GB</b></span>
          <span class="text-xs font-mono text-on-surface-variant">Used: <b style="color:{pct_color}">{disk['used_gb']} GB ({pct}%)</b></span>
          <span class="text-xs font-mono text-on-surface-variant">Free: <b class="text-on-surface">{disk['free_gb']} GB</b></span>
        </div>
      </div>
    </div>
    <div class="flex items-center gap-4">
      {donut}
      <span class="material-symbols-outlined transition-transform duration-300 text-on-surface-variant" id="arrow-{uid}">expand_more</span>
    </div>
  </div>

  <!-- Disk body -->
  <div id="body-{uid}" class="divide-y divide-white/5">

    <!-- Large folders -->
    <details open class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-white/5 transition">
        <span class="text-lg">📁</span>
        <span class="font-semibold text-on-surface">Largest Folders</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left text-on-surface-variant text-xs uppercase tracking-wider border-b border-white/10">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">Path</th><th class="px-4 py-2 text-right">Size</th>
          </tr></thead>
          <tbody>{folders_html}</tbody>
        </table>
      </div>
    </details>

    <!-- Large files -->
    <details class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-white/5 transition">
        <span class="text-lg">📄</span>
        <span class="font-semibold text-on-surface">Largest Files (≥50 MB)</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left text-on-surface-variant text-xs uppercase tracking-wider border-b border-white/10">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">Path</th><th class="px-4 py-2 text-right">Size</th>
          </tr></thead>
          <tbody>{files_html}</tbody>
        </table>
      </div>
    </details>

    <!-- Temp/cache -->
    <details class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-white/5 transition">
        <span class="text-lg">🗑️</span>
        <span class="font-semibold text-on-surface">Detected Temp/Cache Folders</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left text-on-surface-variant text-xs uppercase tracking-wider border-b border-white/10">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">Path</th><th class="px-4 py-2 text-right">Size</th>
          </tr></thead>
          <tbody>{temp_html}</tbody>
        </table>
      </div>
    </details>

    <!-- By extension -->
    <details class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-white/5 transition">
        <span class="text-lg">📊</span>
        <span class="font-semibold text-on-surface">Space by Extension (Top 15)</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="px-6 py-4">{ext_html}</div>
    </details>

  </div><!-- /body -->
</section>"""


def _format_recommendations(text: str) -> str:
    """
    Structured markdown-to-HTML renderer tailored to the Gemini prompt format:
      ## Disk Status            → chip row
      ## Top 5 …                → section heading
      ### N. Title              → numbered action card
      - **Key:** value          → key-value rows inside a card
      ## Preventive Tips        → tips list
      ---                       → ignored divider
    """
    import re

    # ── inline formatting helpers ──────────────────────────────────────────────
    def _inline(s: str) -> str:
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         s)
        # Replace safety emoji text with coloured badges
        s = s.replace('✅ Safe',          '<span class="safety-badge safe">✅ Safe</span>')
        s = s.replace('⚠️ With caution', '<span class="safety-badge warn">⚠️ With Caution</span>')
        s = s.replace('🔴 Manual review', '<span class="safety-badge danger">🔴 Manual Review</span>')
        return s

    # ── parse the text into a list of (kind, content) tokens ──────────────────
    tokens: list[tuple[str, str]] = []
    for raw in text.split('\n'):
        line = raw.strip()
        if not line or line == '---':
            continue
        if re.match(r'^##\s', line):                      # H2 section
            tokens.append(('h2', re.sub(r'^##\s*', '', line)))
        elif re.match(r'^###\s', line):                   # H3 action card title
            tokens.append(('h3', re.sub(r'^###\s*', '', line)))
        elif re.match(r'^[-*]\s', line):                  # bullet
            tokens.append(('li', line[2:]))
        else:                                              # plain paragraph
            tokens.append(('p', line))

    # ── render tokens into HTML ────────────────────────────────────────────────
    out: list[str] = []
    i = 0
    in_action_card = False   # track whether we're inside a ### card
    in_tips_list   = False   # track tips bullet list
    current_section = ''

    def _close_card():
        nonlocal in_action_card
        if in_action_card:
            out.append('</dl></div>')
            in_action_card = False

    def _close_tips():
        nonlocal in_tips_list
        if in_tips_list:
            out.append('</ul>')
            in_tips_list = False

    while i < len(tokens):
        kind, content = tokens[i]

        if kind == 'h2':
            _close_card()
            _close_tips()
            current_section = content.lower()

            if 'status' in current_section or 'estado' in current_section:
                # Collect the disk status lines that follow as chip rows
                out.append('<div class="mb-6">')
                out.append(f'<h2 class="rec-h2">{_inline(content)}</h2>')
                out.append('<div class="disk-chips">')
                i += 1
                while i < len(tokens) and tokens[i][0] == 'p':
                    chip_text = _inline(tokens[i][1])
                    # colour chip border based on status keyword
                    cl = 'chip-ok'
                    if 'attention' in tokens[i][1].lower() or 'atenci' in tokens[i][1].lower() or 'warn' in tokens[i][1].lower():
                        cl = 'chip-warn'
                    elif 'critical' in tokens[i][1].lower() or 'crít' in tokens[i][1].lower() or 'crit' in tokens[i][1].lower():
                        cl = 'chip-danger'
                    out.append(f'<div class="disk-chip {cl}">{chip_text}</div>')
                    i += 1
                out.append('</div></div>')
                continue
            else:
                # Generic H2 (Top 5 / Tips)
                out.append(f'<h2 class="rec-h2 mt-6">{_inline(content)}</h2>')

        elif kind == 'h3':
            _close_card()
            _close_tips()
            # Extract leading number if present: "1. Title"
            m = re.match(r'^(\d+)\.\s*(.*)', content)
            if m:
                num, title = m.group(1), m.group(2)
            else:
                num, title = '', content
            num_html = f'<span class="action-num">{num}</span>' if num else ''
            out.append(
                f'<div class="action-card">'
                f'<div class="action-title">{num_html}<span>{_inline(title)}</span></div>'
                f'<dl class="action-body">'
            )
            in_action_card = True

        elif kind == 'li':
            if in_action_card:
                # Key-value bullet inside an action card: "**Key:** value"
                kv = re.match(r'\*\*(.+?)\*\*[:\s]+(.*)', content)
                if kv:
                    key, val = kv.group(1), _inline(kv.group(2))
                    out.append(f'<div class="action-row"><dt>{key}</dt><dd>{val}</dd></div>')
                else:
                    out.append(f'<div class="action-row"><dd>{_inline(content)}</dd></div>')
            else:
                # Tips or generic bullets
                _close_card()
                if not in_tips_list:
                    out.append('<ul class="tips-list">')
                    in_tips_list = True
                out.append(f'<li>{_inline(content)}</li>')

        elif kind == 'p':
            _close_card()
            _close_tips()
            out.append(f'<p class="rec-p">{_inline(content)}</p>')

        i += 1

    _close_card()
    _close_tips()

    # ── Inject scoped styles ───────────────────────────────────────────────────
    styles = """
<style>
.rec-h2 {
  font-size:1rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
  color:#adc6ff; margin-bottom:.75rem; padding-bottom:.4rem;
  border-bottom:1px solid rgba(173,198,255,.15);
}
.disk-chips { display:flex; flex-wrap:wrap; gap:.5rem; }
.disk-chip {
  font-family:'JetBrains Mono',monospace; font-size:.75rem;
  padding:.35rem .75rem; border-radius:9999px;
  background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.1);
  color:#dfe2eb;
}
.chip-ok     { border-color:rgba(78,222,163,.4);  color:#4edea3; }
.chip-warn   { border-color:rgba(251,191,36,.4);  color:#fbbf24; }
.chip-danger { border-color:rgba(248,113,113,.4); color:#f87171; }

.action-card {
  background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07);
  border-radius:.75rem; padding:1rem 1.25rem; margin-bottom:.75rem;
}
.action-title {
  display:flex; align-items:center; gap:.6rem;
  font-weight:600; font-size:.95rem; color:#dfe2eb; margin-bottom:.6rem;
}
.action-num {
  display:inline-flex; align-items:center; justify-content:center;
  width:1.5rem; height:1.5rem; border-radius:9999px; font-size:.75rem;
  font-weight:700; flex-shrink:0;
  background:linear-gradient(135deg,#3b82f6,#8b5cf6); color:#fff;
}
.action-body { display:flex; flex-direction:column; gap:.35rem; }
.action-row { display:flex; gap:.5rem; align-items:baseline; font-size:.8rem; }
.action-row dt {
  font-family:'JetBrains Mono',monospace; font-weight:600;
  color:#8c909f; white-space:nowrap; min-width:4.5rem;
}
.action-row dd { color:#c2c6d6; margin:0; }

.safety-badge {
  display:inline-block; font-size:.72rem; font-weight:600;
  padding:.1rem .5rem; border-radius:9999px; white-space:nowrap;
}
.safety-badge.safe   { background:rgba(78,222,163,.12); color:#4edea3; }
.safety-badge.warn   { background:rgba(251,191,36,.12);  color:#fbbf24; }
.safety-badge.danger { background:rgba(248,113,113,.12); color:#f87171; }

.tips-list {
  list-style:none; padding:0; display:flex; flex-direction:column; gap:.4rem;
}
.tips-list li {
  font-size:.82rem; color:#c2c6d6; padding:.35rem .75rem;
  border-left:2px solid rgba(173,198,255,.3);
}
.rec-p { font-size:.85rem; color:#8c909f; margin:.25rem 0; }
</style>
"""
    return styles + '\n'.join(out)


def save_report(scan_data: dict, recommendations: str, output_path: str) -> str:
    """Generates a premium HTML report and saves it to output_path."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hostname = _esc(scan_data.get("sistema", "—"))

    # ── Summary cards (one per disk) ───────────────────────────────────────────
    summary_cards = []
    for disk in scan_data["discos"]:
        pct = disk['use_pct']
        if pct >= 80:
            pct_color = "#f87171"
        elif pct >= 60:
            pct_color = "#fbbf24"
        else:
            pct_color = "#4edea3"
        donut = _donut_svg(pct, disk['root'])
        summary_cards.append(f"""
<div class="glass-card rounded-xl p-6 flex flex-col items-center gap-3 hover:scale-105 transition-transform duration-300 fade-in-up">
  <span class="font-mono font-bold text-2xl" style="color:#adc6ff">{_esc(disk['root'])}</span>
  {donut}
  <div class="flex gap-2 flex-wrap justify-center mt-1">
    <span class="px-3 py-1 rounded-full text-xs font-mono" style="background:rgba(173,198,255,0.1);color:#adc6ff">Total: {disk['total_gb']} GB</span>
    <span class="px-3 py-1 rounded-full text-xs font-mono" style="background:rgba(255,255,255,0.05);color:{pct_color}">Used: {disk['used_gb']} GB</span>
    <span class="px-3 py-1 rounded-full text-xs font-mono" style="background:rgba(78,222,163,0.1);color:#4edea3">Free: {disk['free_gb']} GB</span>
  </div>
</div>""")

    # ── Per-disk detail sections ───────────────────────────────────────────────
    disk_sections = "\n".join(_disk_section(d) for d in scan_data["discos"])

    # ── AI recommendations ─────────────────────────────────────────────────────
    ai_html = _format_recommendations(recommendations)

    # ── Assemble full page ─────────────────────────────────────────────────────
    page = f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>AI Disk Analyzer — Report</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script id="tailwind-config">
tailwind.config = {{
  darkMode: "class",
  theme: {{
    extend: {{
      colors: {{
        "surface":               "#10141a",
        "surface-container":     "#1c2026",
        "surface-container-high":"#262a31",
        "surface-variant":       "#31353c",
        "on-surface":            "#dfe2eb",
        "on-surface-variant":    "#c2c6d6",
        "outline-variant":       "#424754",
        "primary":               "#adc6ff",
        "primary-container":     "#4d8eff",
        "secondary":             "#d0bcff",
        "tertiary":              "#4edea3",
        "background":            "#0d1117"
      }},
      fontFamily: {{
        "mono": ["JetBrains Mono", "monospace"],
        "sans": ["Inter", "system-ui", "sans-serif"]
      }}
    }}
  }}
}};
</script>
<style>
  * {{ -webkit-font-smoothing: antialiased; }}
  body {{ background-color:#0d1117; color:#dfe2eb; font-family:Inter,sans-serif; }}

  .glass-card {{
    background: rgba(30,41,59,0.5);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(255,255,255,0.08);
  }}

  /* Shimmer border for AI card */
  .ai-card {{ position:relative; border-radius:0.75rem; z-index:0; }}
  .ai-card::before {{
    content:"";
    position:absolute; inset:-1px;
    border-radius:inherit; padding:1px;
    background: linear-gradient(90deg,#3b82f6,#8b5cf6,#ec4899,#8b5cf6,#3b82f6);
    background-size: 300% auto;
    -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
    -webkit-mask-composite: xor;
    mask-composite: exclude;
    animation: shimmer 4s linear infinite;
    z-index:-1;
  }}
  @keyframes shimmer {{ to {{ background-position: 300% 50%; }} }}

  /* Fade-in entrance */
  .fade-in-up {{
    opacity:0;
    transform:translateY(20px);
    animation: fadeInUp 0.6s cubic-bezier(0.16,1,0.3,1) forwards;
  }}
  @keyframes fadeInUp {{ to {{ opacity:1; transform:translateY(0); }} }}
  .delay-100 {{ animation-delay:100ms; }}
  .delay-200 {{ animation-delay:200ms; }}
  .delay-300 {{ animation-delay:300ms; }}
  .delay-400 {{ animation-delay:400ms; }}

  /* Donut ring animation triggered by JS */
  .donut-ring {{ transition: stroke-dashoffset 1.5s cubic-bezier(0.4,0,0.2,1); }}

  /* Bar chart animated fill */
  .bar-chart-fill {{ transition: width 1.2s cubic-bezier(0.4,0,0.2,1); }}

  /* Table rows */
  .data-table-row {{ transition: background-color 150ms ease; }}
  .data-table-row:hover {{ background: rgba(255,255,255,0.04); }}

  /* Pulsing dot */
  @keyframes ping {{ 75%,100%{{ transform:scale(2); opacity:0; }} }}
  .ping {{ animation: ping 1.5s cubic-bezier(0,0,0.2,1) infinite; }}

  /* Scrollbar */
  ::-webkit-scrollbar {{ width:6px; height:6px; }}
  ::-webkit-scrollbar-track {{ background:transparent; }}
  ::-webkit-scrollbar-thumb {{ background:rgba(255,255,255,0.15); border-radius:3px; }}
</style>
</head>
<body class="min-h-screen">

<!-- ══════════════════ HEADER ══════════════════ -->
<header class="sticky top-0 z-50 flex justify-between items-center px-8 py-3"
        style="background:rgba(13,17,23,0.85);backdrop-filter:blur(24px);border-bottom:1px solid rgba(255,255,255,0.07)">
  <div class="flex items-center gap-3">
    <span class="material-symbols-outlined" style="color:#adc6ff;font-size:30px">hard_drive</span>
    <div>
      <h1 class="font-bold text-xl text-white leading-tight">AI Disk Analyzer</h1>
      <p class="font-mono text-xs" style="color:#8c909f">Report generated on {ts}</p>
    </div>
  </div>
  <div class="flex items-center gap-4">
    <div class="flex items-center gap-2 px-3 py-1 rounded-full" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1)">
      <span class="material-symbols-outlined text-xs" style="color:#8c909f;font-size:14px">computer</span>
      <span class="font-mono text-xs font-semibold text-white">{hostname}</span>
    </div>
    <div class="flex items-center gap-2">
      <span class="relative flex h-2.5 w-2.5">
        <span class="ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background:#4edea3"></span>
        <span class="relative inline-flex rounded-full h-2.5 w-2.5" style="background:#4edea3"></span>
      </span>
      <span class="text-xs font-mono" style="color:#4edea3">Analysis complete</span>
    </div>
  </div>
</header>

<!-- ══════════════════ MAIN ══════════════════ -->
<main class="max-w-7xl mx-auto px-6 pb-16 pt-8 space-y-8">

  <!-- Summary row -->
  <div class="grid gap-6" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr))">
    {''.join(summary_cards)}
  </div>

  <!-- Per-disk sections -->
  {disk_sections}

  <!-- ── AI Recommendations ── -->
  <div class="ai-card glass-card p-8 fade-in-up delay-400" style="background:rgba(20,20,35,0.7)">
    <div class="flex items-center gap-3 mb-6">
      <span class="text-3xl">✨</span>
      <h2 class="text-xl font-bold" style="color:#d0bcff">Gemini AI Recommendations</h2>
    </div>
    <div class="space-y-1 leading-relaxed">{ai_html}</div>
  </div>

</main>

<!-- ══════════════════ FOOTER ══════════════════ -->
<footer class="text-center py-6" style="border-top:1px solid rgba(255,255,255,0.05)">
  <p class="font-mono text-xs" style="color:#424754">Generated by disk_analyzer.py with Gemini AI</p>
</footer>

<script>
// ── Animate donut rings on load ──
document.querySelectorAll('.donut-svg').forEach(svg => {{
  const ring = svg.querySelector('.donut-ring');
  const target = parseFloat(svg.dataset.target);
  const circ   = parseFloat(svg.dataset.circ);
  requestAnimationFrame(() => {{
    setTimeout(() => {{ ring.style.strokeDashoffset = (circ - target).toFixed(2); }}, 100);
  }});
}});

// ── Animate bar chart fills on load ──
document.querySelectorAll('.bar-chart-fill').forEach(bar => {{
  requestAnimationFrame(() => {{
    setTimeout(() => {{ bar.style.width = bar.dataset.width; }}, 200);
  }});
}});

// ── Toggle disk section body ──
function toggleSection(uid) {{
  const body  = document.getElementById('body-' + uid);
  const arrow = document.getElementById('arrow-' + uid);
  const hidden = body.style.display === 'none';
  body.style.display  = hidden ? '' : 'none';
  arrow.style.transform = hidden ? '' : 'rotate(-90deg)';
}}
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return page


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  AI DISK ANALYZER")
    print("=" * 60)

    if API_KEY == "YOUR_API_KEY_HERE":
        print("\n[WARNING] Gemini API key is not configured.")
        print("  Edit the script and replace YOUR_API_KEY_HERE,")
        print("  or define the GEMINI_API_KEY environment variable.")
        print("  -> Get a free key at: https://aistudio.google.com/apikey\n")
        sys.exit(1)

    drives = get_all_drives()
    print(f"\nDrives found: {', '.join(drives)}")

    all_disks = []
    # Scan multiple drives in parallel when more than one drive exists
    if len(drives) > 1:
        with ThreadPoolExecutor(max_workers=len(drives)) as drive_executor:
            drive_futures = {drive_executor.submit(scan_drive, d): d for d in drives}
            for fut in as_completed(drive_futures):
                drive = drive_futures[fut]
                try:
                    all_disks.append(fut.result())
                except Exception as e:
                    print(f"  [!] Could not analyze {drive}: {e}")
        # Preserve original drive order in the report
        drive_order = {d: i for i, d in enumerate(drives)}
        all_disks.sort(key=lambda x: drive_order.get(x["root"], 999))
    else:
        for drive in drives:
            try:
                disk_data = scan_drive(drive)
                all_disks.append(disk_data)
            except Exception as e:
                print(f"  [!] Could not analyze {drive}: {e}")

    if not all_disks:
        print("\n[ERROR] No drives could be analyzed.")
        sys.exit(1)

    scan_data = {
        "system": platform.node(),
        "analysis_date": datetime.now(timezone.utc).isoformat(),
        "discos": all_disks,
    }

    recommendations = ask_gemini(scan_data)

    # Save HTML report and open in the browser
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "disk-analyzer-reports")
    os.makedirs(reports_dir, exist_ok=True)
    output_file = os.path.join(
        reports_dir,
        f"disk_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    save_report(scan_data, recommendations, output_file)
    print(f"\n[OK] Report saved to: {output_file}")
    webbrowser.open(f"file:///{output_file.replace(os.sep, '/')}")
    print("[OK] Report opened in browser.")


if __name__ == "__main__":
    main()
