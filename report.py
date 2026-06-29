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


def _table_rows(items: list, key_path='path', key_size='size', amber=False) -> str:
    if not items:
        return '<tr><td colspan="3" class="px-4 py-3 text-center text-on-surface-variant text-sm">No data</td></tr>'
    rows = []
    for i, item in enumerate(items, 1):
        bg = "background:rgba(251,191,36,0.07);" if amber else ""
        rows.append(
            f'<tr class="data-table-row border-b border-outline-variant/10" style="{bg}">'
            f'<td class="px-4 py-2 text-on-surface-variant font-mono text-xs w-8">{i}</td>'
            f'<td class="px-4 py-2 font-mono text-xs text-on-surface truncate max-w-lg" title="{_esc(item[key_path])}">{_esc(item[key_path])}</td>'
            f'<td class="px-4 py-2 font-mono text-xs text-right whitespace-nowrap" style="color:var(--color-primary)">{_esc(item[key_size])}</td>'
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
            f'<div class="flex-1 h-3 rounded-full overflow-hidden bg-surface-container-high">'
            f'<div class="bar-chart-fill h-full rounded-full" style="background:{grad};width:0" data-width="{width_pct:.1f}%"></div>'
            f'</div>'
            f'<span class="font-mono text-xs w-20 text-right shrink-0" style="color:var(--color-primary)">{_esc(sz)}</span>'
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
  <div class="flex items-center justify-between px-6 py-4 border-b border-outline-variant/20 cursor-pointer"
       onclick="toggleSection('{uid}')" id="hdr-{uid}">
    <div class="flex items-center gap-4">
      <span class="material-symbols-outlined text-primary" style="font-size:28px">hard_drive_2</span>
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
  <div id="body-{uid}" class="divide-y divide-outline-variant/10">

    <!-- Visual Space Distribution (Sunburst) -->
    <details open class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-surface-variant/20 transition">
        <span class="text-lg">🍩</span>
        <span class="font-semibold text-on-surface">Visual Space Distribution (Sunburst)</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="px-6 py-6 bg-surface-container/10 flex flex-col md:flex-row items-center justify-around gap-6">
        <!-- Sunburst Chart container -->
        <div class="flex flex-col items-center w-full md:w-1/2">
          <!-- Breadcrumbs path indicator -->
          <div id="sunburst-breadcrumbs-{uid}" class="text-xs font-mono text-on-surface-variant/80 bg-surface-variant/30 px-3 py-1.5 rounded-full mb-4 w-full text-center overflow-x-auto whitespace-nowrap scrollbar-thin">
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
              <span id="sb-detail-type-{uid}" class="text-xs font-mono bg-surface-variant/20 px-2 py-0.5 rounded text-on-surface-variant inline-block">-</span>
            </div>
          </div>
        </div>
      </div>
    </details>

    <!-- Large folders -->
    <details open class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-surface-variant/20 transition">
        <span class="text-lg">📁</span>
        <span class="font-semibold text-on-surface">Largest Folders</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left text-on-surface-variant text-xs uppercase tracking-wider border-b border-outline-variant/20">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">Path</th><th class="px-4 py-2 text-right">Size</th>
          </tr></thead>
          <tbody>{folders_html}</tbody>
        </table>
      </div>
    </details>

    <!-- Large files -->
    <details class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-surface-variant/20 transition">
        <span class="text-lg">📄</span>
        <span class="font-semibold text-on-surface">Largest Files (≥50 MB)</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left text-on-surface-variant text-xs uppercase tracking-wider border-b border-outline-variant/20">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">Path</th><th class="px-4 py-2 text-right">Size</th>
          </tr></thead>
          <tbody>{files_html}</tbody>
        </table>
      </div>
    </details>

    <!-- Temp/cache -->
    <details class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-surface-variant/20 transition">
        <span class="text-lg">🗑️</span>
        <span class="font-semibold text-on-surface">Detected Temp/Cache Folders</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left text-on-surface-variant text-xs uppercase tracking-wider border-b border-outline-variant/20">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">Path</th><th class="px-4 py-2 text-right">Size</th>
          </tr></thead>
          <tbody>{temp_html}</tbody>
        </table>
      </div>
    </details>

    <!-- By extension -->
    <details class="group">
      <summary class="flex items-center gap-2 px-6 py-3 cursor-pointer hover:bg-surface-variant/20 transition">
        <span class="text-lg">📊</span>
        <span class="font-semibold text-on-surface">Space by Extension (Top 15)</span>
        <span class="ml-auto material-symbols-outlined text-on-surface-variant group-open:rotate-180 transition-transform">expand_more</span>
      </summary>
      <div class="px-6 py-4">{ext_html}</div>
    </details>

  </div><!-- /body -->
</section>"""


def _format_recommendations(recommendations) -> str:
    """
    Structured JSON-to-HTML or markdown-to-HTML fallback recommendations renderer.
    """
    # Inline formatting helper
    def _inline(s: str) -> str:
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         s)
        s = re.sub(r'`(.+?)`',       r'<code>\1</code>',     s)
        return s

    out = []

    # If recommendations is a string (e.g. error, fallback, or no-ai message)
    if isinstance(recommendations, str):
        out.append(f'<p class="rec-p text-on-surface-variant">{_esc(recommendations)}</p>')
    else:
        # Otherwise, parse it as a structured dictionary!
        # 1. Disk Status
        status_list = recommendations.get("disk_status", [])
        if status_list:
            out.append('<div class="mb-6">')
            out.append('<h2 class="rec-h2">Disk Status</h2>')
            out.append('<div class="disk-chips">')
            for item in status_list:
                lvl = item.get("level", "OK").upper()
                cl = "chip-ok"
                if "WARN" in lvl:
                    cl = "chip-warn"
                elif "CRIT" in lvl:
                    cl = "chip-danger"
                summary = _inline(_esc(item.get("summary", "")))
                out.append(f'<div class="disk-chip {cl}">{summary}</div>')
            out.append('</div></div>')

        # 2. Actions
        actions_list = recommendations.get("actions", [])
        if actions_list:
            out.append('<h2 class="rec-h2 mt-6">Top space saving actions</h2>')
            for idx, act in enumerate(actions_list, 1):
                safety = act.get("safety", "Safe")
                safety_badge = ""
                if "Safe" in safety:
                    safety_badge = '<span class="safety-badge safe">✅ Safe</span>'
                elif "Caution" in safety:
                    safety_badge = '<span class="safety-badge warn">⚠️ With Caution</span>'
                elif "Danger" in safety:
                    safety_badge = '<span class="safety-badge danger">🔴 Manual Review</span>'
                else:
                    safety_badge = f'<span class="safety-badge warn">{_esc(safety)}</span>'
                    
                title = _inline(_esc(act.get("title", "")))
                what = _inline(_esc(act.get("what", "")))
                why_how = _inline(_esc(act.get("why_how", "")))
                impact = _inline(_esc(act.get("impact", "")))
                
                out.append(
                    f'<div class="action-card">'
                    f'<div class="action-title"><span class="action-num">{idx}</span><span>{title}</span></div>'
                    f'<dl class="action-body">'
                    f'<div class="action-row"><dt>What</dt><dd>{what}</dd></div>'
                    f'<div class="action-row"><dt>Why/How</dt><dd>{why_how}</dd></div>'
                    f'<div class="action-row"><dt>Impact</dt><dd>{impact}</dd></div>'
                    f'<div class="action-row"><dt>Safety</dt><dd>{safety_badge}</dd></div>'
                    f'</dl></div>'
                )

        # 3. Preventive tips
        tips_list = recommendations.get("preventive_tips", [])
        if tips_list:
            out.append('<h2 class="rec-h2 mt-6">Preventive tips</h2>')
            out.append('<ul class="tips-list">')
            for tip in tips_list:
                out.append(f'<li>{_inline(_esc(tip))}</li>')
            out.append('</ul>')

    # Scoped styles
    styles = """
<style>
.rec-h2 {
  font-size:1rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
  color:var(--color-primary); margin-bottom:.75rem; padding-bottom:.4rem;
  border-bottom:1px solid var(--color-outline-variant);
}
.disk-chips { display:flex; flex-wrap:wrap; gap:.5rem; }
.disk-chip {
  font-family:'JetBrains Mono',monospace; font-size:.75rem;
  padding:.35rem .75rem; border-radius:9999px;
  background:var(--color-surface-container); border:1px solid var(--color-outline-variant);
  color:var(--color-on-surface);
}
.chip-ok     { border-color:rgba(78,222,163,.4);  color:#4edea3; }
.chip-warn   { border-color:rgba(251,191,36,.4);  color:#fbbf24; }
.chip-danger { border-color:rgba(248,113,113,.4); color:#f87171; }
 
.action-card {
  background:var(--color-surface-container); border:1px solid var(--color-outline-variant);
  border-radius:.75rem; padding:1rem 1.25rem; margin-bottom:.75rem;
}
.action-title {
  display:flex; align-items:center; gap:.6rem;
  font-weight:600; font-size:.95rem; color:var(--color-on-surface); margin-bottom:.6rem;
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
  color:var(--color-on-surface-variant); white-space:nowrap; min-width:4.5rem;
}
.action-row dd { color:var(--color-on-surface-variant); margin:0; }
code {
  font-family:'JetBrains Mono',monospace;
  background:var(--color-surface-container-high);
  padding:.15rem .3rem;
  border-radius:.25rem;
  color:var(--color-primary);
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
  font-size:.82rem; color:var(--color-on-surface-variant); padding:.35rem .75rem;
  border-left:2px solid var(--color-primary);
}
.rec-p { font-size:.85rem; color:var(--color-on-surface-variant); margin:.25rem 0; }
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
