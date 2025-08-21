#!/usr/bin/env python3
"""Ray maintenance script for cleaning up logs and monitoring disk usage."""

import argparse
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tracecat.executor.monitoring import (
    cleanup_ray_logs,
    monitor_ray_disk_usage,
    check_ray_disk_usage,
    log_disk_usage_warning,
)
from tracecat.logger import logger


def main():
    parser = argparse.ArgumentParser(description="Ray maintenance utilities")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor Ray disk usage")
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up old Ray logs")
    cleanup_parser.add_argument(
        "--max-age-hours", 
        type=int, 
        default=24,
        help="Maximum age of Ray session directories to keep (default: 24 hours)"
    )
    cleanup_parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    
    # Check command  
    check_parser = subparsers.add_parser("check", help="Check disk usage and exit with code based on status")
    check_parser.add_argument(
        "--threshold",
        type=float,
        default=90.0,
        help="Disk usage threshold percentage for warnings (default: 90.0)"
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        if args.command == "monitor":
            monitor_ray_disk_usage()
            return 0
            
        elif args.command == "cleanup":
            deleted_count = cleanup_ray_logs(
                max_age_hours=args.max_age_hours,
                dry_run=args.dry_run
            )
            if deleted_count > 0:
                action = "Would delete" if args.dry_run else "Deleted"
                logger.info(f"{action} {deleted_count} old Ray session directories")
            else:
                logger.info("No old Ray session directories found to clean up")
            return 0
            
        elif args.command == "check":
            usage_info = check_ray_disk_usage()
            has_critical_usage = log_disk_usage_warning(usage_info, threshold=args.threshold)
            
            if has_critical_usage:
                logger.error(f"Disk usage exceeds {args.threshold}% threshold")
                return 2  # Critical
            
            # Check for moderate usage (80%)
            moderate_threshold = min(80.0, args.threshold - 10.0)
            has_moderate_usage = log_disk_usage_warning(usage_info, threshold=moderate_threshold)
            
            if has_moderate_usage:
                logger.warning(f"Disk usage exceeds {moderate_threshold}% threshold")
                return 1  # Warning
            
            logger.info("Disk usage is within acceptable limits")
            return 0  # OK
            
    except Exception as e:
        logger.error(f"Error running {args.command}", error=e)
        return 3  # Error


if __name__ == "__main__":
    sys.exit(main())