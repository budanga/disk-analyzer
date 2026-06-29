import os

# ── Configuration ──────────────────────────────────────────────────────────────
API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_API_KEY_HERE")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")  # If empty, auto-detects first available local model

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

# Known storage-heavy application folders and their cleanup context
KNOWN_APP_PATTERNS = {
    "docker": "Docker Desktop virtual machine images, volumes, and caches. Can be cleaned up using 'docker system prune' or 'docker builder prune'.",
    "spotify": "Spotify offline music cache. Can be safely deleted; Spotify will recreate it and re-download active songs if needed.",
    "chrome": "Google Chrome browser cache and user data. Can be safely cleared using Chrome settings or by deleting the Cache folder.",
    "npm-cache": "Node.js NPM global package cache. Can be cleared using the command 'npm cache clean --force'.",
    "node_modules": "NodeJS project dependencies folder. Safe to delete (can be reinstalled with 'npm install'). Best cleaned using the 'npkill' CLI tool.",
    "gradle": "Gradle build tool dependency cache. Safe to delete; subsequent builds will automatically re-download dependencies.",
    "maven": "Maven local repository dependency cache (.m2). Safe to delete; Maven will re-download packages when needed.",
    "pip": "Python pip package installer cache. Can be safely cleared using the command 'pip cache purge'.",
    "steam": "Steam game client caches, HTML Web caches, or shader pre-caches. Safe to delete.",
    "discord": "Discord client cache and local files. Safe to delete; Discord will recreate them on startup.",
}
