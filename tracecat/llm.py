from __future__ import annotations

from typing import Any, Literal

import orjson
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion, Choice
from tenacity import retry, stop_after_attempt, wait_exponential

from tracecat.config import MAX_RETRIES
from tracecat.logger import standard_logger

logger = standard_logger(__name__)

TaskType = Literal[
    "translate",
    "extract",
    "summarize",
    "label",
    "enrich",
    "question_answering",
    "choice",
]
ModelType = Literal[
    "gpt-4-turbo-preview",
    "gpt-4-0125-preview",
    "gpt-4-vision-preview",
    "gpt-3.5-turbo-0125",
]
DEFAULT_MODEL_TYPE: ModelType = "gpt-4-turbo-preview"
DEFAULT_SYSTEM_CONTEXT = "You are a helpful assistant."

system_context = (
    "You are an expert decision maker and instruction follower."
    " You will be given JSON data as context to help you complete your task."
    " You do exactly as the user asks."
    " When given a question, you answer it in a conversational manner without repeating it back."
)

additional_instructions = (
    "Additional Instructions:"
    "\nYou have also been provided with the following JSON object of the previous task execution results,"
    " delimited by triple backticks (```)."
    " The object keys are the action ids and the values are the results of the actions."
    "\n```"
    "\n{action_trail}"
    "\n```"
    "You may use the past action run results to help you complete your task."
    " If you think it isn't helpful, you may ignore it."
)


def generate_pydantic_json_response_schema(response_schema: dict[str, Any]) -> str:
    return (
        "\nCreate a `JSONDataResponse` according to the following pydantic model:"
        "\n```"
        "\nclass JSONDataResponse(BaseModel):"
        f"\n{"\n".join(f"\t{k}: {v}" for k, v in response_schema.items())}"
        "\n```"
    )


def get_system_context(
    task: TaskType,
    **template_kwargs,
) -> str:
    context: str
    match task:
        case "translate":
            # The corresponding `message` body for this should be a templated string.
            context = (
                "You are an expert translator. You will be provided with a body of text that may"
                " contain non-English text, and your task is to translate it back into English."
                "\nFor example, given the following text:"
                "\nBonjour, comment ça va? My name is John. 你好吗?"
                "\nYou should respond with:"
                "\nHello, how are you? My name is John. How are you?"
            )
        case "extract":
            context = "Extract the following information from the text."
        case "summarize":
            context = "Summarize the following text."
        case "label":
            context = "Label the following text."
        case "enrich":
            context = "Enrich the following text."
        case "question_answering":
            context = "Answer the following question."
        case "choice":
            context = "Choose the best option from the following choices."
        case _:
            raise ValueError(f"Invalid task: {task}")

    formatted_add_instrs = additional_instructions.format(**template_kwargs)
    return "\n".join((context, formatted_add_instrs))


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def async_openai_call(  # type: ignore
    prompt: str,
    model: ModelType = DEFAULT_MODEL_TYPE,
    temperature: float = 0.2,
    system_context: str = DEFAULT_SYSTEM_CONTEXT,
    response_format: Literal["json_object", "text"] = "text",
    stream: bool = False,
    parse_json: bool = True,
    **kwargs,
):
    """Call the OpenAI API with the given prompt and return the response.

    Returns
    -------
    dict[str, Any]
        The message object from the OpenAI ChatCompletion API.
    """
    load_dotenv()
    client = AsyncOpenAI()

    def parse_choice(choice: Choice) -> str | dict[str, Any]:
        # The content will not be null, so we can safely use the `!` operator.
        content = choice.message.content
        if not content:
            logger.warning("No content in response.")
            return ""
        res = content.strip()
        if parse_json and response_format == "json_object":
            json_res: dict[str, Any] = orjson.loads(res)
            return json_res
        return res

    if response_format == "json_object":
        system_context += " Please only output valid JSON."

    messages = [
        {"role": "system", "content": system_context},
        {"role": "user", "content": prompt},
    ]

    logger.info("🧠 Calling OpenAI API with model: %s...", model)
    response: ChatCompletion = await client.chat.completions.create(  # type: ignore[call-overload]
        model=model,
        response_format={"type": response_format},
        messages=messages,
        temperature=temperature,
        stream=stream,
        **kwargs,
    )
    if stream:
        return response

    return parse_choice(response.choices[0])
