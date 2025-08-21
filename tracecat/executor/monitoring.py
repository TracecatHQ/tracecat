"""Disk space monitoring utilities for Ray executor."""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, NamedTuple

from tracecat.logger import logger


class DiskUsage(NamedTuple):
    """Disk usage information."""
    total: int
    used: int
    free: int
    
    @property
    def usage_percent(self) -> float:
        """Calculate usage percentage."""
        if self.total == 0:
            return 0.0
        return (self.used / self.total) * 100
    
    @property
    def free_percent(self) -> float:
        """Calculate free space percentage."""
        return 100.0 - self.usage_percent


def get_disk_usage(path: str) -> DiskUsage:
    """Get disk usage for a given path."""
    try:
        total, used, free = shutil.disk_usage(path)
        return DiskUsage(total=total, used=used, free=free)
    except OSError as e:
        logger.warning(f"Failed to get disk usage for {path}", error=e)
        return DiskUsage(total=0, used=0, free=0)


def check_ray_disk_usage() -> Dict[str, DiskUsage]:
    """Check disk usage for Ray-related directories."""
    paths_to_check = {}
    
    # Check temporary directory
    temp_dir = tempfile.gettempdir()
    paths_to_check["temp_dir"] = get_disk_usage(temp_dir)
    
    # Check Ray session directory if it exists
    ray_session_dir = None
    potential_ray_dirs = [
        "/tmp/ray",
        "/home/apiuser/.cache/tmp/ray",
        os.path.expanduser("~/.cache/tmp/ray"),
    ]
    
    for ray_dir in potential_ray_dirs:
        if os.path.exists(ray_dir):
            ray_session_dir = ray_dir
            break
    
    if ray_session_dir:
        paths_to_check["ray_session_dir"] = get_disk_usage(ray_session_dir)
        
        # Check for active Ray sessions
        try:
            ray_sessions = list(Path(ray_session_dir).glob("session_*"))
            if ray_sessions:
                # Check the most recent session
                latest_session = max(ray_sessions, key=os.path.getmtime)
                paths_to_check["latest_ray_session"] = get_disk_usage(str(latest_session))
        except Exception as e:
            logger.warning("Failed to check Ray session directories", error=e)
    
    # Check UV cache directory
    uv_cache_dir = os.environ.get("UV_CACHE_DIR", "/home/apiuser/.cache/uv")
    if os.path.exists(uv_cache_dir):
        paths_to_check["uv_cache_dir"] = get_disk_usage(uv_cache_dir)
    
    return paths_to_check


def log_disk_usage_warning(usage_info: Dict[str, DiskUsage], threshold: float = 90.0) -> bool:
    """Log warnings for disk usage above threshold. Returns True if any path exceeds threshold."""
    has_warning = False
    
    for path_name, usage in usage_info.items():
        if usage.usage_percent > threshold:
            has_warning = True
            logger.warning(
                f"High disk usage detected in {path_name}",
                usage_percent=f"{usage.usage_percent:.1f}%",
                free_space_gb=f"{usage.free / (1024**3):.2f}",
                total_space_gb=f"{usage.total / (1024**3):.2f}",
                path_name=path_name,
            )
        elif usage.usage_percent > 80.0:  # Info level warning
            logger.info(
                f"Moderate disk usage in {path_name}",
                usage_percent=f"{usage.usage_percent:.1f}%",
                free_space_gb=f"{usage.free / (1024**3):.2f}",
                path_name=path_name,
            )
    
    return has_warning


def cleanup_ray_logs(max_age_hours: int = 24, dry_run: bool = True) -> int:
    """Clean up old Ray log files. Returns number of files that would be/were deleted."""
    import time
    
    deleted_count = 0
    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    
    # Look for Ray session directories
    potential_ray_dirs = [
        "/tmp/ray",
        "/home/apiuser/.cache/tmp/ray", 
        os.path.expanduser("~/.cache/tmp/ray"),
    ]
    
    for ray_dir in potential_ray_dirs:
        if not os.path.exists(ray_dir):
            continue
            
        try:
            ray_path = Path(ray_dir)
            
            # Clean up old session directories
            for session_dir in ray_path.glob("session_*"):
                if not session_dir.is_dir():
                    continue
                    
                # Check if session is old enough
                if current_time - session_dir.stat().st_mtime > max_age_seconds:
                    # Check if there are any active processes using this session
                    try:
                        # Look for .pid files or other indicators of active sessions
                        pid_files = list(session_dir.glob("**/*.pid"))
                        if pid_files:
                            # Check if any PIDs are still active
                            active_pids = []
                            for pid_file in pid_files:
                                try:
                                    with open(pid_file) as f:
                                        pid = int(f.read().strip())
                                        if os.path.exists(f"/proc/{pid}"):
                                            active_pids.append(pid)
                                except (ValueError, FileNotFoundError):
                                    pass
                            
                            if active_pids:
                                logger.info(
                                    f"Skipping cleanup of active Ray session {session_dir.name}",
                                    active_pids=active_pids,
                                )
                                continue
                        
                        if dry_run:
                            logger.info(
                                f"Would delete old Ray session directory: {session_dir}",
                                age_hours=(current_time - session_dir.stat().st_mtime) / 3600,
                            )
                            deleted_count += 1
                        else:
                            logger.info(f"Deleting old Ray session directory: {session_dir}")
                            shutil.rmtree(session_dir)
                            deleted_count += 1
                            
                    except Exception as e:
                        logger.warning(
                            f"Failed to clean up Ray session directory {session_dir}",
                            error=e,
                        )
                        
        except Exception as e:
            logger.warning(f"Failed to access Ray directory {ray_dir}", error=e)
    
    if deleted_count > 0:
        action = "Would delete" if dry_run else "Deleted"
        logger.info(f"{action} {deleted_count} old Ray session directories")
    
    return deleted_count


def monitor_ray_disk_usage() -> None:
    """Monitor Ray disk usage and log warnings/suggestions."""
    logger.info("Checking Ray disk usage")
    
    usage_info = check_ray_disk_usage()
    
    # Log current disk usage
    for path_name, usage in usage_info.items():
        logger.info(
            f"Disk usage for {path_name}",
            usage_percent=f"{usage.usage_percent:.1f}%",
            free_space_gb=f"{usage.free / (1024**3):.2f}",
            total_space_gb=f"{usage.total / (1024**3):.2f}",
        )
    
    # Check for warnings
    has_critical_usage = log_disk_usage_warning(usage_info, threshold=90.0)
    
    if has_critical_usage:
        logger.error(
            "Critical disk usage detected. Consider the following actions:",
            suggestions=[
                "Reduce Ray log retention by setting RAY_ROTATION_MAX_BYTES and RAY_ROTATION_BACKUP_COUNT",
                "Clean up old Ray session directories",
                "Increase disk space allocation",
                "Monitor Ray temporary directory growth",
            ],
        )
        
        # Show potential cleanup
        cleanup_count = cleanup_ray_logs(max_age_hours=24, dry_run=True)
        if cleanup_count > 0:
            logger.info(
                f"Cleanup suggestion: {cleanup_count} old Ray session directories could be cleaned up"
            )