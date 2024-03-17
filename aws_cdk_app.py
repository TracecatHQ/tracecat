import os

from aws_cdk import App

from aws.stack import TracecatEngineStack

app = App()
TracecatEngineStack(
    app,
    "TracecatEngineStack",
    env={"region": os.environ.get("AWS_DEFAULT_REGION", "us-west-2")},
)

app.synth()
