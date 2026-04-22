"""Static AI SPM control catalog."""

from tracecat_ee.spm.controls.registry import (
    CONTROL_CATALOG,
    get_control,
    get_control_catalog,
)

__all__ = ("CONTROL_CATALOG", "get_control", "get_control_catalog")
