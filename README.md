<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/banner-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="img/banner-light.svg">
    <img src="img/banner-light.svg" alt="The AI-native security automation platform." width="85%">
  </picture>
  <p align="center">
    The agentic security automation platform.
  </p>

  <br>
</div>

<div align="center">

![Commits](https://img.shields.io/github/commit-activity/m/TracecatHQ/tracecat?style=for-the-badge&logo=github&color=6E7ED8)
![License](https://img.shields.io/badge/License-AGPL%203.0-6E7ED8?style=for-the-badge&logo=agpl)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/H4XZwsYzY4)

</div>

## Introduction

[Tracecat](https://tracecat.com) is the open source security automation platform for teams and AI agents. A unified platform with everything AI-native security teams need to build agents and automate cyber defense.
## Core Features

<p align="center">Unlimited agents, cases, lookup tables, and workflows.</p>

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="img/readme/agents.gif" alt="An agent preset in Tracecat with its tools and skills, then a chat where the agent investigates a case by calling tools" width="100%"/>
      <p align="center"><b>Agents and skills</b> — build custom agents with prompts, tools, MCP, and skills</p>
    </td>
    <td width="50%" valign="top">
      <img src="img/readme/cases.gif" alt="The Tracecat case list, then a case with an agent-written verdict, timeline, IoCs, and evidence" width="100%"/>
      <p align="center"><b>Case management</b> — track, automate, and resolve incidents with agents</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="img/readme/workflows.gif" alt="A workflow DAG in the Tracecat builder, zoomed out to show every step, then an agent step opened for editing" width="100%"/>
      <p align="center"><b>Workflows</b> — execute deterministic logic with resilience and scale on Temporal</p>
    </td>
    <td width="50%" valign="top">
      <img src="img/readme/tables.gif" alt="Tracecat workspace tables, opening an entities table and an entity observations table" width="100%"/>
      <p align="center"><b>Tables</b> — store and query structured data</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="img/readme/mcp.gif" alt="A Claude Code session that calls Tracecat MCP tools to list and summarize the open critical cases" width="100%"/>
      <p align="center"><b>Tracecat MCP</b> — turn prompts into automations from Claude Code, Codex, Copilot, and more</p>
    </td>
    <td width="50%" valign="top">
      <img src="img/readme/integrations.gif" alt="Tracecat credentials, OAuth integrations, and the hosted MCP server catalog" width="100%"/>
      <p align="center"><b>Integrations</b> — 100+ pre-built connectors and 50+ hosted MCP servers for security tools</p>
    </td>
  </tr>
</table>

## Other Highlights

- **Pre-built MCP servers**: 50+ Tracecat-hosted MCP servers for security operations
- **MCP client**: connect custom agents any MCP server (remote HTTP / OAuth or local via `npx` / `uvx` commands)
- **Custom registry**: sync custom Python scripts from your Git repo into Tracecat
- **Sandboxed**: run untrusted code and agents within `nsjail` sandboxes or `pid` runtimes
- **Durable execution**: built on [Temporal](https://temporal.io) for resilience and scale
- **Variables**: reuse values across workflows and agents
- **No SSO tax**: SAML / OIDC support
- **Free audit logs**: exportable into your SIEM
- **Deploy anywhere**: sign up for Tracecat Cloud, or self-host with Docker, AWS Fargate, or Kubernetes. Runs fully [air-gapped](https://docs.tracecat.com/self-hosting/air-gapped).

## Enterprise Edition

- **Multi-tenant**: isoated different teams and dev / prod environments into workspaces
- **Fine-grained access control**: RBAC, ABAC, OAuth2.0 scopes for humans and agents
- **Human-in-the-loop**: review and approve sensitive tools calls from a unified inbox, Slack, or email
- **Workspace version control**: sync workflows, agents, and table schemas to GitHub, GitLab, Bitbucket, etc.
- **Metrics and monitoring**: for workflows, agents, and cases

## Open Source vs Enterprise

This repo is available under the [AGPL-3.0 license](https://github.com/TracecatHQ/tracecat/blob/main/LICENSE) except for:

- Code under the `packages/tracecat-ee` directory
- Code that gates `ee` features

These exceptions are fall under Tracecat's paid EE (Enterprise Edition) license. Code that fall under the above exceptions must not be redistributed, sold, used in production, or otherwise commercialized without permission.

> [!NOTE]
> Tracecat Enterprise is available as managed Cloud with US or EU hosting, or as a self-hosted deployment with dedicated support.
> [Book a demo today](https://www.tracecat.com/contact).

## Community

Have questions? Feedback? Come hang out with us in the [Tracecat Community Discord](https://discord.gg/H4XZwsYzY4).

## Tech Stack

- Backend: Python with FastAPI, SQLAlchemy, Pydantic, uv
- Frontend: Next.js with TypeScript, React Query, Shadcn UI
- Durable workflows and jobs: Temporal
- Sandbox: nsjail
- Database: PostgreSQL
- Object store: S3-compatible

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
