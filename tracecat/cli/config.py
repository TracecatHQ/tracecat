from tracecat.auth import Role

USER = "default-tracecat-user"
SECRET = "test-secret"

ROLE: Role = Role(type="service", user_id=USER, service_id="tracecat-runner")
