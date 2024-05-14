<div align="center">
  <h2>
    Open source Tines / Splunk SOAR alternative
  </h2>
  <img src="img/banner.svg" alt="tracecat">
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
[![Discord](https://img.shields.io/discord/1212548097624903681.svg?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/n3GF4qxFU8)

</div>

_Disclaimer: Tracecat is currently in public beta. If you'd like to use Tracecat in production, please reach out to us on Discord or founders@tracecat.com!_
_Want to take Tracecat for a spin? Try out our [tutorials](https://docs.tracecat.com/quickstart) with [Tracecat Cloud](https://platform.tracecat.com) or [self-hosted](https://docs.tracecat.com/installation)._

[Tracecat](https://tracecat.com) is an open source automation platform for security teams. We're building the features of Tines / Splunk SOAR with enterprise-grade open source tools.

It's designed to be simple but powerful. Security automation should be accessible to everyone, ~~including~~ especially understaffed small-to-mid sized teams.

Check out our [quickstart](https://docs.tracecat.com/quickstart) and build your first AI workflow in 15 minutes.
The easiest way to get started is to sign-up for [Tracecat Cloud](https://platform.tracecat.com).
We also support [self-hosted](https://docs.tracecat.com/installation) Tracecat.

> [!NOTE]
> SOAR [(Security Orchestration, Automation and Response)](https://www.gartner.com/en/information-technology/glossary/security-orchestration-automation-response-soar) refers to technologies that enable organizations to automatically collect and respond to alerts across different security tooling (e.g. Crowdstrike, Microsoft Defender, SIEM) and data sources (e.g. AWS CloudTrail, Okta system logs).

### Build SecOps Automations
<img src="https://github.com/TracecatHQ/tracecat/blob/main/img/workflow.png" width="100%" />

### Manage Cases with AI Tagging
<img src="https://github.com/TracecatHQ/tracecat/blob/main/img/cases.gif" width="100%" />

## Getting started

Let's automate a phishing email investigation, collect evidence, and generate a remediation plan using AI.
You can follow the [tutorial here](https://docs.tracecat.com/quickstart).

https://github.com/TracecatHQ/tracecat/assets/46541035/580149cf-624b-4815-a62a-e59bbf61280e

## Features

Build AI-assisted workflows, enrich alerts, and close cases fast.

- Workflows
  - [x] Drag-and-drop builder
  - [x] Core primitives (webhook, HTTP, if-else, send email, etc.)
  - [x] AI Actions (label, summarize, enrich etc.)
  - [x] Secrets
  - [x] Integrations
  - [ ] Playbooks
  - [ ] Formulas (expected May 2024)
  - [ ] Versioning (expected June 2024)
- Case management
  - [x] [SMAC (status, malice, action, context)](https://www.rapid7.com/blog/post/2021/02/12/talkin-smac-alert-labeling-and-why-it-matters/)
  - [x] Suppression
  - [x] Deduplication
  - [x] AI-assisted labelling (e.g. MITRE ATT&CK)
  - [ ] Metrics dashboard
- Data validation
  - [x] [Pydantic V2](https://github.com/pydantic/pydantic) for fast data model and input / output validation in the backend
  - [x] [Zod](https://github.com/colinhacks/zod) for fast form and input / output validation in the frontend
- Teams
  - [x] Single-tenancy
  - [ ] Collaboration
- AI infrastructure
  - [x] VectorDB for alert contextualization / enrichment
  - [ ] LLM evaluation  security
  - [ ] Bring-your-own LLM (OpenAI, Mistral, Anthropic etc.)

Tracecat is **not** a 1-to-1 mapping of Tines / Splunk SOAR. Our aim is to give technical teams a Tines-like experience, but with a focus on open source, alerts triage, unified APIs, and AI features.

## Installation

Tracecat is Cloud agnostic and deploys anywhere that supports Docker.
Learn how to [install Tracecat locally](https://docs.tracecat.com/installation).

- [x] Docker Compose
- [x] AWS
- [ ] Azure
- [ ] GCP

## Status

- [x] Public Alpha: Anyone can sign up over at [tracecat.com](https://tracecat.com) but go easy on us, there are kinks and we are just getting started.
- [x] Public Beta: Stable enough for most non-enteprise use-cases
- [ ] Public: Production-ready

We're currently in Public Alpha.

## Community & Support

Join us in building a newer, more open, kind of security automation platform.

- [Tracecat Discord](https://discord.gg/n3GF4qxFU8) for hanging out with the community
- [GitHub issues](https://github.com/TracecatHQ/tracecat/issues)

## Unified Integrations Model

New integrations and out-of-the-box playbooks will be prioritized according to user feedback. If you've got any suggestions, please let us know on [Discord](https://discord.gg/n3GF4qxFU8) 🦾.

## Security

Please do not file GitHub issues or post on our public forum for security vulnerabilities, as they are public!

Tracecat takes security issues very seriously. If you have any concerns about Tracecat or believe you have uncovered a vulnerability, please get in touch via the e-mail address security@tracecat.com. In the message, try to provide a description of the issue and ideally a way of reproducing it. The security team will get back to you as soon as possible.

Note that this security address should be used only for undisclosed vulnerabilities. Please report any security problems to us before disclosing it publicly.

## License

Copyright (c) 2024 Tracecat

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
[GNU Affero General Public License](LICENSE) for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
