"""
Robust FFmpeg Subprocess Execution & Crash Protection Engine for Gaming Uncut.
Brand: Gaming Uncut | Tagline: NO FILTER. JUST GAMEPLAY.

Protects the FastAPI server and worker threads from:
- Subprocess crashes (e.g. Windows STATUS_ACCESS_VIOLATION 0xc0000005)
- Unhandled CalledProcessError and TimeoutExpired exceptions
- CPU starvation by enforcing bounded thread limits (-threads 2)
- Memory ballooning from infinite capture_output
- Zombie / orphaned FFmpeg processes via process-tree termination
"""

import os
import sys
import time
import subprocess
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from backend.app.core.config import settings

logger = logging.getLogger("gaming_uncut.ffmpeg_executor")

# Human-readable mapping for common Windows NT status exit codes
WINDOWS_NT_STATUS_CODES: Dict[int, str] = {
    -1073741819: "0xC0000005 (STATUS_ACCESS_VIOLATION: memory segment fault / invalid pointer)",
    -1073741510: "0xC000013A (STATUS_CONTROL_C_EXIT: application terminated by CTRL+C)",
    -1073741515: "0xC0000135 (STATUS_DLL_NOT_FOUND: required dynamic library not found)",
    -1073740791: "0xC0000409 (STATUS_STACK_BUFFER_OVERRUN: stack buffer security check failed)",
    -1073741674: "0xC0000096 (STATUS_PRIVILEGED_INSTRUCTION: illegal instruction)",
}


@dataclass
class FFmpegExecutionResult:
    """Encapsulates the full result of a safe FFmpeg execution."""
    success: bool
    returncode: int
    stdout: str
    stderr: str
    error_message: Optional[str]
    duration_sec: float
    output_path: Optional[Path] = None

    def summary(self) -> str:
        if self.success:
            return f"FFmpeg succeeded in {self.duration_sec:.1f}s (code 0)"
        return f"FFmpeg failed in {self.duration_sec:.1f}s (code {self.returncode}): {self.error_message}"


def format_return_code(code: int) -> str:
    """Formats exit code with hexadecimal and human description."""
    if code == 0:
        return "0 (SUCCESS)"
    if code in WINDOWS_NT_STATUS_CODES:
        return f"{code} [{WINDOWS_NT_STATUS_CODES[code]}]"
    # Format negative signed int as hex
    unsigned = code & 0xFFFFFFFF
    return f"{code} (0x{unsigned:08X})"


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Safely kills an active subprocess and all of its spawned child processes."""
    if proc is None:
        return
    try:
        pid = proc.pid
        if sys.platform == "win32":
            # taskkill /F /T kills entire process tree
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        else:
            proc.kill()
    except Exception as e:
        logger.debug(f"Process tree termination notice: {e}")
    finally:
        try:
            proc.communicate(timeout=0.5)
        except Exception:
            pass


def safe_run_ffmpeg(
    cmd: List[str],
    timeout: float = 300.0,
    job_id: Optional[int] = None,
    threads: int = 2,
    output_path: Optional[Path] = None,
    min_output_bytes: int = 1024,
    log_name: Optional[str] = None
) -> FFmpegExecutionResult:
    """
    Executes an FFmpeg or FFprobe command with crash isolation:
    - Injects `-threads <N>` (default 2) to prevent CPU starvation.
    - Captures stderr in a bounded rolling buffer (preventing RAM spikes).
    - Terminates cleanly upon timeout without hanging the worker.
    - Catches all OS, Subprocess, and memory exceptions.
    - If output_path is provided, verifies output exists and size >= min_output_bytes.
    - Never raises unhandled exceptions.
    """
    safe_cmd = list(cmd)
    executable = Path(safe_cmd[0]).name.lower()

    # Automatically enforce thread constraint if not already specified
    if "ffmpeg" in executable and "-threads" not in safe_cmd:
        # Place -threads before output or after -y
        if "-y" in safe_cmd:
            idx = safe_cmd.index("-y") + 1
            safe_cmd[idx:idx] = ["-threads", str(threads)]
        else:
            safe_cmd[1:1] = ["-threads", str(threads)]

    start_time = time.time()
    stderr_lines: List[str] = []
    max_stderr_lines = 200

    # Ensure log directory exists
    logs_dir = settings.LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = logs_dir / f"ffmpeg_{log_name or (f'job_{job_id}' if job_id else 'exec')}_{int(start_time)}.log"

    proc: Optional[subprocess.Popen] = None
    try:
        # Check cancellation before starting
        if job_id:
            from backend.app.services.pipeline_service import pipeline_service
            if pipeline_service.is_cancellation_requested(job_id):
                return FFmpegExecutionResult(
                    success=False,
                    returncode=-1,
                    stdout="",
                    stderr="Job was cancelled prior to FFmpeg execution.",
                    error_message=f"Job #{job_id} was cancelled by user.",
                    duration_sec=0.0
                )

        logger.debug(f"Spawning FFmpeg subprocess: {' '.join(safe_cmd[:6])}...")
        proc = subprocess.Popen(
            safe_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )

        if job_id:
            from backend.app.services.pipeline_service import pipeline_service
            pipeline_service.register_subprocess(job_id, proc)

        # Non-blocking poll loop with timeout and cancellation check
        while True:
            # Check cancellation
            if job_id:
                from backend.app.services.pipeline_service import pipeline_service
                if pipeline_service.is_cancellation_requested(job_id):
                    logger.info(f"Cancellation detected during FFmpeg run for job #{job_id}. Killing process...")
                    kill_process_tree(proc)
                    return FFmpegExecutionResult(
                        success=False,
                        returncode=-15,
                        stdout="",
                        stderr="Process terminated due to job cancellation.",
                        error_message="Cancelled by user.",
                        duration_sec=time.time() - start_time
                    )

            # Check timeout
            elapsed = time.time() - start_time
            if timeout and elapsed > timeout:
                logger.error(f"FFmpeg subprocess timed out after {timeout}s (PID {proc.pid}). Terminating process tree...")
                kill_process_tree(proc)
                return FFmpegExecutionResult(
                    success=False,
                    returncode=-9,
                    stdout="",
                    stderr=f"FFmpeg execution timed out after {timeout}s.",
                    error_message=f"FFmpeg timed out after {timeout}s.",
                    duration_sec=elapsed
                )

            # Check if process has finished
            ret = proc.poll()
            if ret is not None:
                # Process finished, read remaining stderr
                remaining_err = proc.stderr.read() if proc.stderr else ""
                if remaining_err:
                    for line in remaining_err.splitlines():
                        stderr_lines.append(line)
                        if len(stderr_lines) > max_stderr_lines:
                            stderr_lines.pop(0)
                break

            # Read available stderr lines without blocking
            line = proc.stderr.readline() if proc.stderr else ""
            if line:
                clean_line = line.strip()
                stderr_lines.append(clean_line)
                if len(stderr_lines) > max_stderr_lines:
                    stderr_lines.pop(0)
            else:
                time.sleep(0.1)

        duration = time.time() - start_time
        full_stderr = "\n".join(stderr_lines)

        # Write log file for diagnostics
        try:
            log_file_path.write_text(full_stderr, encoding="utf-8", errors="replace")
        except Exception:
            pass

        # Handle return code
        if ret != 0:
            formatted_code = format_return_code(ret)
            # Extract last 3 lines for a concise error summary
            tail_err = "\n".join(stderr_lines[-5:]) if stderr_lines else "No stderr captured"
            err_msg = f"FFmpeg failed with exit code {formatted_code}: {tail_err}"
            logger.error(f"FFmpeg error: {err_msg}")
            
            # Record in crash registry
            from backend.app.video.worker import record_ffmpeg_error
            record_ffmpeg_error(job_id=job_id, exit_code=ret, error_message=err_msg, stderr=full_stderr[-2000:])

            return FFmpegExecutionResult(
                success=False,
                returncode=ret,
                stdout="",
                stderr=full_stderr,
                error_message=err_msg,
                duration_sec=duration
            )

        # Post-execution output verification
        if output_path is not None:
            if not output_path.exists():
                err_msg = f"FFmpeg returned code 0 but output file was not created: {output_path}"
                logger.error(err_msg)
                return FFmpegExecutionResult(
                    success=False,
                    returncode=-1,
                    stdout="",
                    stderr=full_stderr,
                    error_message=err_msg,
                    duration_sec=duration
                )
            size = output_path.stat().st_size
            if size < min_output_bytes:
                err_msg = f"FFmpeg output file is corrupt or too small ({size} bytes < {min_output_bytes}): {output_path}"
                logger.error(err_msg)
                return FFmpegExecutionResult(
                    success=False,
                    returncode=-1,
                    stdout="",
                    stderr=full_stderr,
                    error_message=err_msg,
                    duration_sec=duration
                )

        return FFmpegExecutionResult(
            success=True,
            returncode=0,
            stdout="",
            stderr=full_stderr,
            error_message=None,
            duration_sec=duration,
            output_path=output_path
        )

    except Exception as e:
        duration = time.time() - start_time
        err_msg = f"Unexpected exception running FFmpeg: {e}"
        logger.exception(err_msg)
        if proc:
            kill_process_tree(proc)

        from backend.app.video.worker import record_ffmpeg_error
        record_ffmpeg_error(job_id=job_id, exit_code=-999, error_message=err_msg, stderr=str(e))

        return FFmpegExecutionResult(
            success=False,
            returncode=-999,
            stdout="",
            stderr=str(e),
            error_message=err_msg,
            duration_sec=duration
        )
    finally:
        if job_id and proc:
            try:
                from backend.app.services.pipeline_service import pipeline_service
                pipeline_service.unregister_subprocess(job_id, proc)
            except Exception:
                pass


def cleanup_orphaned_ffmpeg_processes() -> int:
    """
    Finds and terminates any orphaned ffmpeg.exe or ffprobe.exe processes
    that might be lingering on Windows from previous crashes.
    """
    cleaned = 0
    if sys.platform != "win32":
        return cleaned

    try:
        import psutil
        current_pid = os.getpid()
        for p in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
            try:
                name = (p.info["name"] or "").lower()
                if name in ("ffmpeg.exe", "ffprobe.exe"):
                    ppid = p.info.get("ppid")
                    # If parent process no longer exists or parent is current_pid and process is zombie
                    if not psutil.pid_exists(ppid) or (time.time() - p.info["create_time"] > 600):
                        logger.warning(f"Killing orphaned FFmpeg process: PID {p.info['pid']} (name: {name})")
                        p.kill()
                        cleaned += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.debug(f"Orphan process cleanup notice: {e}")

    return cleaned
