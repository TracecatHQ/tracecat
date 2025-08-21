# Ray Disk Management

This document describes the disk management improvements implemented to prevent Ray executor outages due to disk space issues.

## Background

Ray can generate significant amounts of log data and temporary files, which can fill up disk space and cause executor failures with errors like:

```
OSError: [Errno 28] No space left on device
RuntimeEnvSetupError: Failed to set up runtime environment
```

## Implemented Solutions

### 1. Ray Logging Configuration

Ray logging is now configured with reduced disk usage:

- **Log file size limit**: 1MB per log file (configurable via `RAY_ROTATION_MAX_BYTES`)
- **Log rotation**: Keep only 2 backup files (configurable via `RAY_ROTATION_BACKUP_COUNT`)
- **Log level**: ERROR level only to reduce verbosity (configurable via `RAY_BACKEND_LOG_LEVEL`)
- **Driver logging**: Disabled to reduce log volume

### 2. Enhanced Error Handling

The executor now provides better error messages for disk space issues:

- Detects disk space related errors (`"no space left"`, `"disk full"`, `"errno 28"`)
- Provides helpful context and suggestions in error messages
- Logs specific guidance for resolution

### 3. Disk Usage Monitoring

New monitoring capabilities:

- **Disk usage checks**: Monitor Ray temporary directories, UV cache, and system temp
- **Automated warnings**: Log warnings when disk usage exceeds configurable thresholds
- **Cleanup suggestions**: Identify old Ray session directories that can be cleaned up

### 4. Maintenance Tools

A maintenance script is provided for operational tasks:

```bash
# Monitor current disk usage
python scripts/ray-maintenance.py monitor

# Check disk usage and exit with status code
python scripts/ray-maintenance.py check --threshold 85.0

# Clean up old Ray logs (dry run)
python scripts/ray-maintenance.py cleanup --dry-run

# Clean up old Ray logs (actual cleanup)
python scripts/ray-maintenance.py cleanup --max-age-hours 12
```

## Configuration

### Environment Variables

The following environment variables can be used to configure Ray logging:

```bash
# Ray log file size (default: 1MB)
RAY_ROTATION_MAX_BYTES=1048576

# Number of backup log files to keep (default: 2)
RAY_ROTATION_BACKUP_COUNT=2

# Ray backend log level (default: 40 = ERROR)
# 10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL
RAY_BACKEND_LOG_LEVEL=40
```

### Docker Compose

All Docker compose files have been updated with these environment variables and sensible defaults.

## Monitoring and Alerts

### Disk Usage Thresholds

- **80%+**: Info level warnings
- **90%+**: Warning level alerts with suggestions
- **95%+**: Critical level alerts

### What to Monitor

1. **Ray session directories**: `/tmp/ray/session_*` or `/home/apiuser/.cache/tmp/ray/session_*`
2. **UV cache**: `/home/apiuser/.cache/uv`
3. **System temp**: `/tmp` or configured `TMPDIR`

## Troubleshooting

### If Disk Space Issues Occur

1. **Immediate Actions**:
   ```bash
   # Check current disk usage
   df -h
   
   # Check Ray-specific usage
   python scripts/ray-maintenance.py monitor
   
   # Clean up old Ray sessions
   python scripts/ray-maintenance.py cleanup
   ```

2. **Configuration Adjustments**:
   ```bash
   # Reduce Ray log retention
   export RAY_ROTATION_MAX_BYTES=524288  # 512KB
   export RAY_ROTATION_BACKUP_COUNT=1   # Keep only 1 backup
   
   # Restart the executor service
   docker-compose restart executor
   ```

3. **Long-term Solutions**:
   - Increase disk space allocation
   - Set up automated cleanup cron jobs
   - Implement disk usage alerting

### Common Error Patterns

#### Runtime Environment Setup Failure
```
RuntimeEnvSetupError: Failed to set up runtime environment
...
OSError: [Errno 28] No space left on device
```

**Solution**: Clean up Ray temporary directories and reduce log retention.

#### Ray Task Execution Failure
```
RayTaskError: ray::run_action_task() (pid=..., ip=...)
...
No space left on device
```

**Solution**: Same as above, plus check for any stuck or long-running tasks.

## Preventive Measures

### Automated Monitoring

Set up monitoring for disk usage:

```bash
# Add to cron for regular monitoring
0 */6 * * * /app/scripts/ray-maintenance.py check --threshold 85 || echo "Disk usage warning"
```

### Log Rotation

Ensure system log rotation is configured for Ray logs:

```bash
# Example logrotate configuration for Ray
/tmp/ray/session_*/logs/*.log {
    daily
    missingok
    rotate 3
    compress
    delaycompress
    notifempty
    maxsize 10M
}
```

### Resource Limits

Consider setting resource limits in Docker:

```yaml
executor:
  deploy:
    resources:
      limits:
        memory: 2G
      reservations:
        memory: 1G
```

## Best Practices

1. **Monitor regularly**: Use the monitoring tools to track disk usage trends
2. **Set up alerts**: Configure alerting when disk usage exceeds 80%
3. **Clean up proactively**: Run cleanup scripts regularly, not just when issues occur
4. **Size appropriately**: Ensure adequate disk space for your workload
5. **Test recovery**: Regularly test the cleanup and recovery procedures

## References

- [Ray Logging Configuration](https://docs.ray.io/en/latest/ray-observability/user-guides/configure-logging.html)
- [Ray Performance Tips](https://docs.ray.io/en/latest/data/performance-tips.html)