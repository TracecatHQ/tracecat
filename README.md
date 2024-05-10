<div align="center">
  <h2>
    Open source Tines / Splunk SOAR alternative
  </h2>
  <img src="img/banner.svg" alt="tracecat">
</div>

</br>

<div align="center">

![License](https://img.shields.io/badge/License-Apache%202.0-blue?style=for-the-badge&logo=apache)
![Commit Activity](https://img.shields.io/github/commit-activity/m/TracecatHQ/tracecat?style=for-the-badge&logo=github)
[![Docs](https://img.shields.io/badge/Docs-available-blue?style=for-the-badge&logoColor=white)](https://docs.tracecat.com)

</div>

<div align="center">

![Next.js](https://img.shields.io/badge/next.js-%23000000.svg?style=for-the-badge&logo=next.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
[![Pydantic v2](https://img.shields.io/endpoint?style=for-the-badge&url=https://raw.githubusercontent.com/pydantic/pydantic/main/docs/badge/v2.json)](https://docs.pydantic.dev/latest/contributing/#badges)
[![Discord](https://img.shields.io/discord/1212548097624903681.svg?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/n3GF4qxFU8)

</div>

_Disclaimer: Tracecat is currently in public alpha. If you'd like to use Tracecat in production, please reach out to us on Discord or founders@tracecat.com!_
_Want to take Tracecat for a spin? Try out our [tutorials](https://docs.tracecat.com/quickstart) with [Tracecat Cloud](https://platform.tracecat.com) or [self-hosted](https://docs.tracecat.com/installation)._

[Tracecat](https://tracecat.com) is an open source automation platform for security teams. We're building the features of Tines / Splunk SOAR with:

- Enterprise-grade open source tools
- Open source AI infra and GPT models
- [Practitioner-obsessed UI/UX](#faq)

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
  - [ ] Batch-stream data transforms (expected April 2024)
  - [ ] Formulas (expected May 2024)
  - [ ] Versioning (expected June 2024)
- Case management
  - [x] [SMAC (status, malice, action, context)](https://www.rapid7.com/blog/post/2021/02/12/talkin-smac-alert-labeling-and-why-it-matters/)
  - [x] Suppression
  - [ ] Deduplication (expected 1st week April)
  - [x] AI-assisted labelling (e.g. MITRE ATT&CK)
  - [ ] Metrics
  - [ ] Analytics dashboard
- Event logs
  - [x] Unlimited logs storage
  - [x] Logs search
  - [ ] Visual detection rules
  - [ ] Piped query language
- Data validation
  - [x] [Pydantic V2](https://github.com/pydantic/pydantic) for fast data model and input / output validation in the backend
  - [x] [Zod](https://github.com/colinhacks/zod) for fast form and input / output validation in the frontend
- Teams
  - [ ] Collaboration
  - [ ] Tenants
- AI infrastructure
  - [x] Vector database for RAG
  - [ ] LLM evaluation and security
  - [ ] Bring-your-own LLM (OpenAI, Mistral, Anthropic etc.)

Tracecat is **not** a 1-to-1 mapping of Tines / Splunk SOAR. Our aim is to give technical teams a Tines-like experience, but with a focus on open source and AI features. [What do we mean by AI-native?](#what-does-ai-native-mean).

## Installation

Tracecat is Cloud agnostic and deploys anywhere that supports Docker.
Learn how to [install Tracecat locally](https://docs.tracecat.com/installation).

- [ ] Deployment
  - [x] Docker Compose
  - [x] AWS
  - [ ] Azure
  - [ ] GCP

## Status

- [x] Public Alpha: Anyone can sign up over at [tracecat.com](https://tracecat.com) but go easy on us, there are kinks and we are just getting started.
- [ ] Public Beta: Stable enough for most non-enteprise use-cases
- [ ] Public: Production-ready

We're currently in Public Alpha.

## Community & Support

Join us in building a newer, more open, kind of automation platform.

- [Tracecat Discord](https://discord.gg/n3GF4qxFU8) for hanging out with the community
- [GitHub issues](https://github.com/TracecatHQ/tracecat/issues)

## Integrations and pre-built workflows

We are working hard to reach core feature parity with Tines. Integrations and out-of-the-box automations will be prioritized according to user feedback. If you've got any suggestions, please let us know on [Discord](https://discord.gg/n3GF4qxFU8) 🦾.

Here are a few integrations on our roadmap:

- [ ] Slack
- [ ] Microsoft Teams
- [ ] GitHub
- [ ] CrowdStrike
- [ ] Terraform
- [ ] AWS CloudTrail
- [ ] Vanta

## Security

Please do not file GitHub issues or post on our public forum for security vulnerabilities, as they are public!

Tracecat takes security issues very seriously. If you have any concerns about Tracecat or believe you have uncovered a vulnerability, please get in touch via the e-mail address security@tracecat.com. In the message, try to provide a description of the issue and ideally a way of reproducing it. The security team will get back to you as soon as possible.

Note that this security address should be used only for undisclosed vulnerabilities. Please report any security problems to us before disclosing it publicly.

## FAQ

### What does it mean to be "practitioner-obsessed"?

Core features, user-interfaces, and day-to-day workflows are based on existing best-practices from [best-in-class security teams](https://medium.com/brexeng/elevating-security-alert-management-using-automation-828004ad596c). We won't throw in a Clippy chatbot just for the sake of it.

### Does the world really need another SOAR?

- Big enterprise SOARs are too expensive. They also lack transparency regarding their AI features.
- Open source SOARs were popular two years ago, but failed to mature from side-projects into enterprise-ready software.
- Most SIEMs are bundled with a SOAR, but lack flexibility for security teams (e.g. MSSPs) that work across multiple SIEMs or no SIEM at all.

### Why build open source?

- We love using and building open source tools.
- Existing "AI" security products hide behind demo-ware, sales calls, and white papers. We want to build in the open: open community, open tutorials, and open vision.
- Create a safe space for practitioners to experiment with open source AI models in their own isolated environments.

### What does AI-native mean?

We believe the most useful AI is "boring AI" (e.g. summarization, semantic search, data enrichment, labelling) that integrates with existing workflows, but with modern UI/UX and robust data engineering.

## Contributing

Whether it's big or small, we love contributions.
There's plenty of opportunity for new integrations and bug fixes.
The best way to get started is to ping us on [Discord](https://discord.gg/n3GF4qxFU8)!

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

## Open source vs paid

The Tracecat codebase is 100% open source under Apache-2.0. This includes (soon-to-be-built) enterprise features such as SSO and multi-tenancy. We offer a paid Cloud version for small-to-mid sized teams. Moreover, we plan to charge service fees to enterprises that want to deploy and maintain a self-hosted distributed version of Tracecat.

## License

[Apache-2.0](LICENSE)
