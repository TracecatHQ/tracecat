# STS session abuse

Use when temporary credentials or role assumptions are implicated. Keep the
issuing principal, assumed role, resulting session, and any subsequent role
assumptions distinct; disabling the initial credential does not close every hop.

## Reconstruct the identity path

Correlate available STS issuance events with subsequent activity using principal
identifiers, session issuer, access-key IDs, issuance times, source identity,
and request context. A role session name alone is insufficient attribution.
Record each account and region searched and gaps in cross-account visibility.

Build a sequence of issuer → session → resource actions → subsequent assumptions.
Check whether the observed entry point was a long-term key, workload identity,
SAML/OIDC federation, or another session. Correlate with the identity provider
when that source is available and authorized. Verify trust-policy or permission
changes that enabled continued access; do not infer them from a session name.

Evaluate access from both identity and resource policies. Resource grants may
matter even when an identity permission is removed. For Identity Center-managed
roles, use its session-revocation procedure rather than assuming an editable
ordinary IAM role policy.

## Choose the containment scope

Temporary credentials have an expiry, but waiting for expiry can leave active
access. AWS evaluates their permissions on requests, so approved permission
changes or explicit denies can constrain existing credentials. Allow for policy
propagation and verify effectiveness against the affected path.

IAM role-session revocation uses a deny policy with a token-issuance cutoff. It
can affect legitimate sessions issued before that cutoff. Explain that impact,
check the relevant role type, and record the cutoff before recommending it.
Closing the original issuance path is also necessary: sessions issued after the
cutoff may otherwise remain usable. Investigate each downstream role separately;
do not assume revoking one role's sessions revokes a chain of different roles.

Avoid weakening an incident deny merely to restore a workload. Determine the
intended recovery state, legitimate reauthentication path, and remaining
resource-policy exposure before proposing removal of containment controls.

## Evidence to hand off

Include the identity/session graph, issuance and expiry evidence where available,
affected resources, continued issuance paths, proposed containment scope, observed
verification results, and identities whose activity remains unknown.

## Authoritative references

- [Permissions for temporary credentials and resource-policy grants](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_control-access_disable-perms.html).
- [IAM role-session revocation and its cutoff](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_revoke-sessions.html).
