<div align="center">
  <img src="img/banner.svg" alt="The workflow orchestration platform for security engineers.">
</div>

</br>

<div align="center">

![License](https://img.shields.io/badge/License-AGPL%203.0-blue?style=for-the-badge&logo=agpl)
![Commit Activity](https://img.shields.io/github/commit-activity/m/TracecatHQ/tracecat?style=for-the-badge&logo=github)
[![Docs](https://img.shields.io/badge/Docs-available-blue?style=for-the-badge&logoColor=white)](https://docs.tracecat.com)

</div>

<div align="center">

![Next.js](https://img.shields.io/badge/next.js-%23000000.svg?style=for-the-badge&logo=next.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
[![Pydantic v2](https://img.shields.io/endpoint?style=for-the-badge&url=https://raw.githubusercontent.com/pydantic/pydantic/main/docs/badge/v2.json)](https://docs.pydantic.dev/latest/contributing/#badges)
![Tests](https://github.com/TracecatHQ/tracecat/actions/workflows/tests.yml/badge.svg)

</div>

[Tracecat](https://tracecat.com) is an open-source Tines / Splunk SOAR alternative for security engineers. We're building the features of Tines using enterprise-grade open-source tools.

- [x] Hosted [Temporal](https://github.com/temporalio/temporal) workflows
- [x] No-code workflow builder
- [x] Automations-as-code
  - [x] GitHub Actions-like YAML syntax. [Docs](https://docs.tracecat.com/concepts/workflow-definitions)
  - [x] Python-to-no-code compiler. [Docs](https://docs.tracecat.com/concepts/user-defined-functions)
  - [x] Version control
  - [ ] VSCode extension (coming soon)
- [x] Actions (HTTP requests, if-else, etc.). [Docs](https://docs.tracecat.com/concepts/actions)
- [x] Case Management. [Docs]()
- [x] Dashboard UI
- [x] Command-line interface
- [x] Integrations

Tracecat is not a 1-to-1 Tines / Splunk SOAR equivalent. We designed Tracecat to be the simpliest way for modern security teams to build, scale, and maintain workflows. Tracecat enables security practitioners to build automations using both:

- No-code drag-and-drop UI
- Configuration-as-code (e.g. Ansible / GitHub Actions)

No-code workflows are automatically synced into code, and vice versa. Tracecat extends the classic no-code [Security Orchestration, Automation and Response (SOAR)](https://www.gartner.com/en/information-technology/glossary/security-orchestration-automation-response-soar) experience with DevOps best-practices.

## Why Tracecat?

- **Security Operations (SecOps):** Unify workflow development across security engineering and SOC teams
- **Security Engineers (SecEng):** Build and maintain complex automations using open source integrations, configuration-as-code, and a powerful templating language
- **Managed Detection & Response (MDR):** Rapidly embed scalable workflow applications into any security product

## Highlights

<table>
  <tr>
    <td align="center" width="50%">
      <h4>Automate security workflows</h4>
      <img src="img/workflow.png" alt="Build security workflows" width="100%" />
    </td>
    <td align="center" width="50%">
      <h4>Close security cases fast with AI</h4>
      <img src="img/cases.gif" alt="Manage security cases with AI" width="100%" />
    </td>
  </tr>
</table>

## Getting Started

The easiest way to get started is to meet one of our cofounders on an open-source [onboarding call](https://calendly.com/d/cpfn-rsm-4t7/tracecat-onboarding). We'll help you install Tracecat self-hosted via `docker compose` and run your first workflow in 30 minutes.

More of a DIY hacker? Check out the self-serve [installation guide here](https://docs.tracecat.com/installation).

## Community & Support

- [Discord:](https://discord.gg/n3GF4qxFU8) seeking support, sharing new feature or integration ideas, and hanging out with the community.
- [GitHub issues:](https://github.com/TracecatHQ/tracecat/issues) bugs and errors you encounter with Tracecat.
- [Security:](https://github.com/TracecatHQ/tracecat?tab=security-ov-file) reporting security concerns and vulnerabilities.

## Documentation

- For full documentation, visit [https://docs.tracecat.com](https://docs.tracecat.com).
- For developers looking to create custom security apps, check out our [API Reference](https://docs.tracecat.com/api-reference/introduction).
- [Quickstart](https://docs.tracecat.com/quickstart): Deploy the classic threat intel workflow with VirusTotal in 15 minutes.

## Partner With Us

Tracecat is now open to MDRs and MSSPs. [Sign up](https://tracecat.com/#deal) over at our website or [book a call](https://calendly.com/meet-tracecat) with one of our cofounders.
