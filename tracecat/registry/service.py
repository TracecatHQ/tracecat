from tracecat_registry import Registry


class RegistryService:
    """Service for managing the registry.

    This wraps tracecat_registry.

    This is used by tracecat API and workers.
    Users:
    - Tracecat API:
        - List actions
        - Get action
        - Register action
        - Deregister action
    - Tracecat workers:
        - Get actions
        - Execute action
            - Need to wrap with auth sandbox
    """

    def __init__(self):
        self.registry = Registry()

    def list_actions(self):
        pass

    def register_action(self):
        pass

    def get_action(self, name: str):
        pass

    def deregister_action(self, name: str):
        pass

    def run_action(self, name: str, args: dict):
        pass

    def validate_args(self, name: str, args: dict):
        pass
