import os
import sys
import json
import html as _html
from datetime import datetime, timezone
import re

import config
from scanner import bytes_to_human, get_app_context


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
    fill="{colour}" font-family="'Plus Jakarta Sans',sans-serif" font-size="18" font-weight="700">{pct:.0f}%</text>
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

    <!-- Visual Space Distribution (Sunburst) -->
    <details open class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-white/5 transition">
        <span class="text-lg">🍩</span>
        <span class="font-semibold text-on-surface">Visual Space Distribution (Sunburst)</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="px-6 py-6 bg-white/[0.01] flex flex-col md:flex-row items-center justify-around gap-6">
        <!-- Sunburst Chart container -->
        <div class="flex flex-col items-center w-full md:w-1/2">
          <!-- Breadcrumbs path indicator -->
          <div id="sunburst-breadcrumbs-{uid}" class="text-xs font-mono text-on-surface-variant/80 bg-white/5 px-3 py-1.5 rounded-full mb-4 w-full text-center overflow-x-auto whitespace-nowrap scrollbar-thin">
            Click a segment to drill down
          </div>
          <div id="sunburst-{uid}" class="w-full flex justify-center" style="min-height: 380px;"></div>
        </div>
        <!-- Detail Panel -->
        <div id="sunburst-details-{uid}" class="w-full md:w-1/3 glass-card rounded-xl p-6 flex flex-col justify-center min-h-[220px]">
          <h3 class="text-xs uppercase tracking-wider text-on-surface-variant font-semibold mb-2">Hovered Element</h3>
          <div class="space-y-3">
            <div>
              <span class="text-xs text-on-surface-variant/70 block">Name / Path</span>
              <span id="sb-detail-name-{uid}" class="font-semibold text-sm text-primary break-all">-</span>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <span class="text-xs text-on-surface-variant/70 block">Size</span>
                <span id="sb-detail-size-{uid}" class="font-mono text-base font-bold text-on-surface">-</span>
              </div>
              <div>
                <span class="text-xs text-on-surface-variant/70 block">Percentage of parent</span>
                <span id="sb-detail-pct-{uid}" class="font-mono text-base font-bold text-secondary">-</span>
              </div>
            </div>
            <div>
              <span class="text-xs text-on-surface-variant/70 block">Type</span>
              <span id="sb-detail-type-{uid}" class="text-xs font-mono bg-white/10 px-2 py-0.5 rounded text-on-surface-variant inline-block">-</span>
            </div>
          </div>
        </div>
      </div>
    </details>

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
    # ── inline formatting helpers ──────────────────────────────────────────────
    def _inline(s: str) -> str:
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         s)
        s = re.sub(r'`(.+?)`',       r'<code>\1</code>',     s)
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
        elif re.match(r'^###+\s', line):                  # H3+ action card title
            tokens.append(('h3', re.sub(r'^###+\s*', '', line)))
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
            # Extract leading number if present: "1. Title" or "#1. Title"
            m = re.match(r'^#?(\d+)[\.\:]?\s*(.*)', content)
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
            if in_action_card:
                # Inside action card, support both key-value text (e.g. "Path: C:\XboxGames") and general text
                kv = re.match(r'^([\w\s\/]+?)\s*:\s+(.*)', content)
                if kv:
                    key, val = kv.group(1), _inline(kv.group(2))
                    out.append(f'<div class="action-row"><dt>{key}</dt><dd>{val}</dd></div>')
                else:
                    out.append(f'<div class="action-row"><dd>{_inline(content)}</dd></div>')
            else:
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
code {
  font-family:'JetBrains Mono',monospace;
  background:rgba(255,255,255,.06);
  padding:.15rem .3rem;
  border-radius:.25rem;
  color:#adc6ff;
  font-size:.85em;
}
 
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

    discos_json = json.dumps(scan_data["discos"], ensure_ascii=False)

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

    # ── Load and format template ───────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "report_template.html")
    
    if not os.path.exists(template_path):
        print(f"\n[ERROR] Template file not found: {template_path}")
        print("Please ensure 'report_template.html' is in the same directory as 'disk_analyzer.py'.")
        sys.exit(1)

    with open(template_path, "r", encoding="utf-8") as tf:
        template_content = tf.read()

    page = template_content
    page = page.replace("{{TIMESTAMP}}", ts)
    page = page.replace("{{SUMMARY_CARDS}}", "".join(summary_cards))
    page = page.replace("{{DISK_SECTIONS}}", disk_sections)
    page = page.replace("{{AI_RECOMMENDATIONS}}", ai_html)
    page = page.replace("{{DISCOS_JSON}}", discos_json)

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return page


def clean_reports():
    """Deletes all generated HTML reports from the reports directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "disk-analyzer-reports")
    if not os.path.exists(reports_dir):
        print(f"\n[INFO] Reports directory does not exist: {reports_dir}")
        return

    try:
        files = [os.path.join(reports_dir, f) for f in os.listdir(reports_dir) if f.endswith(".html")]
        if not files:
            print(f"\n[INFO] No reports found to clean in: {reports_dir}")
            return

        print(f"\nFound {len(files)} reports to delete in {reports_dir}.")
        deleted_count = 0
        for f in files:
            try:
                os.remove(f)
                deleted_count += 1
            except Exception as e:
                print(f"  [!] Failed to delete {os.path.basename(f)}: {e}")

        print(f"[OK] Successfully deleted {deleted_count} reports.")
    except Exception as e:
        print(f"[ERROR] Failed to read reports directory: {e}")
