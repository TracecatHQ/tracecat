<div align="center">
  <img src="img/banner.svg" alt="The open source AI automation platform for agents and builders.">
</div>

</br>

<div align="center">

![Commits](https://img.shields.io/github/commit-activity/m/TracecatHQ/tracecat?style=for-the-badge&logo=github&color=6E7ED8)
![License](https://img.shields.io/badge/License-AGPL%203.0-6E7ED8?style=for-the-badge&logo=agpl)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/Hr4UWYEcTT)

</div>

[Tracecat](https://tracecat.com)

## Feature Highlights
- Agents:
- Workflows:
- Cases:
- Integrations:
- Custom Python: 
- Sandboxed-by-default: run untrusted code and agents within `nsjail` sandboxes or `pid` runtimes.
- No SSO tax: open source SAML / OIDC support and audit logs

Along with advanced features in our enterprise edition:
- Fine-grained access control (RBAC, ABAC, OAuth2.0 scopes) for humans and agents
- Workflow git sync

## Getting Started

> [!IMPORTANT]
> Tracecat is in active development. Expect breaking changes with releases. Review the release [changelog](https://github.com/TracecatHQ/tracecat/releases) before updating.

### Local deployment

Deploy a local Tracecat stack using Docker Compose. View full instructions [here](https://docs.tracecat.com/self-hosting/deployment-options/docker-compose).

```bash
# Setup environment variables and secrets
curl -o env.sh https://raw.githubusercontent.com/TracecatHQ/tracecat/1.0.0-beta.15/env.sh
curl -o .env.example https://raw.githubusercontent.com/TracecatHQ/tracecat/1.0.0-beta.15/.env.example
chmod +x env.sh && ./env.sh

# Download Caddyfile
curl -o Caddyfile https://raw.githubusercontent.com/TracecatHQ/tracecat/1.0.0-beta.15/Caddyfile

# Download Docker Compose file
curl -o docker-compose.yml https://raw.githubusercontent.com/TracecatHQ/tracecat/1.0.0-beta.15/docker-compose.yml

# Start Tracecat
docker compose up -d
```

### Cloud deployments

For production deployments, check out one of the following IaaC (Infrastructure as Code) options:

- Kubernetes (Helm chart) under [`deployments/helm`](https://github.com/TracecatHQ/tracecat/tree/main/deployments/helm)
- AWS ECS Fargate (Terraform) under [`deployments/fargate`](https://github.com/TracecatHQ/tracecat/tree/main/deployments/fargate)
- AWS EKS (Terraform) under [`deployments/eks`](https://github.com/TracecatHQ/tracecat/tree/main/deployments/eks)

## Tech

## Community

Have questions? Feedback? Come hang out with us in the [Tracecat Community Discord](https://discord.gg/H4XZwsYzY4).

## Open Source vs Enterprise

This repo is available under the AGPL-3.0 license with the following exceptions:

- `packages/tracecat-ee` directory is under Tracecat's paid EE (Enterprise Edition) license. This excludes extra security and monitoring features useful for larger organizations.
- `deployments/helm` and `deployments/eks` directory is under the source available [PolyForm Shield License](https://polyformproject.org/licenses/shield/1.0.0/). This allows you to use the Tracecat Helm chart and EKS deployment templates for internal use only.

Code within the above directories must not be redistributed, sold, or otherwise commercialized without permission.

*If you are interested in Tracecat's Enterprise License or managed Cloud offering, check out [our website](https://tracecat.com) or [book a meeting with us](https://cal.com/team/tracecat).*

## Contributors

Thank you all our amazing contributors for contributing code, integrations, docs, and support. Open source is only possible because of you.
Check out our [Contribution Guide](CONTRIBUTING.md) for more information.

<a href="https://github.com/TracecatHQ/tracecat/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=TracecatHQ/tracecat" />
</a>

<br>
<br>

<div align="center">

  <sub>**`Tracecat`** is distributed under [**AGPL-3.0**](https://github.com/TracecatHQ/tracecat/blob/main/LICENSE)</sub>

</div>
