<h1 align="center">
  <img src="img/banner.svg" alt="tracecat">
</h1>

<div align="center">
  <p>
    The open source + AI-native Tines alternative.
  </p>
</div>

[Tracecat](https://tracecat.com) is an open-source workflow automation and case management platform. We are building the features of Tines / Torq / Palo Alto XSOAR using:

- Enterprise-grade open source tools
- Open source AI infra and GPT models
- [Practioner-obsessed UI/UX](#faq)

It's designed to be simple but powerful. Try out our [tutorial](https://docs.tracecat.com) and build your first SOAR automation with AI analysts *in minutes*.

Tracecat is also Cloud agnostic and deploys anywhere that supports Docker.

## Get started

Help Mario automate away false positives from his pizza shop.

## Features

- Workflows
  - [x] Drag-and-drop builder
  - [x] Core primitives (webhook, HTTP, if-else, send email, etc.)
  - [x] AI Actions (label, summarize, enrich etc.)
  - [ ] Batch-stream data transforms (expected April 2024)
  - [ ] Secrets (expected April 2024)
  - [ ] Formulas (expected May 2024)
  - [ ] Versioning (expected June 2024)
- Case management
  - [x] [SMAC (status, malice, action, context)](https://www.rapid7.com/blog/post/2021/02/12/talkin-smac-alert-labeling-and-why-it-matters/)
  - [x] Suppression
  - [x] Deduplication
  - [x] AI-assisted labelling (e.g. MITRE ATT&CK)
  - [ ] Metrics
  - [ ] Analytics dashboard
- Event logs
  - [x] Unlimited logs storage
  - [x] Logs search
  - [ ] Visual detection rules
  - [ ] Piped query language
- Teams
  - [ ] Collaboration
  - [ ] Tenants
- AI infrastructure
  - [x] Vector database for RAG
  - [ ] LLM evaluation and security
  - [ ] Bring-your-own LLM (OpenAI, Mistral, Anthropic etc.)

Tracecat is **not** a 1-to-1 mapping of Tines. Our aim is to give technical teams a Tines-like experience, but with a focus on open source, AI features, and unlimited logs storage.

## Installation

- [x] Authentication
  - [x] Supabase
  - [ ] Auth.js
  - [ ] Supertokens
- [ ] Deployment
  - [x] Docker Compose
  - [ ] AWS
  - [ ] Azure
  - [ ] GCP

## Is Tracecat enterprise ready?

Yes and no.

Can already scale beyond Tines' free tier, but for enterprise (100+ employees).

- [x] Embedded architecture (single instance)
  - [x] Flunk: homegrown workflow engine based on Flink
  - [x] LanceDB
  - [x] Tantivy
  - [x] Polars
- [ ] Distributed architecture
  - [ ] Apache Flink
  - [ ] LanceDB / Lantern
  - [ ] Quickwit

## Status

- [x] Public Alpha: Anyone can sign up over at [tracecat.com](https://tracecat.com) but go easy on us, there are kinks and we are just getting started.
- [ ] Public Beta: Stable enough for most non-enteprise use-cases
- [ ] Public: Production-ready

We're currently in Public Alpha.

## Community & Support

Join us in building a new, more open kind of automation platform.

- [Tracecat Discord](https://discord.gg/n3GF4qxFU8) for hanging out with the community
- [GitHub issues](https://github.com/TracecatHQ/tracecat/issues)

## Integrations

We are working hard to reach core feature parity with Tines. In the meantime, integrations and OOTB automations will be prioritized according to user feedback.

If you've got suggestions, please let us know on Discord! Any help is welcome :)

Here are just a few integrations we have planned:

- [ ] Slack
- [ ] Microsoft Teams
- [ ] GitHub
- [ ] CrowdStrike
- [ ] Terraform
- [ ] AWS CloudTrail
- [ ] Vanta

## Security

Looking to report a security vulnerability? Please don't post about it in GitHub issue. Instead, refer to our [SECURITY.md](SECURITY.md) file.

## FAQ

### What does it mean to be "practioner-obsessed"?

Core features, user-interfaces, and day-to-day workflows are based on existing best-practices from [best-in-class security teams](https://medium.com/brexeng/elevating-security-alert-management-using-automation-828004ad596c).

We won't throw in a Clippy chatbot just for the sake of it.

### What does AI-native mean?

AI isn't magic.

At Tracecat we want to build boring AI that integrates with existing workflows, but with a modern UI/UX and robust data engineering.

### Does the world really need another SOAR?

- Big enterprise SOARs are too expensive. They also lack transparency regarding their AI features.
- Open source SOARs were popular two years ago, but failed to mature from side-projects into enterprise-ready software.
- Most SIEMs are bundled with a SOAR, but lack flexibility for security teams (e.g. MSSPs) that work across multiple SIEMs or no SIEM at all.

### Tracecat is a venture-backed start up. Why build open source?

We believe LLMs are a **must-have** technology for defenders.

## Contributing

## Open source vs paid

Like our favorite data orchestration platforms Apache Airflow and Prefect, we plan to keep our codebase open source. This includes enterprise features such as SSO and multi-tenancy.

We plan to grow through Tracecat Cloud for small-to-mid sized teams. Moreover, deploying, maintaining, and debugging a self-hosted distributed system for >1,000 person enteprises is not easy. We plan to charge a good sum for that service 💸.

## License

[Apache-2.0](LICENSE)
