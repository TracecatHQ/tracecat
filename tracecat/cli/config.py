from dotenv import find_dotenv, load_dotenv

from tracecat.auth.credentials import Role

load_dotenv(find_dotenv())

# In reality we should use the user's id from config.toml
USER = "default-tracecat-user"

ROLE: Role = Role(type="service", user_id=USER, service_id="tracecat-runner")
