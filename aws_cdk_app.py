import os

from aws_cdk import App

from aws.stack import TracecatEngineStack

TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "staging")

app = App()
TracecatEngineStack(
    app,
    f"TracecatEngineStack-{TRACECAT__APP_ENV}",
    env={"region": os.environ["AWS_DEFAULT_REGION"]},
)

app.synth()
