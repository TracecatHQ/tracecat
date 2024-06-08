import rich
import typer
from dotenv import find_dotenv, load_dotenv

from tracecat.auth.credentials import Role

if not load_dotenv(find_dotenv()):
    rich.print("[red]No .env file found[/red]")
    raise typer.Exit()


# In reality we should use the user's id from config.toml

ROLE: Role = Role(
    type="service", user_id="default-tracecat-user", service_id="tracecat-cli"
)
