import os
import sys
import platform
import webbrowser
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import scanner
import ai
import report

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Disk Analyzer — Scan storage and get AI recommendations.")
    parser.add_argument("--path", "-p", help="Specific directory or drive root to scan (defaults to all drives).")
    parser.add_argument("--drives", "-d", nargs="+", help="Scan only these specific drives or paths (e.g. --drives C:\\ D:\\).")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI recommendations entirely.")
    parser.add_argument("--model", "-m", help="AI model to use ('gemini' or a local Ollama model name). Bypasses interactive selection.")
    parser.add_argument("--clean", action="store_true", help="Delete all generated HTML reports from reports directory and exit.")
    parser.add_argument("--export", choices=["json", "csv"], help="Export scan data to JSON or CSV format and save it in the reports directory.")
    args = parser.parse_args()

    if args.clean:
        report.clean_reports()
        sys.exit(0)

    print("=" * 60)
    print("  AI DISK ANALYZER")
    print("=" * 60)

    # Set model if specified
    if args.model:
        if args.model.upper() == "GEMINI":
            config.OLLAMA_MODEL = "GEMINI"
        else:
            config.OLLAMA_MODEL = args.model

    # Check if a model is available (either Ollama local or Gemini API)
    ollama_ok = False
    if not args.no_ai:
        try:
            model = ai.get_ollama_model()
            if model == "GEMINI":
                print("\n[INFO] User selected Gemini API. Bypassing local models.")
            else:
                print(f"\n[INFO] Found local Ollama model: {model}")
                ollama_ok = True
        except Exception as e:
            print(f"\n[INFO] Local Ollama not available or has no models: {e}")

        if not ollama_ok and config.API_KEY == "YOUR_API_KEY_HERE":
            print("\n[ERROR] No AI configuration found.")
            print("  - To use a local model, make sure Ollama is running and has at least one model downloaded.")
            print("  - To use Gemini, edit config.py to replace YOUR_API_KEY_HERE, or set the GEMINI_API_KEY environment variable.")
            sys.exit(1)

    if args.drives:
        drives = []
        for d in args.drives:
            if not os.path.exists(d):
                print(f"\n[ERROR] The specified drive/path does not exist: {d}")
                if platform.system() == "Windows":
                    print("  Note: Windows drive roots must include a colon and backslash (e.g., 'C:\\' instead of 'C').")
                else:
                    print("  Note: Specify absolute paths (e.g., '/' or '/home/user').")
                available = scanner.get_all_drives()
                if available:
                    print(f"  Available drives on this system: {', '.join(available)}")
                sys.exit(1)
            drives.append(os.path.abspath(d))
    elif args.path:
        if not os.path.exists(args.path):
            print(f"\n[ERROR] The specified path does not exist: {args.path}")
            if platform.system() == "Windows":
                print("  Note: Windows drive roots must include a colon and backslash (e.g., 'C:\\' instead of 'C').")
            else:
                print("  Note: Specify absolute paths (e.g., '/' or '/home/user').")
            available = scanner.get_all_drives()
            if available:
                print(f"  Available drives on this system: {', '.join(available)}")
            sys.exit(1)
        drives = [os.path.abspath(args.path)]
    else:
        drives = scanner.get_all_drives()
        
    print(f"\nPaths to scan: {', '.join(drives)}")

    all_disks = []
    # Scan multiple drives in parallel when more than one drive exists
    if len(drives) > 1:
        with ThreadPoolExecutor(max_workers=len(drives)) as drive_executor:
            drive_futures = {drive_executor.submit(scanner.scan_drive, d): d for d in drives}
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
                disk_data = scanner.scan_drive(drive)
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

    # Create a copy of scan_data without the huge sunburst_data for the AI
    ai_scan_data = {
        "system": scan_data["system"],
        "analysis_date": scan_data["analysis_date"],
        "discos": []
    }
    for disk in scan_data["discos"]:
        disk_copy = {k: v for k, v in disk.items() if k != "sunburst_data"}
        ai_scan_data["discos"].append(disk_copy)

    recommendations = {}
    if args.no_ai:
        recommendations = "AI Recommendations were disabled via command-line option."
    elif ollama_ok:
        try:
            raw_rec = ai.ask_ollama(ai_scan_data)
            recommendations = json.loads(raw_rec)
        except Exception as e:
            print(f"\n[WARNING] Failed to query Ollama or parse JSON: {e}")
            if config.API_KEY != "YOUR_API_KEY_HERE":
                print("Falling back to Gemini API...")
                try:
                    raw_rec = ai.ask_gemini(ai_scan_data)
                    recommendations = json.loads(raw_rec)
                except Exception as ge:
                    recommendations = f"Failed to retrieve or parse Gemini recommendations: {ge}"
            else:
                recommendations = "Could not retrieve local Ollama recommendations due to an error, and Gemini API is not configured."
    else:
        try:
            raw_rec = ai.ask_gemini(ai_scan_data)
            recommendations = json.loads(raw_rec)
        except Exception as e:
            recommendations = f"Failed to retrieve or parse Gemini recommendations: {e}"

    # Save HTML report and open in the browser
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "disk-analyzer-reports")
    os.makedirs(reports_dir, exist_ok=True)

    # Export data if requested
    if args.export:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if args.export == "json":
            import json
            output_json = os.path.join(reports_dir, f"disk_scan_{timestamp}.json")
            with open(output_json, "w", encoding="utf-8") as jf:
                json.dump(scan_data, jf, ensure_ascii=False, indent=2)
            print(f"[OK] Exported JSON data to: {output_json}")
        elif args.export == "csv":
            import csv
            output_csv = os.path.join(reports_dir, f"disk_scan_{timestamp}.csv")
            with open(output_csv, "w", newline="", encoding="utf-8") as cf:
                writer = csv.writer(cf)
                writer.writerow(["Drive", "Type", "Path", "Size"])
                for disk in scan_data["discos"]:
                    drive = disk["root"]
                    writer.writerow([drive, "Usage", "Used Space", f"{disk['used_gb']} GB / {disk['total_gb']} GB ({disk['use_pct']}%)"])
                    for folder in disk.get("large_folders", []):
                        writer.writerow([drive, "Folder", folder["path"], folder["size"]])
                    for file in disk.get("large_files", []):
                        writer.writerow([drive, "File", file["path"], file["size"]])
                    for temp in disk.get("temp_folders", []):
                        writer.writerow([drive, "Temp/Cache", temp["path"], temp["size"]])
                    for ext, sz in disk.get("by_extension", {}).items():
                        writer.writerow([drive, "Extension", ext, sz])
            print(f"[OK] Exported CSV data to: {output_csv}")

    output_file = os.path.join(
        reports_dir,
        f"disk_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    report.save_report(scan_data, recommendations, output_file)
    print(f"\n[OK] Report saved to: {output_file}")
    webbrowser.open(f"file:///{output_file.replace(os.sep, '/')}")
    print("[OK] Report opened in browser.")


if __name__ == "__main__":
    main()
