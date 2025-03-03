import re

from pydantic import EmailStr, TypeAdapter, ValidationError

# EMAIL
EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

EmailTypeAdapter = TypeAdapter(EmailStr)


def is_email(email: str) -> bool:
    """Check if a string is a valid email address."""
    try:
        EmailTypeAdapter.validate_python(email)
        return True
    except ValidationError:
        return False


def normalize_email(email: str) -> str:
    """Convert sub-addressed email to a normalized email address."""
    # This function:
    # 1. Converts the email to lowercase
    # 2. Removes the subaddress part (everything after + in the local part)

    # Example: User.Name+Newsletter@Example.COM -> user.name@example.com
    email = email.lower()
    local_part, domain = email.split("@")
    local_part = local_part.split("+")[0]
    return f"{local_part}@{domain}"


def extract_emails(text: str, normalize: bool = False) -> list[str]:
    """Extract unique emails from a string."""
    potential_emails = re.findall(EMAIL_REGEX, text)
    unique_emails = {email for email in potential_emails if is_email(email)}

    if normalize:
        unique_emails = {normalize_email(email) for email in unique_emails}

    return list(unique_emails)
