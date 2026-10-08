"""Local project, preview, packaging, and sandboxed Docker tools."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = Path(os.getenv("JARVIS_PROJECTS_DIR", str(APP_DIR / "workspace" / "projects"))).expanduser().resolve()
_PREVIEWS: dict[str, subprocess.Popen] = {}
_PREVIEW_URLS: dict[str, str] = {}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-_").lower()
    if not value or len(value) > 50:
        raise ValueError("Project name must contain 1–50 letters, numbers, dashes, or underscores.")
    return value


def _project_path(project: str) -> Path:
    path = (PROJECTS_DIR / _slug(project)).resolve()
    root = PROJECTS_DIR.resolve()
    normalized_path = os.path.normcase(os.path.realpath(path))
    normalized_root = os.path.normcase(os.path.realpath(root))
    try:
        if os.path.commonpath([normalized_path, normalized_root]) != normalized_root:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Project path is outside the local projects directory.") from exc
    return path


def create_local_project(name: str, template: str = "python") -> dict:
    """Create a local Python or static project workspace."""
    if template not in {"python", "static"}:
        raise ValueError("Supported templates are python and static.")
    path = _project_path(name)
    if path.exists():
        raise FileExistsError(f"Project already exists: {path.name}")
    path.mkdir(parents=True)
    if template == "python":
        (path / "main.py").write_text(
            'def main():\n    print("Your local project is ready.")\n\n\nif __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
    else:
        (path / "index.html").write_text(
            "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Local project</title>"
            "<main><h1>Local project ready</h1></main></html>\n",
            encoding="utf-8",
        )
    (path / "README.md").write_text(
        f"# {path.name}\n\nLocal workspace created by JARVIS-LOCAL.\n",
        encoding="utf-8",
    )
    return {"project": path.name, "path": str(path), "template": template}


def write_project_file(project: str, relative_path: str, content: str) -> dict:
    """Write UTF-8 text to a file contained by one Jarvis local project."""
    root = _project_path(project)
    if not root.is_dir():
        raise FileNotFoundError(f"Local project does not exist: {project}")
    if not isinstance(relative_path, str) or not relative_path.strip() or len(relative_path) > 240:
        raise ValueError("Provide a project-relative file path up to 240 characters.")
    if not isinstance(content, str):
        raise ValueError("Project file content must be text.")
    if len(content.encode("utf-8")) > 500_000:
        raise ValueError("Project file content must be 500 KB or smaller.")
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Project file path must stay inside its local project.") from exc
    if not target.is_file() and target.exists():
        raise ValueError("Project file target must be a regular file.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"project": root.name, "file": str(target.relative_to(root)), "bytes": target.stat().st_size}


def live_preview(project: str, port: int | None = None) -> dict:
    path = _project_path(project)
    if not path.is_dir():
        raise FileNotFoundError(f"Local project does not exist: {project}")
    if port is not None and not 1024 <= int(port) <= 65535:
        raise ValueError("Preview port must be between 1024 and 65535.")
    if path.name in _PREVIEWS and _PREVIEWS[path.name].poll() is None:
        raise RuntimeError(f"Preview is already running for {project}.")
    server = socket.socket()
    server.bind(("127.0.0.1", int(port) if port else 0))
    selected_port = server.getsockname()[1]
    server.close()
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(selected_port), "--bind", "127.0.0.1"],
        cwd=str(path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError("Local preview server exited during startup.")
        try:
            with socket.create_connection(("127.0.0.1", selected_port), timeout=0.2):
                _PREVIEWS[path.name] = process
                url = f"http://127.0.0.1:{selected_port}"
                _PREVIEW_URLS[path.name] = url
                return {"project": path.name, "url": url, "pid": process.pid}
        except OSError:
            time.sleep(0.1)
    process.terminate()
    raise TimeoutError("Local preview did not start within three seconds.")


def stop_preview(project: str) -> dict:
    name = _slug(project)
    process = _PREVIEWS.get(name)
    if not process or process.poll() is not None:
        _PREVIEWS.pop(name, None)
        return {"stopped": False, "message": "No running preview for this project."}
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    _PREVIEWS.pop(name, None)
    _PREVIEW_URLS.pop(name, None)
    return {"stopped": True, "project": name}


def show_project(project: str) -> dict:
    """Start a local preview and open it in Jarvis's isolated visible browser."""
    name = _slug(project)
    process = _PREVIEWS.get(name)
    if process is None or process.poll() is not None:
        _PREVIEWS.pop(name, None)
        _PREVIEW_URLS.pop(name, None)
        preview = live_preview(name)
    else:
        preview = {"project": name, "url": _PREVIEW_URLS[name], "pid": process.pid}
    from demo_browser import open_demo_url

    browser = open_demo_url(preview["url"])
    return {**preview, "browser": browser}


def make_exe(project: str, entry_file: str = "main.py") -> dict:
    path = _project_path(project)
    entry = (path / entry_file).resolve()
    try:
        entry.relative_to(path)
    except ValueError as exc:
        raise ValueError("Entry file must be inside the project directory.") from exc
    if not entry.is_file() or entry.suffix.lower() != ".py":
        raise FileNotFoundError("Choose an existing Python entry file inside the project.")
    output_dir = path / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", "--distpath", str(output_dir), str(entry)],
        cwd=str(path),
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode:
        raise RuntimeError(f"PyInstaller failed:\n{result.stderr[-4000:]}")
    executable = output_dir / (entry.stem + (".exe" if sys.platform == "win32" else ""))
    return {"executable": str(executable), "built": executable.is_file(), "log": result.stdout[-1500:]}


def install_package(package: str, project: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", package):
        raise ValueError("Package must be a plain PyPI name; URLs, flags, and VCS installs are blocked.")
    cwd = str(_project_path(project)) if project else str(APP_DIR)
    if project and not Path(cwd).is_dir():
        raise FileNotFoundError(f"Project does not exist: {project}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode:
        raise RuntimeError(f"Package installation failed:\n{result.stderr[-3000:]}")
    return {"package": package, "installed": True, "output": result.stdout[-2000:]}


def docker_status() -> dict:
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError("Install the Docker SDK and Docker Desktop, then restart JARVIS.") from exc
    try:
        client = docker.from_env(timeout=5)
        info = client.ping()
        return {"available": bool(info), "version": client.version().get("Version", "unknown")}
    except Exception as exc:
        raise RuntimeError(f"Docker daemon is unavailable: {exc}") from exc


def docker_build(project: str, image_tag: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,100}(:[a-z0-9._-]+)?", image_tag):
        raise ValueError("Use a valid local image tag (lowercase letters, digits, dot, dash, slash, colon).")
    context = _project_path(project)
    if not context.is_dir() or not (context / "Dockerfile").is_file():
        raise FileNotFoundError("The project must exist and contain a Dockerfile.")
    try:
        import docker
        client = docker.from_env(timeout=30)
        image, logs = client.images.build(path=str(context), tag=image_tag, rm=True, forcerm=True)
        tail = [item.get("stream", "").strip() for item in logs if item.get("stream")]
        return {"image": image.tags[0] if image.tags else image_tag, "built": True, "log": "\n".join(tail[-15:])}
    except Exception as exc:
        raise RuntimeError(f"Docker build failed: {exc}") from exc


def docker_run(image: str, command: list[str] | None = None, timeout_seconds: int = 30) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,160}", image):
        raise ValueError("Provide a local Docker image name.")
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("Container timeout must be between 1 and 120 seconds.")
    if command is not None and (not isinstance(command, list) or len(command) > 32 or any(len(str(part)) > 500 for part in command)):
        raise ValueError("Container command must be a list of up to 32 short arguments.")
    try:
        import docker
        client = docker.from_env(timeout=10)
        container = client.containers.run(
            image,
            command=command,
            detach=True,
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="512m",
            pids_limit=128,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        )
        try:
            result = container.wait(timeout=timeout_seconds)
            logs = container.logs(stdout=True, stderr=True, tail=200).decode("utf-8", errors="replace")
            return {"container": container.short_id, "status": result.get("StatusCode"), "logs": logs[-8000:]}
        finally:
            container.remove(force=True)
    except Exception as exc:
        raise RuntimeError(f"Restricted Docker run failed: {exc}") from exc
