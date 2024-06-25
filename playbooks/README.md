# Playbooks

Tracecat playbooks are defined in our YAML DSL.
These are pre-built workflows that you can use as-is or customize to suit your needs.

# Running workflows:

From the root of the repository, run the following commands to get started.

1. Create, commit, and activate the workflow (+webhook) in one step with the following command:

```console
tracecat workflow create --commit playbooks/alert_management/crowdstrike-to-cases.yml --activate --webhook
```

2. To run the workflow, run the following command with the workflow ID (replace with your own):

```console
tracecat workflow run wf-XXXXXXXXXXXXXXXXXXXXXXXXXX --data '{"my": "data", "for": "the", "workflow": "run"}'
```

To run workflow with action tests:

```console
tracecat workflow run wf-XXXXXXXXXXXXXXXXXXXXXXXXXX --data '{"my": "data", "for": "the", "workflow": "run"}' --test
```
