import os
import sys
import time
import shutil
import platform
import stat as _stat
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import config

# Mode mask to detect symbolic links without importing stat repeatedly
_S_ISLNK = _stat.S_ISLNK


def get_app_context(path: str) -> str | None:
    path_lower = path.lower()
    for key, desc in config.KNOWN_APP_PATTERNS.items():
        if key in path_lower:
            return desc
    return None


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
    try:
        parts = Path(path_str).parts
    except Exception:
        parts = path_str.replace("\\", "/").split("/")
    for part in parts:
        if part.lower() in config._SKIP_PATHS_LOWER:
            return True
    return False


def _scan_subtree(
    root_path: str,
    drive_root: str,
    # Shared accumulators protected by lock
    top_level_sizes: defaultdict,
    temp_folder_sizes: defaultdict,
    big_files: list,
    ext_sizes: defaultdict,
    all_folder_sizes: defaultdict,
    parent_to_children: dict,
    lock: Lock,
    executor: ThreadPoolExecutor,
    futures: list,
    progress_data: dict,
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
            if any(k in part_lower for k in config._TEMP_KEYWORDS):
                temp_ancestor = os.path.join(drive_root, *rel_parts[: i + 1])
                break

    # Accumulate file sizes from this directory; batch them into the shared
    # structures once (single lock acquisition per directory visit).
    local_top_delta = 0
    local_temp_delta = 0
    local_files_size = 0
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

            local_files_size += sz
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
    if local_top_delta or local_temp_delta or local_big or local_ext or local_files_size or subdirs_to_visit:
        with lock:
            if top_level and local_top_delta:
                top_level_sizes[top_level] += local_top_delta
            if temp_ancestor and local_temp_delta:
                temp_folder_sizes[temp_ancestor] += local_temp_delta
            if local_big:
                big_files.extend(local_big)
            for ext, sz in local_ext.items():
                ext_sizes[ext] += sz

            if subdirs_to_visit:
                parent_to_children[root_path] = subdirs_to_visit

            if local_files_size > 0:
                path = root_path
                while True:
                    all_folder_sizes[path] += local_files_size
                    parent = os.path.dirname(path)
                    if parent == path or path.lower() == drive_root.lower():
                        break
                    path = parent

    # Always increment folders scanned and update progress
    with lock:
        progress_data["folders"] += 1
        progress_data["bytes"] += local_files_size
        
        now = time.time()
        if now - progress_data["last_update"] >= 0.1:
            progress_data["last_update"] = now
            sys.stdout.write(
                f"\r  Scanning... [{progress_data['folders']:,} folders | "
                f"{bytes_to_human(progress_data['bytes'])} scanned]"
            )
            sys.stdout.flush()

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
            all_folder_sizes,
            parent_to_children,
            lock,
            executor,
            futures,
            progress_data,
        )
        with lock:
            futures.append(fut)


def scan_drive(drive_root: str) -> dict:
    """Scans a drive and collects metrics in a single pass."""
    print(f"\n  Scanning {drive_root} ...", end="", flush=True)

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
    root_files_sizes: list[tuple[str, int]] = []
    all_folder_sizes: defaultdict[str, int] = defaultdict(int)
    parent_to_children: dict[str, list[str]] = {}
    lock = Lock()

    progress_data = {
        "folders": 0,
        "bytes": 0,
        "last_update": 0.0,
    }

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

        top_level_subdirs = []
        for entry in top_entries:
            if entry.is_dir(follow_symlinks=False):
                if not should_skip(entry.path):
                    top_level_subdirs.append(entry.path)
                    fut = executor.submit(
                        _scan_subtree,
                        entry.path,
                        drive_root,
                        top_level_sizes,
                        temp_folder_sizes,
                        big_files,
                        ext_sizes,
                        all_folder_sizes,
                        parent_to_children,
                        lock,
                        executor,
                        futures,
                        progress_data,
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
                                root_files_sizes.append((entry.path, sz))
                    except (PermissionError, OSError):
                        pass

        with lock:
            parent_to_children[drive_root] = top_level_subdirs

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

    # Clear progress line and print completion
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.write(f"  Scanning {drive_root} ... [OK]\n")
    sys.stdout.flush()

    # Select hotspots (largest folders at any depth that don't have a single dominant subdirectory)
    all_folders = sorted(all_folder_sizes.items(), key=lambda x: x[1], reverse=True)
    hotspots = []
    for path, size in all_folders:
        if size < config.MIN_FOLDER_SIZE_MB * 1e6:
            continue
        # Avoid including the drive root itself
        if path.lower() == drive_root.lower():
            continue
            
        # If any subdirectory of `path` is > 80% of `size`, it is the dominant child.
        # This makes `path` redundant.
        has_dominant_child = False
        subdirs = parent_to_children.get(path, [])
        for sd in subdirs:
            sd_size = all_folder_sizes.get(sd, 0)
            if sd_size > 0.8 * size:
                has_dominant_child = True
                break
                
        if not has_dominant_child:
            hotspots.append({"path": path, "size": bytes_to_human(size)})
            
    result["large_folders"] = hotspots[:config.TOP_N_FOLDERS]

    big_files.sort(reverse=True)
    result["large_files"] = [
        {"path": p, "size": bytes_to_human(s)}
        for s, p in big_files[:config.TOP_N_FILES]
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

    # Enrich folder results with App Context description
    for folder in result["large_folders"]:
        ctx = get_app_context(folder["path"])
        if ctx:
            folder["app_context"] = ctx

    for folder in result["temp_folders"]:
        ctx = get_app_context(folder["path"])
        if ctx:
            folder["app_context"] = ctx

    # Build hierarchical sunburst data
    # Map big files by parent directory for O(1) lookup
    big_files_by_parent = defaultdict(list)
    for sz, p in big_files:
        parent = os.path.dirname(p)
        big_files_by_parent[parent].append((p, sz))

    def build_tree(current_path, min_size=50 * 1024 * 1024):
        path_size = all_folder_sizes.get(current_path, 0)
        is_root = current_path.lower() == drive_root.lower()
        if is_root:
            path_size += sum(sz for _, sz in root_files_sizes)

        if path_size < min_size:
            return None

        name = current_path if is_root else os.path.basename(current_path)
        if not name:
            name = current_path

        children = []
        included_size = 0

        # Subdirectories
        subdirs = parent_to_children.get(current_path, [])
        for sd in subdirs:
            sd_size = all_folder_sizes.get(sd, 0)
            if sd_size >= min_size:
                child_node = build_tree(sd, min_size)
                if child_node:
                    children.append(child_node)
                    included_size += sd_size

        # Big files directly here
        files_here = big_files_by_parent.get(current_path, [])
        for fp, sz in files_here:
            children.append({
                "name": os.path.basename(fp),
                "value": sz,
                "path": fp,
                "is_file": True
            })
            included_size += sz

        # Root files
        if is_root:
            files_here_set = {f[0].lower() for f in files_here}
            for fp, sz in root_files_sizes:
                if fp.lower() not in files_here_set:
                    children.append({
                        "name": os.path.basename(fp),
                        "value": sz,
                        "path": fp,
                        "is_file": True
                    })
                    included_size += sz

        # Add "Other" node for remainder
        remaining = path_size - included_size
        if remaining > 1 * 1024 * 1024:  # > 1 MB
            children.append({
                "name": "[Other files/folders]",
                "value": remaining,
                "path": os.path.join(current_path, "[other]"),
                "is_other": True
            })

        node = {
            "name": name,
            "path": current_path,
            "value": path_size
        }
        if children:
            node["children"] = children
        return node

    # We set a threshold of 50MB to keep the DOM footprint reasonable
    sunburst_tree = build_tree(drive_root, min_size=50 * 1024 * 1024)
    if not sunburst_tree:
        sunburst_tree = {
            "name": drive_root,
            "path": drive_root,
            "value": sum(sz for _, sz in root_files_sizes)
        }

    # Add System Skipped if there's significant space used but not scanned
    total_used_bytes = int(usage.used)
    total_scanned_bytes = sum(top_level_sizes.values()) + sum(sz for _, sz in root_files_sizes)
    system_skipped_bytes = max(0, total_used_bytes - total_scanned_bytes)
    if system_skipped_bytes > 50 * 1024 * 1024:  # > 50 MB
        if "children" not in sunburst_tree:
            sunburst_tree["children"] = []
        sunburst_tree["children"].append({
            "name": "System & Skipped",
            "value": system_skipped_bytes,
            "path": os.path.join(drive_root, "System & Skipped"),
            "is_system": True
        })
        sunburst_tree["value"] += system_skipped_bytes

    result["sunburst_data"] = sunburst_tree

    return result
