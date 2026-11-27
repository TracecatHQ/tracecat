from enum import StrEnum


class AuthType(StrEnum):
    BASIC = "basic"
    OIDC = "oidc"
    SAML = "saml"


class SpecialUserID(StrEnum):
    """A sentinel user ID that represents the current user."""

    CURRENT = "current"


class CredentialChangeErrorCode(StrEnum):
    """Why an email or password change was refused."""

    PASSWORD_LOGIN_NOT_ALLOWED = "password_login_not_allowed"
    INVALID_CURRENT_PASSWORD = "invalid_current_password"
    INVALID_EMAIL = "invalid_email"
    MEMBER_OF_OTHER_ORGANIZATION = "member_of_other_organization"


class AuthErrorCode(StrEnum):
    """Machine-readable codes for login failures surfaced to the sign-in UI."""

    SAML_ENFORCED = "saml_enforced"
