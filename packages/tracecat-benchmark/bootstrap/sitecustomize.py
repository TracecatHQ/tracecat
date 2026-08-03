"""Enable benchmark-only instrumentation before Tracecat imports."""

from tracecat_benchmark.pool_metrics import install_pool_metrics_instrumentation

install_pool_metrics_instrumentation()
