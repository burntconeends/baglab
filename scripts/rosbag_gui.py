"""Flask browser UI for the baglab rosbag workflow.

Exposes every baglab function: sync bags from the Orin, convert .db3 -> .mcap,
run bronze quality checks (baglab check), save an animated Rerun recording
(.rrd) for offline viewing, and stream a bag into Rerun's web viewer.

Run inside the container (recommended — all deps are installed there):

    ./docker/run.sh
    # inside:
    python3 scripts/rosbag_gui.py --host 0.0.0.0 --port 8765
    # then open http://127.0.0.1:8765

If the GUI is launched on the host but the deps live inside a running
container, route jobs through docker exec:

    python3 scripts/rosbag_gui.py \
        --docker-container baglab \
        --docker-repo /workspace \
        --docker-bin "docker"
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import threading
import time
import uuid

try:
    from flask import Flask, jsonify, request
except ImportError as exc:
    raise SystemExit(
        "Flask is not installed in this environment. Install the project extras with:\n"
        '  uv pip install -e "decoupled_wbc[full,dev]" -e "gear_sonic[sim]"\n'
        "or install Flask directly:\n"
        "  uv pip install flask\n"
    ) from exc

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
# Single-mount layout: bags inside the repo, outputs inside the repo. Both
# gitignored. BAGLAB_BAGS_ROOT overrides the bag root if you want it elsewhere.
DATA_ROOT = Path(os.environ.get("BAGLAB_BAGS_ROOT", REPO_ROOT / "data"))
BAGS_ROOT = DATA_ROOT / "orin_bags"
OUTPUTS_ROOT = REPO_ROOT / "outputs"
CONVERT_SCRIPT = SCRIPT_DIR / "convert_db3_to_mcap.py"
RERUN_WEB_SCRIPT = SCRIPT_DIR / "bag_to_rerun_web.py"
RERUN_SAVE_SCRIPT = SCRIPT_DIR / "bag_to_rerun.py"
SYNC_SCRIPT = SCRIPT_DIR / "sync_orin_bags.sh"
EXECUTION = {
    "docker_container": "",
    "docker_repo": "/workspace",
    "docker_bin": ["docker"],
}


class Job:
    def __init__(self, name: str, cmd: list[str], cwd: Path):
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.status = "running"
        self.returncode: int | None = None
        self.logs: list[str] = []
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None

    def append(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 4000:
                self.logs = self.logs[-4000:]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "name": self.name,
                "cmd": self.cmd,
                "status": self.status,
                "returncode": self.returncode,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "logs": list(self.logs),
            }

    def run(self) -> None:
        self.append("$ " + " ".join(self.cmd))
        self.append("")
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=(sys.platform != "win32"),
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self.append(line.rstrip("\n"))
            self.returncode = self._proc.wait()
            self.status = "done" if self.returncode == 0 else "failed"
        except Exception as exc:
            self.status = "failed"
            self.returncode = -1
            self.append(f"ERROR: {exc}")
        finally:
            self.finished_at = time.time()

    def stop(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self.append("")
        self.append("Stopping job...")
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception as exc:
            self.append(f"WARN: failed to terminate process: {exc}")


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, name: str, cmd: list[str], cwd: Path = REPO_ROOT) -> Job:
        job = Job(name, cmd, cwd)
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(target=job.run, daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.started_at, reverse=True)
        return [job.snapshot() for job in jobs[:20]]

    def find_running_with_prefix(self, prefix: str) -> list[Job]:
        """Return every job whose name starts with `prefix` and is still running."""
        with self._lock:
            return [j for j in self._jobs.values()
                    if j.status == "running" and j.name.startswith(prefix)]

    def stop_running_with_prefix(self, prefix: str, timeout_s: float = 3.0) -> int:
        """Stop matching running jobs and wait briefly for them to exit.
        Returns the count of jobs we asked to stop. The brief wait lets any
        ports they were holding (e.g. Rerun web/gRPC) be released before the
        caller starts a replacement."""
        stopped = self.find_running_with_prefix(prefix)
        for job in stopped:
            job.append("")
            job.append("Auto-stopped by a new request that needs the same port.")
            job.stop()
        deadline = time.time() + timeout_s
        while time.time() < deadline and any(j.status == "running" for j in stopped):
            time.sleep(0.1)
        return len(stopped)


def _kill_leftover_serve_processes() -> list[int]:
    """SIGTERM any leftover bag_to_rerun(_web).py processes anywhere in the
    container, even ones this GUI didn't start (e.g. survivors of a previous
    GUI restart). Returns the PIDs we signalled. Linux-only (uses /proc)."""
    if not Path("/proc").is_dir():
        return []
    my_pid = os.getpid()
    parent_pid = os.getppid()
    killed: list[int] = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(proc.name)
        except ValueError:
            continue
        if pid in (my_pid, parent_pid):
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError):
            continue
        argv = cmdline.replace(b"\x00", b" ").decode("utf-8", "ignore")
        if "bag_to_rerun" in argv:
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except OSError:
                pass
    if killed:
        # Give them a moment to release sockets.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            alive = []
            for pid in killed:
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except OSError:
                    pass
            if not alive:
                break
            time.sleep(0.1)
    return killed


JOBS = JobStore()


def _safe_repo_path(value: str, *, must_exist: bool = False) -> Path:
    if not value or not str(value).strip():
        raise ValueError("Path is empty — pick a row in the bag list or paste a path.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if REPO_ROOT not in path.parents and path != REPO_ROOT:
        raise ValueError(f"Path must be inside repo: {path}")
    if must_exist and not path.exists():
        raise ValueError(f"Path does not exist: {path}")
    return path


def _resolve_bag(payload: dict) -> Path:
    """Common bag validation for check / rerun / save actions.

    Rejects an empty field, the bare repo root, anything that's not under the
    bags root, and anything that isn't either an .mcap file or a rosbag2 dir
    (a directory containing metadata.yaml). Returns the resolved path.
    """
    raw = str(payload.get("bag") or "")
    bag = _safe_repo_path(raw, must_exist=True)
    if bag == REPO_ROOT or bag == BAGS_ROOT:
        raise ValueError("Pick a specific bag (.mcap file or rosbag2 dir), "
                         "not the repo root or the bags root.")
    if bag.is_file() and bag.suffix == ".mcap":
        return bag
    if bag.is_dir() and (bag / "metadata.yaml").exists():
        return bag
    raise ValueError(
        f"Not a bag: {_rel(bag)} — expected an .mcap file or a rosbag2 "
        f"directory containing metadata.yaml.")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _container_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return str(resolved)
    return str(Path(EXECUTION["docker_repo"]) / rel)


def _wrap_cmd(cmd: list[str]) -> list[str]:
    container = EXECUTION["docker_container"]
    if not container:
        return cmd
    return [
        *EXECUTION["docker_bin"],
        "exec",
        "-i",
        "-w",
        EXECUTION["docker_repo"],
        container,
        *cmd,
    ]


def _python_cmd(script: Path, *args: Path | str) -> list[str]:
    if not EXECUTION["docker_container"]:
        return [sys.executable, str(script), *[str(arg) for arg in args]]
    container_args = [
        _container_path(arg) if isinstance(arg, Path) else str(arg)
        for arg in args
    ]
    return _wrap_cmd(["python3", _container_path(script), *container_args])


def _bash_cmd(script: Path, *args: str) -> list[str]:
    if not EXECUTION["docker_container"]:
        return ["bash", str(script), *args]
    return _wrap_cmd(["bash", _container_path(script), *args])


def _baglab_cmd(*args: str) -> list[str]:
    """Invoke baglab — installed console script first, fall back to module form."""
    base = ["baglab", *args]
    if not EXECUTION["docker_container"]:
        # Use the entry point if installed, else run as a module.
        if subprocess.run(["which", "baglab"], capture_output=True).returncode == 0:
            return base
        return [sys.executable, "-m", "baglab", *args]
    return _wrap_cmd(base)


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _fmt_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def scan_bags() -> list[dict]:
    if not BAGS_ROOT.exists():
        return []

    bags = []
    for path in sorted(BAGS_ROOT.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        db3_files = sorted(path.glob("*.db3"))
        metadata = path / "metadata.yaml"
        default_mcap = BAGS_ROOT / f"{path.name}.mcap"
        stat = path.stat()
        bags.append(
            {
                "name": path.name,
                "path": _rel(path),
                "metadata": metadata.exists(),
                "db3_count": len(db3_files),
                "default_mcap": _rel(default_mcap),
                "default_mcap_exists": default_mcap.exists(),
                "size": _fmt_bytes(_dir_size(path)),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return bags


def scan_mcaps() -> list[dict]:
    if not BAGS_ROOT.exists():
        return []
    mcaps = []
    for path in sorted(BAGS_ROOT.rglob("*.mcap"), reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        mcaps.append(
            {
                "name": path.name,
                "path": _rel(path),
                "size": _fmt_bytes(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return mcaps


def scan_rrds() -> list[dict]:
    if not OUTPUTS_ROOT.exists():
        return []
    rrds = []
    for path in sorted(OUTPUTS_ROOT.rglob("*.rrd"), reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        rrds.append(
            {
                "name": path.name,
                "path": _rel(path),
                "size": _fmt_bytes(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "open_cmd": f"rerun {path.resolve()}",
            }
        )
    return rrds


def build_convert_job(payload: dict) -> Job:
    bag = _safe_repo_path(str(payload.get("bag") or ""), must_exist=True)
    out = _safe_repo_path(str(payload.get("output") or ""))
    overwrite = bool(payload.get("overwrite", False))

    if not bag.is_dir():
        raise ValueError("Input bag must be a directory.")
    if out.suffix != ".mcap":
        raise ValueError("Output path must end with .mcap.")
    if BAGS_ROOT not in bag.parents and bag != BAGS_ROOT:
        raise ValueError(f"Input bag must be under {_rel(BAGS_ROOT)}.")
    if BAGS_ROOT not in out.parents and out.parent != BAGS_ROOT:
        raise ValueError(f"Output MCAP must be under {_rel(BAGS_ROOT)}.")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        if not overwrite:
            raise ValueError(f"Output already exists: {_rel(out)}")
        out.unlink()

    cmd = _python_cmd(CONVERT_SCRIPT, bag, out)
    return JOBS.start(f"Convert {bag.name}", cmd)


def build_sync_job() -> Job:
    if not SYNC_SCRIPT.exists():
        raise ValueError(f"Missing sync script: {_rel(SYNC_SCRIPT)}")
    cmd = _bash_cmd(SYNC_SCRIPT)
    return JOBS.start("Sync Orin bags", cmd)


def build_rerun_job(payload: dict) -> Job:
    bag = _resolve_bag(payload)
    try:
        subsample = int(payload.get("subsample", 20))
    except (TypeError, ValueError) as exc:
        raise ValueError("Subsample must be an integer.") from exc
    if subsample <= 0:
        raise ValueError("Subsample must be positive.")
    try:
        port = int(payload.get("port", 9876))
    except (TypeError, ValueError) as exc:
        raise ValueError("Rerun port must be an integer.") from exc
    if port <= 0 or port > 65535:
        raise ValueError("Rerun port must be between 1 and 65535.")

    cmd = _python_cmd(
        RERUN_WEB_SCRIPT,
        bag,
        "--subsample",
        str(subsample),
        "--port",
        str(port),
    )
    if payload.get("no_images"):
        cmd.append("--no-images")
    # Only one Rerun-web serve at a time — it binds a fixed port pair.
    JOBS.stop_running_with_prefix("Rerun web ")
    # Also kill leftovers from a previous GUI session that this JobStore
    # never tracked (the OS process can outlive a Python restart).
    _kill_leftover_serve_processes()
    return JOBS.start(f"Rerun web {bag.name}", cmd)


def build_rerun_save_job(payload: dict) -> Job:
    """Run scripts/bag_to_rerun.py to write a .rrd file under outputs/."""
    bag = _safe_repo_path(str(payload.get("bag") or ""), must_exist=True)
    if bag.suffix != ".mcap" and not bag.is_dir():
        raise ValueError("Input must be an .mcap file or bag directory.")
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUTS_ROOT / f"{bag.stem}.rrd"
    if not bool(payload.get("overwrite", False)) and out_path.exists():
        raise ValueError(f"Output already exists: {_rel(out_path)}")
    try:
        subsample = int(payload.get("subsample", 20))
    except (TypeError, ValueError) as exc:
        raise ValueError("Subsample must be an integer.") from exc
    if subsample <= 0:
        raise ValueError("Subsample must be positive.")

    cmd = _python_cmd(
        RERUN_SAVE_SCRIPT,
        bag,
        "--subsample", str(subsample),
        "--out", out_path,
    )
    if payload.get("no_images"):
        cmd.append("--no-images")
    if payload.get("no_robot"):
        cmd.append("--no-robot")
    if payload.get("show_collision"):
        cmd.append("--show-collision")
    if payload.get("strict"):
        cmd.append("--strict")
    return JOBS.start(f"Save .rrd {bag.name}", cmd)


def build_check_job(payload: dict) -> Job:
    """Run baglab check on a bag and surface the printed report."""
    bag = _safe_repo_path(str(payload.get("bag") or ""), must_exist=True)
    if bag.suffix != ".mcap" and not bag.is_dir():
        raise ValueError("Input must be an .mcap file or bag directory.")
    cmd = _baglab_cmd("check", str(bag) if EXECUTION["docker_container"]
                      else str(bag.resolve()))
    if payload.get("no_smoothness"):
        cmd.append("--no-smoothness")
    if payload.get("no_static"):
        cmd.append("--no-static")
    if payload.get("json"):
        cmd.append("--json")
    return JOBS.start(f"Check {bag.name}", cmd)


INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>G1 Rosbag Tools</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #246bfe;
      --accent-dark: #174fc0;
      --ok: #12805c;
      --warn: #b54708;
      --bad: #b42318;
      --code: #111827;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 { font-size: 18px; margin: 0; letter-spacing: 0; }
    main {
      display: grid;
      grid-template-columns: minmax(420px, 1.2fr) minmax(360px, 0.8fr);
      gap: 16px;
      padding: 16px;
      min-height: calc(100vh - 56px);
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    h2 { font-size: 14px; margin: 0; letter-spacing: 0; }
    .stack { display: grid; gap: 16px; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    button {
      min-height: 34px;
      padding: 7px 12px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
    }
    button:hover { border-color: #aeb8c8; }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }
    button.primary:hover { background: var(--accent-dark); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    label { color: var(--muted); font-size: 12px; display: grid; gap: 5px; }
    input[type="text"], input[type="number"], select {
      width: 100%;
      min-height: 34px;
      padding: 7px 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--ink);
      font-size: 13px;
    }
    .table-wrap { overflow: auto; max-height: calc(100vh - 330px); }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td {
      padding: 9px 10px;
      border-bottom: 1px solid #edf0f5;
      text-align: left;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    th {
      position: sticky;
      top: 0;
      background: #f9fafb;
      color: var(--muted);
      font-weight: 600;
      z-index: 1;
    }
    tr { cursor: pointer; }
    tr:hover td { background: #f6f9ff; }
    tr.selected td { background: #eaf1ff; }
    .badge {
      display: inline-flex;
      align-items: center;
      height: 22px;
      padding: 0 7px;
      border-radius: 999px;
      background: #eef2f6;
      color: #344054;
      font-size: 12px;
    }
    .badge.ok { background: #e8f5ef; color: var(--ok); }
    .badge.warn { background: #fff4e5; color: var(--warn); }
    .badge.bad { background: #fee4e2; color: var(--bad); }
    .panel-body { padding: 14px; }
    .form-grid { display: grid; gap: 12px; }
    .two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    pre {
      margin: 0;
      padding: 12px;
      border-radius: 8px;
      background: var(--code);
      color: #d1d5db;
      overflow: auto;
      min-height: 280px;
      max-height: calc(100vh - 430px);
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .status-line {
      color: var(--muted);
      min-height: 20px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .error { color: var(--bad); }
    .split-list {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      max-height: 180px;
      overflow: auto;
    }
    .mcap-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      cursor: pointer;
    }
    .mcap-row:hover, .mcap-row.selected { border-color: var(--accent); background: #f6f9ff; }
    .muted { color: var(--muted); }
    @media (max-width: 960px) {
      main { grid-template-columns: 1fr; }
      .table-wrap { max-height: 420px; }
      pre { max-height: 360px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>G1 Rosbag Tools</h1>
    <div class="toolbar">
      <button id="syncBtn">Sync</button>
      <button id="refreshBtn">Refresh</button>
    </div>
  </header>
  <main>
    <div class="stack">
      <section>
        <div class="section-head">
          <h2>Orin Bags</h2>
          <div id="bagCount" class="status-line"></div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width: 45%">Name</th>
                <th style="width: 15%">DB3</th>
                <th style="width: 16%">MCAP</th>
                <th style="width: 12%">Size</th>
                <th style="width: 12%">Modified</th>
              </tr>
            </thead>
            <tbody id="bagRows"></tbody>
          </table>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>MCAP Files</h2>
          <div id="mcapCount" class="status-line"></div>
        </div>
        <div class="panel-body">
          <div id="mcapRows" class="split-list"></div>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>RRD Recordings</h2>
          <div id="rrdCount" class="status-line"></div>
        </div>
        <div class="panel-body">
          <div id="rrdRows" class="split-list"></div>
          <div class="muted" style="margin-top:8px;font-size:12px;">
            Open on the host with the suggested <code>rerun ...</code> command.
          </div>
        </div>
      </section>
    </div>

    <div class="stack">
      <section>
        <div class="section-head">
          <h2>Convert</h2>
          <button id="convertBtn" class="primary">Convert</button>
        </div>
        <div class="panel-body form-grid">
          <label>
            Input bag
            <input id="bagInput" type="text" placeholder="outputs/orin_bags/...">
          </label>
          <label>
            Output MCAP
            <input id="mcapOutput" type="text" placeholder="outputs/orin_bags/name.mcap">
          </label>
          <label class="check">
            <input id="overwrite" type="checkbox">
            Overwrite existing MCAP
          </label>
          <div id="convertStatus" class="status-line"></div>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>Quality Check</h2>
          <button id="checkBtn" class="primary">Run check</button>
        </div>
        <div class="panel-body form-grid">
          <label>
            Bag (.mcap or .db3 dir)
            <input id="checkInput" type="text" placeholder="data/orin_bags/...">
          </label>
          <div class="two">
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="checkNoSmoothness" type="checkbox">
              Skip smoothness
            </label>
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="checkNoStatic" type="checkbox">
              Skip static-frame
            </label>
          </div>
          <label class="check">
            <input id="checkJson" type="checkbox">
            JSON output (instead of summary)
          </label>
          <div id="checkStatus" class="status-line"></div>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>Rerun (save .rrd)</h2>
          <button id="saveBtn" class="primary">Save .rrd</button>
        </div>
        <div class="panel-body form-grid">
          <label>
            Bag (.mcap or .db3 dir)
            <input id="saveInput" type="text" placeholder="data/orin_bags/...">
          </label>
          <div class="two">
            <label>
              Subsample
              <input id="saveSubsample" type="number" min="1" value="20">
            </label>
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="saveOverwrite" type="checkbox">
              Overwrite
            </label>
          </div>
          <div class="two">
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="saveNoImages" type="checkbox">
              No images
            </label>
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="saveNoRobot" type="checkbox">
              No robot model
            </label>
          </div>
          <div class="two">
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="saveShowCollision" type="checkbox">
              Show collision
            </label>
            <label class="check" style="align-self:end; min-height:34px;">
              <input id="saveStrict" type="checkbox">
              Strict (missing-topic = fail)
            </label>
          </div>
          <div id="saveStatus" class="status-line"></div>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>Rerun Web</h2>
          <button id="rerunBtn">Serve</button>
        </div>
        <div class="panel-body form-grid">
          <label>
            MCAP or bag
            <input id="rerunInput" type="text" placeholder="outputs/orin_bags/name.mcap">
          </label>
          <div class="two">
            <label>
              Subsample
              <input id="subsample" type="number" min="1" value="20">
            </label>
            <label>
              Port
              <input id="rerunPort" type="number" min="1" max="65535" value="9876">
            </label>
          </div>
          <div>
            <label class="check" style="align-self: end; min-height: 34px;">
              <input id="noImages" type="checkbox">
              No images
            </label>
          </div>
          <div id="rerunStatus" class="status-line">Viewer URL will appear here once serving starts.</div>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>Job Log</h2>
          <div class="toolbar">
            <select id="jobSelect"></select>
            <button id="stopBtn">Stop</button>
          </div>
        </div>
        <div class="panel-body form-grid">
          <div id="jobStatus" class="status-line"></div>
          <pre id="logBox"></pre>
        </div>
      </section>
    </div>
  </main>

  <script>
    let bags = [];
    let mcaps = [];
    let rrds = [];
    let selectedBag = null;
    let selectedMcap = null;
    let selectedRrd = null;
    let activeJob = null;
    let pollTimer = null;

    const $ = (id) => document.getElementById(id);

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || res.statusText);
      }
      return data;
    }

    function setStatus(id, text, isError = false) {
      const el = $(id);
      el.textContent = text || "";
      el.className = "status-line" + (isError ? " error" : "");
    }

    function renderBags() {
      $("bagCount").textContent = `${bags.length} bags`;
      const body = $("bagRows");
      body.innerHTML = "";
      for (const bag of bags) {
        const tr = document.createElement("tr");
        if (selectedBag && selectedBag.path === bag.path) tr.classList.add("selected");
        tr.innerHTML = `
          <td title="${bag.path}">${bag.name}</td>
          <td><span class="badge ${bag.db3_count ? "ok" : "bad"}">${bag.db3_count}</span></td>
          <td><span class="badge ${bag.default_mcap_exists ? "ok" : "warn"}">${bag.default_mcap_exists ? "exists" : "none"}</span></td>
          <td>${bag.size}</td>
          <td title="${bag.modified}">${bag.modified.slice(5, 16)}</td>
        `;
        tr.onclick = () => selectBag(bag);
        body.appendChild(tr);
      }
    }

    function renderMcaps() {
      $("mcapCount").textContent = `${mcaps.length} files`;
      const body = $("mcapRows");
      body.innerHTML = "";
      for (const mcap of mcaps) {
        const row = document.createElement("div");
        row.className = "mcap-row" + (selectedMcap && selectedMcap.path === mcap.path ? " selected" : "");
        row.innerHTML = `
          <div>
            <div title="${mcap.path}">${mcap.name}</div>
            <div class="muted">${mcap.path}</div>
          </div>
          <div class="muted">${mcap.size}</div>
        `;
        row.onclick = () => selectMcap(mcap);
        body.appendChild(row);
      }
    }

    function selectBag(bag) {
      selectedBag = bag;
      $("bagInput").value = bag.path;
      $("mcapOutput").value = bag.default_mcap;
      // Also seed the Check + Save panels so a single click is enough.
      $("checkInput").value = bag.default_mcap_exists ? bag.default_mcap : bag.path;
      $("saveInput").value = bag.default_mcap_exists ? bag.default_mcap : bag.path;
      if (!selectedMcap && bag.default_mcap_exists) {
        $("rerunInput").value = bag.default_mcap;
      }
      renderBags();
    }

    function selectMcap(mcap) {
      selectedMcap = mcap;
      $("rerunInput").value = mcap.path;
      $("checkInput").value = mcap.path;
      $("saveInput").value = mcap.path;
      renderMcaps();
    }

    function renderRrds() {
      $("rrdCount").textContent = `${rrds.length} files`;
      const body = $("rrdRows");
      body.innerHTML = "";
      for (const rrd of rrds) {
        const row = document.createElement("div");
        row.className = "mcap-row" + (selectedRrd && selectedRrd.path === rrd.path ? " selected" : "");
        row.innerHTML = `
          <div>
            <div title="${rrd.path}">${rrd.name}</div>
            <div class="muted">${rrd.open_cmd}</div>
          </div>
          <div class="muted">${rrd.size}</div>
        `;
        row.onclick = () => {
          selectedRrd = rrd;
          // Copy the open-command to clipboard if the API is available.
          if (navigator.clipboard) navigator.clipboard.writeText(rrd.open_cmd).catch(() => {});
          renderRrds();
        };
        body.appendChild(row);
      }
    }

    async function refresh() {
      try {
        const data = await api("/api/bags");
        bags = data.bags || [];
        mcaps = data.mcaps || [];
        rrds = data.rrds || [];
        renderBags();
        renderMcaps();
        renderRrds();
      } catch (err) {
        setStatus("convertStatus", err.message, true);
      }
    }

    async function startJob(action, payload = {}) {
      const data = await api("/api/jobs", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action, ...payload}),
      });
      activeJob = data.job.id;
      await loadJobs();
      startPolling();
    }

    async function loadJobs() {
      const data = await api("/api/jobs");
      const select = $("jobSelect");
      select.innerHTML = "";
      for (const job of data.jobs || []) {
        const option = document.createElement("option");
        option.value = job.id;
        option.textContent = `${job.status} - ${job.name}`;
        select.appendChild(option);
      }
      if (activeJob) select.value = activeJob;
    }

    async function pollJob() {
      if (!activeJob) return;
      try {
        const data = await api(`/api/jobs/${activeJob}`);
        const job = data.job;
        $("jobStatus").textContent = `${job.status} ${job.returncode === null ? "" : `(code ${job.returncode})`}`;
        $("logBox").textContent = (job.logs || []).join("\n");
        $("logBox").scrollTop = $("logBox").scrollHeight;
        await loadJobs();
        if (job.status !== "running") {
          clearInterval(pollTimer);
          pollTimer = null;
          await refresh();
        }
      } catch (err) {
        $("jobStatus").textContent = err.message;
      }
    }

    function startPolling() {
      if (pollTimer) clearInterval(pollTimer);
      pollJob();
      pollTimer = setInterval(pollJob, 1000);
    }

    $("refreshBtn").onclick = refresh;
    $("syncBtn").onclick = async () => {
      try {
        await startJob("sync");
      } catch (err) {
        setStatus("convertStatus", err.message, true);
      }
    };
    $("convertBtn").onclick = async () => {
      setStatus("convertStatus", "");
      try {
        await startJob("convert", {
          bag: $("bagInput").value,
          output: $("mcapOutput").value,
          overwrite: $("overwrite").checked,
        });
      } catch (err) {
        setStatus("convertStatus", err.message, true);
      }
    };
    $("rerunBtn").onclick = async () => {
      setStatus("rerunStatus", "");
      try {
        await startJob("rerun", {
          bag: $("rerunInput").value,
          subsample: $("subsample").value,
          port: $("rerunPort").value,
          no_images: $("noImages").checked,
        });
        // Poll the job log until we find the viewer URL, then show it.
        const port = $("rerunPort").value || "9876";
        setStatus("rerunStatus", "Starting… (URL will appear here)");
        for (let i = 0; i < 120; i++) {       // up to 60 s
          await new Promise(r => setTimeout(r, 500));
          const data = await api(`/api/jobs/${activeJob}`);
          const line = (data.job.logs || []).find(
            l => l.includes("127.0.0.1") && l.includes("?url="));
          if (line) {
            const url = line.trim();
            $("rerunStatus").innerHTML =
              `Viewer ready — <a href="${url}" target="_blank" style="color:var(--accent)">open in browser</a><br><small style="color:var(--muted)">${url}</small>`;
            break;
          }
          if (data.job.status !== "running") {
            setStatus("rerunStatus", "Job finished (check job log for URL)");
            break;
          }
        }
      } catch (err) {
        setStatus("rerunStatus", err.message, true);
      }
    };
    $("checkBtn").onclick = async () => {
      setStatus("checkStatus", "");
      try {
        await startJob("check", {
          bag: $("checkInput").value,
          no_smoothness: $("checkNoSmoothness").checked,
          no_static: $("checkNoStatic").checked,
          json: $("checkJson").checked,
        });
      } catch (err) {
        setStatus("checkStatus", err.message, true);
      }
    };
    $("saveBtn").onclick = async () => {
      setStatus("saveStatus", "");
      try {
        await startJob("rerun_save", {
          bag: $("saveInput").value,
          subsample: $("saveSubsample").value,
          overwrite: $("saveOverwrite").checked,
          no_images: $("saveNoImages").checked,
          no_robot: $("saveNoRobot").checked,
          show_collision: $("saveShowCollision").checked,
          strict: $("saveStrict").checked,
        });
      } catch (err) {
        setStatus("saveStatus", err.message, true);
      }
    };
    $("jobSelect").onchange = () => {
      activeJob = $("jobSelect").value;
      startPolling();
    };
    $("stopBtn").onclick = async () => {
      if (!activeJob) return;
      await api(`/api/jobs/${activeJob}/stop`, {method: "POST"});
      await pollJob();
    };

    refresh();
    loadJobs();
  </script>
</body>
</html>
"""


def _flask_error(message: str, status: int = 400):
    response = jsonify({"error": message})
    response.status_code = status
    return response


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return INDEX_HTML

    @app.get("/api/bags")
    def bags():
        return jsonify({"bags": scan_bags(), "mcaps": scan_mcaps(),
                        "rrds": scan_rrds()})

    @app.get("/api/jobs")
    def jobs():
        return jsonify({"jobs": JOBS.latest()})

    @app.get("/api/jobs/<job_id>")
    def job_detail(job_id: str):
        job = JOBS.get(job_id)
        if job is None:
            return _flask_error("Unknown job.", 404)
        return jsonify({"job": job.snapshot()})

    @app.post("/api/jobs")
    def start_job():
        payload = request.get_json(silent=True) or {}
        action = payload.get("action")
        try:
            if action == "convert":
                job = build_convert_job(payload)
            elif action == "sync":
                job = build_sync_job()
            elif action == "rerun":
                job = build_rerun_job(payload)
            elif action == "rerun_save":
                job = build_rerun_save_job(payload)
            elif action == "check":
                job = build_check_job(payload)
            else:
                raise ValueError("Unknown job action.")
        except Exception as exc:
            return _flask_error(str(exc))
        return jsonify({"job": job.snapshot()})

    @app.post("/api/jobs/<job_id>/stop")
    def stop_job(job_id: str):
        job = JOBS.get(job_id)
        if job is None:
            return _flask_error("Unknown job.", 404)
        job.stop()
        return jsonify({"job": job.snapshot()})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind host, use 0.0.0.0 inside Docker.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    parser.add_argument(
        "--docker-container",
        default="",
        help="Run conversion/Rerun/sync jobs through docker exec in this container.",
    )
    parser.add_argument(
        "--docker-repo",
        default="/workspace",
        help="Repo path inside the Docker container.",
    )
    parser.add_argument(
        "--docker-bin",
        default="docker",
        help='Docker command prefix. Use "sudo docker" if your host requires sudo.',
    )
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    args = parser.parse_args()

    EXECUTION["docker_container"] = args.docker_container
    EXECUTION["docker_repo"] = args.docker_repo
    EXECUTION["docker_bin"] = shlex.split(args.docker_bin)

    BAGS_ROOT.mkdir(parents=True, exist_ok=True)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    print(f"[rosbag_gui] Repo: {REPO_ROOT}", flush=True)
    print(f"[rosbag_gui] Bags: {BAGS_ROOT}", flush=True)
    if EXECUTION["docker_container"]:
        print(
            f"[rosbag_gui] Jobs: docker exec {EXECUTION['docker_container']} "
            f"(repo {EXECUTION['docker_repo']})",
            flush=True,
        )
    else:
        print("[rosbag_gui] Jobs: host Python", flush=True)
    print(f"[rosbag_gui] Open: http://{shown_host}:{args.port}", flush=True)

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
