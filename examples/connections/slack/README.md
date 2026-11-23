# Connect Tracecat to Slack

Use [`slack-app-manifest.json`](slack-app-manifest.json) to receive mentions, handle interactive messages, run a workflow with `/tracecat`, and write reports to canvases. Publish one Tracecat workflow per feature you use.

1. In a local copy of the manifest, replace each placeholder URL with the published workflow's HTTPS webhook URL. Keep `?echo=true` for events and `?echo=true&empty_echo=true` for interactivity and slash commands. Remove any feature block you skip. Treat each full webhook URL as a credential.
2. In [Slack apps](https://api.slack.com/apps), select **Create New App → From a manifest**. Pick your workspace, select **JSON**, and paste the configured manifest.
3. Install the app, save its bot token as `SLACK_BOT_TOKEN` in Tracecat's `slack` credential, and invite the app to your test channel.

Follow [Connect to Slack](https://docs.tracecat.com/cheatsheets/connection-guides/connect-to-slack) for workflow examples and verification steps.
