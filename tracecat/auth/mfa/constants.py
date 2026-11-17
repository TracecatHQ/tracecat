from typing import Final

ORG_MFA_REQUIRED_KEY: Final = "mfa_required"
"""Organization setting: members must sign in with a security key."""

PLATFORM_ADMIN_MFA_REQUIRED_KEY: Final = "platform_admin_mfa_required"
"""Platform setting: platform superusers must sign in with a security key."""

MFA_POLICY_CACHE_TTL_SECONDS: Final = 30
CHALLENGE_TTL_SECONDS: Final = 300
CHALLENGE_TIMEOUT_MS: Final = 120_000
RECOVERY_CODE_COUNT: Final = 10
MAX_CREDENTIALS_PER_USER: Final = 20

MFA_STEP_UP_REQUIRED_CODE: Final = "mfa_step_up_required"
MFA_RECENT_VERIFICATION_REQUIRED_CODE: Final = "mfa_recent_verification_required"
