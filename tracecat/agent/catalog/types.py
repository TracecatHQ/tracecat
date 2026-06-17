"""Agent catalog type definitions."""

from __future__ import annotations

from typing import Literal, NotRequired, Protocol, TypedDict


class BedrockContentBlock(TypedDict):
    text: str


class BedrockMessage(TypedDict):
    role: Literal["user"]
    content: list[BedrockContentBlock]


class BedrockInferenceConfig(TypedDict):
    maxTokens: int


class AnthropicInvokeContentBlock(TypedDict):
    type: Literal["text"]
    text: str


class AnthropicInvokeMessage(TypedDict):
    role: Literal["user"]
    content: list[AnthropicInvokeContentBlock]


class AnthropicInvokePayload(TypedDict):
    anthropic_version: str
    max_tokens: int
    messages: list[AnthropicInvokeMessage]


class BedrockInferenceProfileResponse(TypedDict):
    status: NotRequired[str]
    inferenceProfileId: NotRequired[str]
    inferenceProfileArn: NotRequired[str]
    models: NotRequired[list[dict[str, str]]]


class BedrockModelAvailabilityResponse(TypedDict):
    authorizationStatus: NotRequired[str]
    entitlementAvailability: NotRequired[str]
    regionAvailability: NotRequired[str]


class BedrockInferenceProfileDetails(TypedDict):
    target_type: Literal["inference_profile"]
    model_count: int
    status: NotRequired[str]
    inference_profile_id: NotRequired[str]
    inference_profile_arn: NotRequired[str]


class BedrockModelAvailabilityDetails(TypedDict):
    target_type: Literal["model_id"]
    authorization_status: NotRequired[str]
    entitlement_availability: NotRequired[str]
    region_availability: NotRequired[str]


type BedrockVerificationDetails = (
    BedrockInferenceProfileDetails | BedrockModelAvailabilityDetails
)


class BedrockControlClient(Protocol):
    async def get_inference_profile(
        self,
        *,
        inferenceProfileIdentifier: str,
    ) -> BedrockInferenceProfileResponse: ...

    async def get_foundation_model_availability(
        self,
        *,
        modelId: str,
    ) -> BedrockModelAvailabilityResponse: ...


class BedrockRuntimeClient(Protocol):
    async def converse(
        self,
        *,
        modelId: str,
        messages: list[BedrockMessage],
        inferenceConfig: BedrockInferenceConfig,
    ) -> object: ...

    async def invoke_model(
        self,
        *,
        modelId: str,
        body: bytes,
        contentType: str,
        accept: str,
    ) -> object: ...
