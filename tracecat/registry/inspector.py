"""Inspector module to extract registry action metadata from a package.

This module can be invoked as a script to inspect a package and output
JSON metadata for all registry actions (UDFs and templates) found within.

Usage:
    python -m tracecat.registry.inspector --module <package_name>
"""

import argparse
import logging
import sys
from pathlib import Path

from pydantic_core import to_json

from tracecat.registry.actions.models import (
    RegistryActionMetadata,
    TemplateAction,
)
from tracecat.registry.repository import (
    construct_module_name,
    import_and_reload,
    metadata_from_function,
    metadata_from_template,
    walk_module_py_files,
    walk_module_udfs,
    walk_module_yaml_files,
)

# Write to stderr in the subprocess
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def collect_udfs(package: str, origin: str) -> list[RegistryActionMetadata]:
    """Return serializable metadata objects for every UDF and template in the package.

    Args:
        package: The name of the package to inspect
        origin: The origin of the registry actions

    Returns:
        List of metadata dictionaries for all actions found
    """
    try:
        module = import_and_reload(package)
    except ImportError as e:
        logger.error(e)
        return []

    results: list[RegistryActionMetadata] = []

    # Get base path for the package
    if not hasattr(module, "__path__"):
        logger.error(f"Error: {package} is not a package")
        return []

    base_path = Path(module.__path__[0])
    base_package = module.__name__

    # Collect UDFs from Python files using the utility function
    for path in walk_module_py_files(module):
        # Convert path to module name
        module_name = construct_module_name(path, base_path, base_package)

        try:
            curr_mod = import_and_reload(module_name)

            # Find all UDF functions in the module using the utility function
            for name, fn in walk_module_udfs(curr_mod):
                try:
                    meta = metadata_from_function(
                        fn, origin=origin, module_name=module_name
                    )
                    logger.warning(f"Metadata: {meta}")
                    results.append(meta)
                except Exception as e:
                    logger.error(
                        f"Warning: Failed to extract metadata from {module_name}.{name}: {e}"
                    )

        except ImportError as e:
            logger.error(f"Warning: Could not import module {module_name}: {e}")
            continue

    # Collect template actions from YAML files using the utility function
    for file_path in walk_module_yaml_files(base_path, ignore="schemas"):
        try:
            template_action = TemplateAction.from_yaml(file_path)
            meta = metadata_from_template(template_action, origin)
            results.append(meta)
        except Exception as e:
            logger.error(f"Warning: Failed to parse template at {file_path}: {e}")

    return results


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the inspector CLI.

    This runs an instance of the tracecat python interpreter.
    We need to know the location of the venv to run the inspector in. (i.e. the sha)

    """
    parser = argparse.ArgumentParser(
        description="Inspect a package for Tracecat registry actions"
    )
    parser.add_argument(
        "--origin",
        required=True,
        help="The origin of the registry actions. i.e. git url",
    )
    parser.add_argument(
        "--package",
        default="custom_actions",
        help="The Python package to inspect (default: custom_actions)",
    )
    try:
        args = parser.parse_args(argv)
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    try:
        metadata = collect_udfs(args.package, args.origin)
        sys.stdout.write(to_json(metadata).decode("utf-8"))
        sys.exit(0)
    except Exception as e:
        logger.error(e)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
