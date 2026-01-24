"""Entry point for Tracecat Worker Pool Operator."""

import logging

import kopf


def configure_logging() -> None:
    """Configure logging for the operator."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> None:
    """Run the operator."""
    configure_logging()

    # Import handlers to register them with kopf
    from . import handlers  # noqa: F401

    # Run kopf with default settings
    kopf.run()


if __name__ == "__main__":
    main()
