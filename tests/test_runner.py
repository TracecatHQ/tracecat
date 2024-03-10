import hashlib
import os

import httpx
from dotenv import load_dotenv

from tracecat.logger import standard_logger
from tracecat.workflows import Workflow

logger = standard_logger(__name__)
load_dotenv()


def test_diamond_dag():
    adj_list: dict[str, list[str]] = {
        "TEST-WORKFLOW-ID.receive_sentry_event": [
            "TEST-WORKFLOW-ID.question_generation"
        ],
        "TEST-WORKFLOW-ID.question_generation": [
            "TEST-WORKFLOW-ID.google_search",
            "TEST-WORKFLOW-ID.consolidate",
        ],
        "TEST-WORKFLOW-ID.google_search": ["TEST-WORKFLOW-ID.send_slack"],
        "TEST-WORKFLOW-ID.consolidate": ["TEST-WORKFLOW-ID.send_slack"],
        "TEST-WORKFLOW-ID.send_slack": [],
    }
    raw_action_mapping: dict[str, dict[str, str]] = {
        "TEST-WORKFLOW-ID.receive_sentry_event": {
            "key": "TEST-WORKFLOW-ID.receive_sentry_event",
            "type": "webhook",
            "title": "Receive Sentry event",
            "url": "http://localhost:8000/",
            "method": "GET",
        },
        "TEST-WORKFLOW-ID.question_generation": {
            "key": "TEST-WORKFLOW-ID.question_generation",
            "type": "llm",
            "title": "Question generation",
            "instructions": "You just received this data: {{ $.receive_sentry_event.data }}. Generate a question from the given text.",
            "response_schema": {
                "questions": "list[str]",
            },
        },
        "TEST-WORKFLOW-ID.google_search": {
            "key": "TEST-WORKFLOW-ID.google_search",
            "type": "http_request",
            "title": "Google search",
            "url": "http://localhost:8000/mock/search",
            "method": "POST",
            "payload": {
                "query": "{{ $.question_generation.questions[0] }}",
            },
        },
        "TEST-WORKFLOW-ID.consolidate": {
            "key": "TEST-WORKFLOW-ID.consolidate",
            "type": "llm",
            "title": "Consolidate",
            "instructions": "Consolidate your findings into a few sentences.",
            "model": "gpt-4-turbo-preview",
        },
        "TEST-WORKFLOW-ID.send_slack": {
            "key": "TEST-WORKFLOW-ID.send_slack",
            "type": "http_request",
            "title": "Send Slack",
            "url": "http://localhost:8000/mock/slack",
            "method": "POST",
            "payload": {
                "workspace": "Tracecat",
                "channel": "general",
                "message": "Here are some questions I prepared: {{ $.question_generation.questions }}",
            },
        },
    }

    uuid = "TEST_ID"
    workflow = Workflow(
        id=uuid,
        adj_list=adj_list,
        actions=raw_action_mapping,
    )

    logger.info(f"Workflow: {workflow}")

    with httpx.Client() as client:
        response = client.post(
            "http://localhost:8000/workflow",
            json=workflow.model_dump(),
        )
        logger.debug(response.json())

        entrypoint = "TEST-WORKFLOW-ID.receive_sentry_event"
        response = client.post(f"http://localhost:8000/webhook/{entrypoint}")
        logger.debug(response.json())


def test_slack_qna_e2e():
    headers = {"X-API-KEY": os.getenv("RUNNER_API_KEY")}
    adj_list: dict[str, list[str]] = {
        "TEST-WORKFLOW-ID.receive_question_from_slack": [
            "TEST-WORKFLOW-ID.question_answering"
        ],
        "TEST-WORKFLOW-ID.question_answering": [
            "TEST-WORKFLOW-ID.send_answer_to_slack"
        ],
        "TEST-WORKFLOW-ID.send_answer_to_slack": [],
    }
    raw_action_mapping: dict[str, dict[str, str]] = {
        "TEST-WORKFLOW-ID.receive_question_from_slack": {
            "key": "TEST-WORKFLOW-ID.receive_question_from_slack",
            "type": "webhook",
            "title": "Receive question from Slack",
            "url": "http://localhost:8000/",
            "method": "POST",
        },
        "TEST-WORKFLOW-ID.question_answering": {
            "key": "TEST-WORKFLOW-ID.question_answering",
            "type": "llm",
            "title": "Question answering",
            "instructions": (
                "Your task is to answer the user's question."
                " The question: {{ $.receive_question_from_slack.payload.question }}."
                "If you don't know the answer, respond saying you don't know."
            ),
        },
        "TEST-WORKFLOW-ID.send_answer_to_slack": {
            "key": "TEST-WORKFLOW-ID.send_answer_to_slack",
            "type": "http_request",
            "title": "Send answer to Slack",
            "url": "http://localhost:8000/mock/slack",
            "headers": headers,
            "method": "POST",
            "payload": {
                "workspace": "Tracecat",
                "channel": "general",
                "message": "{{ $.question_answering.response }}",
            },
        },
    }

    uuid = "TEST_WORKFLOW_ID"
    workflow = Workflow(
        id=uuid,
        adj_list=adj_list,
        actions=raw_action_mapping,
    )
    # url = "https://f735-162-218-227-134.ngrok-free.app"
    url = "http://localhost:8000"

    with httpx.Client() as client:
        response = client.post(
            f"{url}/workflow",
            json=workflow.model_dump(),
            headers=headers,
        )
        logger.info(response.json())

        entrypoint = "TEST-WORKFLOW-ID.receive_question_from_slack"
        response = client.post(
            f"{url}/webhook/{entrypoint}",
            headers=headers,
            json={
                "question": "What is the capital of France?",
            },
        )
        logger.info(response.json())


def test_slack_qna_e2e_live(url: str):
    base_headers = {"X-API-KEY": os.getenv("RUNNER_API_KEY")}
    workflow_uuid = hashlib.sha256(b"TEST-WORKFLOW-ID").hexdigest()[:10]
    logger.info(f"Workflow UUID: {workflow_uuid}")
    adj_list: dict[str, list[str]] = {
        f"{workflow_uuid}.receive_question_from_slack": [
            f"{workflow_uuid}.question_answering"
        ],
        f"{workflow_uuid}.question_answering": [
            f"{workflow_uuid}.send_answer_to_slack"
        ],
        f"{workflow_uuid}.send_answer_to_slack": [],
    }
    raw_action_mapping: dict[str, dict[str, str]] = {
        f"{workflow_uuid}.receive_question_from_slack": {
            "key": f"{workflow_uuid}.receive_question_from_slack",
            "type": "webhook",
            "title": "Receive question from Slack",
            "url": "http://localhost:8000/",
            "method": "POST",
        },
        f"{workflow_uuid}.question_answering": {
            "key": f"{workflow_uuid}.question_answering",
            "type": "llm",
            "title": "Question answering",
            "instructions": (
                "Your task is to answer the user's question."
                " The question: {{ $.receive_question_from_slack.payload.text }}."
            ),
        },
        f"{workflow_uuid}.send_answer_to_slack": {
            "key": f"{workflow_uuid}.send_answer_to_slack",
            "type": "http_request",
            "title": "Send answer to Slack",
            "url": "https://hooks.slack.com/services/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",
            "headers": base_headers,
            "method": "POST",
            "payload": {
                "text": "{{ $.question_answering.response }}",
            },
        },
    }

    workflow = Workflow(
        id=workflow_uuid,
        adj_list=adj_list,
        actions=raw_action_mapping,
    )

    with httpx.Client() as client:
        response = client.post(
            f"{url}/workflow",
            json=workflow.model_dump(),
            headers=base_headers,
        )
        logger.info(response.json())

        entrypoint = f"{workflow_uuid}.receive_question_from_slack"
        secret = hashlib.sha256(
            f"{entrypoint}{os.environ['TRACECAT__SIGNING_SECRET']}".encode()
        ).hexdigest()

        logger.info(secret)


def test_slack_e2e_live_with_task_types(url: str):
    base_headers = {"X-API-KEY": os.getenv("RUNNER_API_KEY")}
    workflow_uuid = hashlib.sha256(b"TEST-WORKFLOW-ID").hexdigest()[:10]
    logger.info(f"Workflow UUID: {workflow_uuid}")
    adj_list: dict[str, list[str]] = {
        # Source
        f"{workflow_uuid}.receive_question_from_slack": [f"{workflow_uuid}.ai_task"],
        f"{workflow_uuid}.ai_task": [
            f"{workflow_uuid}.send_answer_to_slack",
            f"{workflow_uuid}.send_answer_to_email",
        ],
        # Sink
        f"{workflow_uuid}.send_answer_to_slack": [],
        f"{workflow_uuid}.send_answer_to_email": [f"{workflow_uuid}.send_fun_email"],
        f"{workflow_uuid}.send_fun_email": [],
    }
    raw_action_mapping: dict[str, dict[str, str]] = {
        f"{workflow_uuid}.receive_question_from_slack": {
            "key": f"{workflow_uuid}.receive_question_from_slack",
            "type": "webhook",
            "title": "Receive message from Slack",
            "url": "http://localhost:8000/",
            "method": "POST",
        },
        f"{workflow_uuid}.ai_task": {
            "key": f"{workflow_uuid}.ai_task",
            "type": "llm",
            "title": "Multiple Choice Question",
            # Message should contain the data that should be operated on
            # Think the last 'user' message in the chat
            "message": "{{ $.receive_question_from_slack.payload.text }}",
            "task_fields": {
                "type": "choice",
                "choices": ["This is spam", "This is not spam", "I can't tell"],
            },
            "response_schema": {
                "choice": "str",
                "reasoning": "str = Field(description='Detailed and clear reasoning for your choice')",
            },
        },
        f"{workflow_uuid}.send_answer_to_slack": {
            "key": f"{workflow_uuid}.send_answer_to_slack",
            "type": "http_request",
            "title": "Send answer to Slack",
            "url": "https://hooks.slack.com/services/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",
            "headers": base_headers,
            "method": "POST",
            "payload": {
                "text": "My Choice: {{ $.ai_task.choice }}\nReason: {{ $.ai_task.reasoning }}",
            },
        },
        f"{workflow_uuid}.send_answer_to_email": {
            "key": f"{workflow_uuid}.send_answer_to_email",
            "type": "send_email",
            "title": "Send answer to email recipients",
            "recipients": ["daryl@tracecat.com"],
            "subject": "AI Spam detection test",
            "contents": (
                "Hey!"
                "\n\nI'm the Tracecat workflow automation AI."
                "\nI've received a message from Slack:"
                "\n```"
                "\n{{ $.receive_question_from_slack.payload.text }}"
                "\n```"
                "\n\nHere are my thoughts:"
                "My thoughts: {{ $.ai_task.choice }}"
                "\n {{ $.ai_task.reasoning }}"
                "\n\nBest,"
                "\nTracecat AI"
            ),
        },
        f"{workflow_uuid}.send_fun_email": {
            "key": f"{workflow_uuid}.send_answer_to_email",
            "type": "send_email",
            "title": "Send answer to email recipients",
            "recipients": ["janeychan.jc@gmail.com"],
            "subject": "AI Spam detection test",
            "contents": (
                "Herro!"
                "\n\nI'm the Tracecat workflow automation AI."
                " Good morning and have a good day <3"
                "\n\n~Daryl"
                "\nSent from Tracecat AI"
            ),
        },
    }

    workflow = Workflow(
        id=workflow_uuid,
        adj_list=adj_list,
        actions=raw_action_mapping,
    )

    logger.info(f"Workflow: {workflow}")

    with httpx.Client() as client:
        response = client.post(
            f"{url}/workflow",
            json=workflow.model_dump(),
            headers=base_headers,
        )
        logger.info(response.json())

        entrypoint = f"{workflow_uuid}.receive_question_from_slack"
        secret = hashlib.sha256(
            f"{entrypoint}{os.environ['TRACECAT__SIGNING_SECRET']}".encode()
        ).hexdigest()

        logger.info(secret)


if __name__ == "__main__":
    import sys

    url = None
    if len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        url = "http://localhost:8000"
    test_slack_e2e_live_with_task_types(url)
