# Security Policy

## Supported versions

We always recommend using the latest version of Tracecat to ensure you get all security updates.

## Security Features

The following security features are available in Tracecat open source:
- SAML SSO
- Audit logs
- Workspaces to isolate resources
- `nsjail` sandbox or `pid` runtime for isolated code and agent execution

> [!NOTE]
> `nsjail` is the recommended executor runtime for production deployments. We do not accept reports related to "breakout" in the `pid` runtime using the `UnsafePidExecutor`.
> `nsjail` is enabled by default for Helm chart / Kubernetes deployments only and must be explicitly enabled in other deployment options.

## Reporting Vulnerabilities

> [!IMPORTANT]
> Please do not file GitHub issues or post on our public forum for security vulnerabilities, as they are public!
> As part of responsible disclosure, please report any security problems to us before disclosing it publicly.

If you are a security researcher and have discovered a vulnerability, please follow the steps below:

1. Open a new [security advisory](https://github.com/TracecatHQ/tracecat/security/advisories/new) in GitHub.
2. Our security team will get back to you as soon as possible.
3. We will review the vulnerability and determine if it is a valid security issue.
4. If it is a valid security issue, we will work with you to reproduce and fix it.

All reports are reviewed within 24 hours. Timeline for the fix is dependent on the severity of the vulnerability.
Bounties and [exclusive Tracecat merch](https://tracecat-shop.fourthwall.com/products/tracecat-bounty-hunter) may be offered depending on the severity of the vulnerability.
