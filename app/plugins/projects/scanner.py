"""
Finds your code projects and reads their Git state (no Qt here).

A project = a folder with .git, a Visual Studio solution, or a typical project file (package.json,
pyproject.toml, *.csproj, Dockerfile, go.mod…), searched a few levels deep under the folders you choose
(default: %USERPROFILE%\\source\\repos). Git is read with plain `git` commands — nothing is changed unless
you press Pull / Fetch / Start Git.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

NO_WINDOW = 0x08000000
SKIP = {"node_modules", ".git", "bin", "obj", "dist", "build", ".venv", "venv", "__pycache__", ".vs", ".idea",
        "packages", "target", ".next", ".gradle"}
MARKERS = {  # file name / pattern -> kind
    "package.json": "node", "pyproject.toml": "python", "requirements.txt": "python", "setup.py": "python",
    "main.py": "python", "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java", "build.gradle": "java",
    "composer.json": "php", "Dockerfile": "docker", "docker-compose.yml": "docker", "compose.yaml": "docker",
    "CMakeLists.txt": "cpp", "index.html": "web",
}
KIND_ORDER = ["dotnet", "node", "python", "rust", "go", "java", "php", "cpp", "docker", "web", "other"]


def default_roots() -> list[str]:
    repos = Path.home() / "source" / "repos"
    return [str(repos)] if repos.is_dir() else []


def git_exe() -> str:
    return shutil.which("git") or ""


def visual_studio() -> str:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            out = subprocess.run([str(vswhere), "-latest", "-property", "productPath"], capture_output=True, text=True,
                                 timeout=10, creationflags=NO_WINDOW).stdout.strip()
            if out and Path(out).is_file():
                return out
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ""


def vscode() -> str:
    found = shutil.which("code")
    if found:
        return found
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        exe = Path(base) / ("Programs" if "Local" in base else "") / "Microsoft VS Code" / "Code.exe"
        if exe.is_file():
            return str(exe)
    return ""


def _is_project(folder: Path, names: set[str]) -> bool:
    return ".git" in names or any(n.endswith((".sln", ".csproj")) for n in names) or bool(names & set(MARKERS))


def find_projects(roots: list[str], depth: int = 3, limit: int = 300) -> list[Path]:
    found: list[Path] = []

    def walk(folder: Path, level: int):
        if len(found) >= limit:
            return
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        names = {e.name for e in entries}
        if level > 0 and _is_project(folder, names):
            found.append(folder)
            return  # a project's own sub-folders are part of it
        if level >= depth:
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False) and e.name not in SKIP and not e.name.startswith("."):
                walk(Path(e.path), level + 1)

    for root in roots:
        if Path(root).is_dir():
            walk(Path(root), 0)
    return found


def _git(path: Path, *args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run([git_exe(), "-C", str(path), *args], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def kind_of(path: Path) -> str:
    try:
        names = {e.name for e in os.scandir(path)}
    except OSError:
        return "other"
    if any(n.endswith((".sln", ".csproj")) for n in names):
        return "dotnet"
    for marker, kind in MARKERS.items():
        if marker in names:
            return kind
    return "other"


def inspect(path: Path) -> dict:
    """Everything shown on a project row."""
    info = {"path": str(path), "name": path.name, "kind": kind_of(path), "git": False,
            "sln": next((str(p) for p in sorted(path.glob("*.sln"))), "")}
    try:
        info["modified"] = max((p.stat().st_mtime for p in path.iterdir()), default=path.stat().st_mtime)
    except OSError:
        info["modified"] = 0
    if not git_exe() or not (path / ".git").exists():
        return info
    info["git"] = True
    _, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    info["branch"] = branch if branch and "fatal" not in branch else "—"
    _, status = _git(path, "status", "--porcelain")
    info["changes"] = len([line for line in status.splitlines() if line.strip()])
    code, counts = _git(path, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if code == 0 and re.match(r"^\d+\s+\d+$", counts):
        ahead, behind = counts.split()
        info["ahead"], info["behind"] = int(ahead), int(behind)
    code, last = _git(path, "log", "-1", "--format=%h|%s|%cr|%an")
    if code == 0 and "|" in last:
        h, subject, when, author = (last.split("|", 3) + ["", "", "", ""])[:4]
        info["last"] = {"hash": h, "subject": subject, "when": when, "author": author}
    _, remote = _git(path, "remote", "get-url", "origin")
    info["remote"] = remote if remote and "fatal" not in remote and "error" not in remote else ""
    return info


def inspect_all(paths: list[Path]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(inspect, paths))


def web_url(remote: str) -> str:
    """git@github.com:me/repo.git / https://…/repo.git -> https://github.com/me/repo"""
    m = re.match(r"^git@([^:]+):(.+?)(\.git)?$", remote)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^https?://(?:[^@/]+@)?(.+?)(\.git)?$", remote)
    return f"https://{m.group(1)}" if m else ""


# ------------------------------------------------------------------------- actions
def pull(path: str) -> tuple[int, str]:
    return _git(Path(path), "pull", "--ff-only", timeout=120)


def fetch(path: str) -> tuple[int, str]:
    return _git(Path(path), "fetch", "--all", "--prune", timeout=120)


def init(path: str) -> tuple[int, str]:
    return _git(Path(path), "init")


def recent_commits(path: str, n: int = 20) -> str:
    _, out = _git(Path(path), "log", f"-{n}", "--format=%h  %cr  %an%n    %s")
    return out


def changed_files(path: str) -> str:
    _, out = _git(Path(path), "status", "--short", "--branch")
    return out


def open_terminal(path: str) -> None:
    wt = shutil.which("wt")
    if wt:
        subprocess.Popen([wt, "-d", path], creationflags=NO_WINDOW)
    else:
        subprocess.Popen(["powershell.exe", "-NoExit", "-Command", f"Set-Location -LiteralPath '{path}'"],
                         creationflags=0x00000010)  # CREATE_NEW_CONSOLE
