"""Tests for ScriptValidator - the AST-based script validation for safe Python execution."""

import pytest

from tracecat.sandbox.safe_executor import (
    NETWORK_MODULES,
    SAFE_STDLIB_MODULES,
    SYSTEM_ACCESS_MODULES,
    ScriptValidator,
)


class TestScriptValidatorBasics:
    """Test basic ScriptValidator functionality."""

    def test_empty_script(self):
        """Test that an empty script is valid."""
        validator = ScriptValidator()
        errors = validator.validate("")
        assert errors == []

    def test_simple_function(self):
        """Test that a simple function is valid."""
        script = """
def main():
    return 42
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert errors == []

    def test_syntax_error(self):
        """Test that syntax errors are caught."""
        script = """
def main(
    return 42
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) == 1
        assert "Syntax error" in errors[0]


class TestSafeStdlibModules:
    """Test that safe stdlib modules are allowed."""

    @pytest.mark.parametrize(
        "module",
        [
            "json",
            "re",
            "datetime",
            "base64",
            "hashlib",
            "math",
            "collections",
            "itertools",
            "functools",
            "copy",
            "uuid",
            "enum",
            "dataclasses",
            "typing",
            "string",
            "textwrap",
            "decimal",
            "fractions",
            "random",
            "statistics",
            "calendar",
            "time",
            "zoneinfo",
            "csv",
            "binascii",
            "hmac",
            "secrets",
            "unicodedata",
            "operator",
            "pprint",
            "html",
            "gzip",
            "zipfile",
            "zlib",
            "contextlib",
            "warnings",
            "logging",
            "traceback",
            "abc",
            "numbers",
            "difflib",
            "fnmatch",
            "struct",
            "io",
            # NOTE: "inspect" is intentionally NOT included - it enables sandbox escape
            # via frame introspection (inspect.currentframe().f_back.f_globals)
        ],
    )
    def test_safe_stdlib_import_allowed(self, module):
        """Test that importing safe stdlib modules is allowed."""
        script = f"import {module}"
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert errors == [], f"Expected {module} to be allowed, got errors: {errors}"

    def test_safe_stdlib_from_import_allowed(self):
        """Test that from-imports from safe stdlib are allowed."""
        script = """
from json import dumps, loads
from datetime import datetime, timedelta
from collections import defaultdict
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert errors == []

    def test_safe_submodule_import(self):
        """Test that submodule imports from safe modules are allowed."""
        script = """
import xml.etree.ElementTree
from urllib.parse import quote, urlencode
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert errors == []


class TestSystemAccessModulesBlocked:
    """Test that system access modules are blocked."""

    @pytest.mark.parametrize(
        "module",
        [
            "os",
            "sys",
            "subprocess",
            "multiprocessing",
            "threading",
            "signal",
            "resource",
            "ctypes",
            "pickle",
            "marshal",
            "shutil",
            "tempfile",
            "pathlib",
            "glob",
        ],
    )
    def test_system_module_import_blocked(self, module):
        """Test that importing system modules is blocked."""
        script = f"import {module}"
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) == 1
        assert module in errors[0]
        assert (
            "system module" in errors[0].lower() or "not allowed" in errors[0].lower()
        )

    def test_os_from_import_blocked(self):
        """Test that from-imports from os are blocked."""
        script = "from os import environ, getcwd"
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) == 1
        assert "os" in errors[0]

    def test_sys_from_import_blocked(self):
        """Test that from-imports from sys are blocked."""
        script = "from sys import exit, path"
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) == 1
        assert "sys" in errors[0]


class TestNetworkModulesBlocked:
    """Test that network modules are blocked by default."""

    @pytest.mark.parametrize(
        "module",
        [
            "socket",
            "socketserver",
            "http",
            "http.client",
            "http.server",
            "urllib.request",
            "urllib.error",
            "ftplib",
            "smtplib",
            "ssl",
            "asyncio",
        ],
    )
    def test_network_module_blocked_by_default(self, module):
        """Test that network modules are blocked when allow_network=False."""
        script = f"import {module}"
        validator = ScriptValidator(allow_network=False)
        errors = validator.validate(script)
        assert len(errors) >= 1
        assert any(
            "network" in e.lower() or "not allowed" in e.lower() for e in errors
        ), f"Expected network module {module} to be blocked, got: {errors}"

    @pytest.mark.parametrize(
        "module",
        [
            "socket",
            "http.client",
            "asyncio",
        ],
    )
    def test_network_module_allowed_when_enabled(self, module):
        """Test that network modules are allowed when allow_network=True."""
        script = f"import {module}"
        validator = ScriptValidator(allow_network=True)
        errors = validator.validate(script)
        # Network modules should be allowed, but system modules should still be blocked
        # Some network modules may still be blocked if they're in SYSTEM_ACCESS_MODULES
        if module not in SYSTEM_ACCESS_MODULES:
            assert errors == [], (
                f"Expected {module} to be allowed with allow_network=True"
            )


class TestDependenciesAllowed:
    """Test that declared dependencies are allowed."""

    def test_dependency_import_allowed(self):
        """Test that importing a declared dependency is allowed."""
        script = "import requests"
        validator = ScriptValidator(allowed_dependencies={"requests"})
        errors = validator.validate(script)
        assert errors == []

    def test_dependency_from_import_allowed(self):
        """Test that from-imports from dependencies are allowed."""
        script = "from requests import get, post"
        validator = ScriptValidator(allowed_dependencies={"requests"})
        errors = validator.validate(script)
        assert errors == []

    def test_dependency_submodule_allowed(self):
        """Test that submodule imports from dependencies are allowed."""
        script = """
import requests.auth
from requests.adapters import HTTPAdapter
"""
        validator = ScriptValidator(allowed_dependencies={"requests"})
        errors = validator.validate(script)
        assert errors == []

    def test_undeclared_dependency_blocked(self):
        """Test that importing an undeclared dependency is blocked."""
        script = "import pandas"
        validator = ScriptValidator(allowed_dependencies={"requests"})
        errors = validator.validate(script)
        assert len(errors) == 1
        assert "pandas" in errors[0]

    def test_multiple_dependencies_allowed(self):
        """Test that multiple declared dependencies are allowed."""
        script = """
import requests
import pandas
import numpy
"""
        validator = ScriptValidator(
            allowed_dependencies={"requests", "pandas", "numpy"}
        )
        errors = validator.validate(script)
        assert errors == []

    def test_hyphenated_dependency_allowed(self):
        """Test that hyphenated package names are handled correctly."""
        script = "import py_ocsf_models"
        # Package name is py-ocsf-models but import is py_ocsf_models
        validator = ScriptValidator(allowed_dependencies={"py_ocsf_models"})
        errors = validator.validate(script)
        assert errors == []


class TestOsEnvironBlocked:
    """Test that os.environ access is blocked."""

    def test_os_environ_attribute_blocked(self):
        """Test that os.environ attribute access is detected."""
        script = """
import os
x = os.environ
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        # Should have errors for both import and environ access
        assert len(errors) >= 1
        assert any("os" in e.lower() for e in errors)

    def test_os_environ_subscript_blocked(self):
        """Test that os.environ["KEY"] access is detected."""
        script = """
import os
x = os.environ["HOME"]
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) >= 1

    def test_os_environ_get_blocked(self):
        """Test that os.environ.get() is detected."""
        script = """
import os
x = os.environ.get("HOME")
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) >= 1


class TestComplexScripts:
    """Test validation of more complex scripts."""

    def test_valid_complex_script(self):
        """Test that a complex valid script passes validation."""
        script = """
import json
import re
from datetime import datetime
from collections import defaultdict

def process_data(data):
    result = defaultdict(list)
    for item in data:
        if re.match(r"^[a-z]+$", item.get("name", "")):
            result[item["category"]].append(item)
    return dict(result)

def main(input_data):
    processed = process_data(input_data)
    timestamp = datetime.now().isoformat()
    return {
        "processed": processed,
        "timestamp": timestamp,
        "count": len(processed),
    }
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert errors == []

    def test_script_with_dependency(self):
        """Test a script that uses a declared dependency."""
        script = """
import json
import requests

def main(url):
    response = requests.get(url)
    return response.json()
"""
        validator = ScriptValidator(allowed_dependencies={"requests"})
        errors = validator.validate(script)
        assert errors == []

    def test_multiple_violations(self):
        """Test that multiple violations are all reported."""
        script = """
import os
import sys
import subprocess
"""
        validator = ScriptValidator()
        errors = validator.validate(script)
        assert len(errors) == 3
        assert any("os" in e for e in errors)
        assert any("sys" in e for e in errors)
        assert any("subprocess" in e for e in errors)


class TestModuleConstants:
    """Test the module constant sets."""

    def test_safe_stdlib_modules_is_frozenset(self):
        """Test that SAFE_STDLIB_MODULES is a frozenset."""
        assert isinstance(SAFE_STDLIB_MODULES, frozenset)

    def test_network_modules_is_frozenset(self):
        """Test that NETWORK_MODULES is a frozenset."""
        assert isinstance(NETWORK_MODULES, frozenset)

    def test_system_access_modules_is_frozenset(self):
        """Test that SYSTEM_ACCESS_MODULES is a frozenset."""
        assert isinstance(SYSTEM_ACCESS_MODULES, frozenset)

    def test_no_overlap_safe_and_system(self):
        """Test that safe and system modules don't overlap."""
        overlap = SAFE_STDLIB_MODULES & SYSTEM_ACCESS_MODULES
        assert overlap == set(), f"Unexpected overlap: {overlap}"

    def test_no_overlap_safe_and_network(self):
        """Test that safe and network modules don't overlap."""
        overlap = SAFE_STDLIB_MODULES & NETWORK_MODULES
        assert overlap == set(), f"Unexpected overlap: {overlap}"

    def test_key_modules_in_correct_sets(self):
        """Test that key modules are in the correct sets."""
        # Safe modules
        assert "json" in SAFE_STDLIB_MODULES
        assert "datetime" in SAFE_STDLIB_MODULES
        assert "re" in SAFE_STDLIB_MODULES
        assert "base64" in SAFE_STDLIB_MODULES

        # System modules
        assert "os" in SYSTEM_ACCESS_MODULES
        assert "sys" in SYSTEM_ACCESS_MODULES
        assert "subprocess" in SYSTEM_ACCESS_MODULES

        # Network modules
        assert "socket" in NETWORK_MODULES
        assert "http" in NETWORK_MODULES
        assert "asyncio" in NETWORK_MODULES
