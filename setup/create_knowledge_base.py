"""Create one remote SharePoint knowledge source and its knowledge base."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

import requests
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


load_dotenv()


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set {name}.")
    return value


SEARCH_ENDPOINT = required_env("SEARCH_ENDPOINT").rstrip("/")
API_VERSION = os.environ.get("SEARCH_API_VERSION", "2026-05-01-preview")
KNOWLEDGE_BASE_NAME = required_env("KNOWLEDGE_BASE_NAME")
OUTPUT_MODE = os.environ.get("KNOWLEDGE_BASE_OUTPUT_MODE", "extractiveData")
REASONING_EFFORT = os.environ.get("RETRIEVAL_REASONING_EFFORT", "minimal")
KNOWLEDGE_BASE_DESCRIPTION = os.environ.get(
    "KNOWLEDGE_BASE_DESCRIPTION",
    "SharePoint knowledge base with extractive retrieval.",
)

SUPPORTED_OUTPUT_MODES = {"extractiveData", "answerSynthesis"}
SUPPORTED_REASONING_EFFORTS = {"minimal", "low", "medium", "auto"}
if OUTPUT_MODE not in SUPPORTED_OUTPUT_MODES:
    raise ValueError(
        f"KNOWLEDGE_BASE_OUTPUT_MODE must be one of {sorted(SUPPORTED_OUTPUT_MODES)}."
    )
if REASONING_EFFORT not in SUPPORTED_REASONING_EFFORTS:
    raise ValueError(
        "RETRIEVAL_REASONING_EFFORT must be one of "
        f"{sorted(SUPPORTED_REASONING_EFFORTS)}."
    )
if OUTPUT_MODE == "answerSynthesis" and REASONING_EFFORT == "minimal":
    raise ValueError(
        "answerSynthesis is incompatible with minimal retrieval reasoning effort."
    )

MODEL_REQUIRED = OUTPUT_MODE == "answerSynthesis" or REASONING_EFFORT != "minimal"
MODEL_ENDPOINT = (
    required_env("FOUNDRY_MODEL_ENDPOINT").rstrip("/") if MODEL_REQUIRED else None
)
MODEL_DEPLOYMENT = required_env("FOUNDRY_MODEL_DEPLOYMENT") if MODEL_REQUIRED else None


def load_knowledge_sources() -> list[dict[str, str]]:
    try:
        value = json.loads(required_env("KNOWLEDGE_SOURCES_JSON"))
    except json.JSONDecodeError as error:
        raise ValueError("KNOWLEDGE_SOURCES_JSON must contain valid JSON.") from error

    if not isinstance(value, list) or not value:
        raise ValueError("KNOWLEDGE_SOURCES_JSON must be a non-empty JSON array.")

    sources: list[dict[str, str]] = []
    for index, source in enumerate(value):
        if not isinstance(source, dict):
            raise ValueError(f"Knowledge source at index {index} must be an object.")
        name = source.get("name")
        path = source.get("path")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Knowledge source at index {index} needs a name.")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"Knowledge source {name!r} needs a path.")
        sources.append({"name": name.strip(), "path": path.strip()})

    names = [source["name"] for source in sources]
    if len(names) != len(set(names)):
        raise ValueError("Knowledge source names must be unique.")
    return sources


KNOWLEDGE_SOURCES = load_knowledge_sources()


def resource_url(resource_type: str, name: str) -> str:
    return f"{SEARCH_ENDPOINT}/{resource_type}/{name}?api-version={API_VERSION}"


def knowledge_source_payload(source: dict[str, str]) -> dict[str, Any]:
    return {
        "name": source["name"],
        "kind": "remoteSharePoint",
        "description": "Remote SharePoint knowledge source for Toolbox retrieval.",
        "remoteSharePointParameters": {
            "filterExpression": f'Path:"{source["path"]}"',
            "containerTypeId": "",
            "resourceMetadata": ["Title", "Author"],
        },
    }


def knowledge_base_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": KNOWLEDGE_BASE_NAME,
        "description": KNOWLEDGE_BASE_DESCRIPTION,
        "outputMode": OUTPUT_MODE,
        "knowledgeSources": [
            {"name": source["name"]} for source in KNOWLEDGE_SOURCES
        ],
        "retrievalReasoningEffort": {"kind": REASONING_EFFORT},
    }
    if MODEL_REQUIRED:
        payload["models"] = [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": MODEL_ENDPOINT,
                    "deploymentId": MODEL_DEPLOYMENT,
                    "modelName": MODEL_DEPLOYMENT,
                    "apiKey": None,
                    "authIdentity": None,
                },
            }
        ]
    return payload


def comparable_source(value: dict[str, Any]) -> dict[str, Any]:
    parameters = value.get("remoteSharePointParameters", {})
    return {
        "name": value.get("name"),
        "kind": value.get("kind"),
        "filterExpression": parameters.get("filterExpression"),
        "resourceMetadata": parameters.get("resourceMetadata"),
    }


def comparable_knowledge_base(value: dict[str, Any]) -> dict[str, Any]:
    model = (value.get("models") or [{}])[0].get("azureOpenAIParameters", {})
    return {
        "name": value.get("name"),
        "description": value.get("description"),
        "outputMode": value.get("outputMode"),
        "knowledgeSources": [
            {"name": source.get("name")}
            for source in value.get("knowledgeSources", [])
        ],
        "reasoning": value.get("retrievalReasoningEffort"),
        "modelEndpoint": model.get("resourceUri"),
        "modelDeployment": model.get("deploymentId"),
    }


def ensure_exact_resource(
    session: requests.Session,
    resource_type: str,
    name: str,
    payload: dict[str, Any],
    comparable: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    url = resource_url(resource_type, name)
    response = session.get(url, timeout=60)
    resource_missing = response.status_code == 404
    created = False
    if resource_missing:
        response = session.put(url, json=payload, timeout=60)
        response.raise_for_status()
        created = True
    else:
        response.raise_for_status()
        if comparable(response.json()) != comparable(payload):
            raise RuntimeError(
                f"Existing {resource_type} resource {name!r} does not match."
            )
    return response.json(), created


def main() -> int:
    client_request_id = str(uuid.uuid4())
    credential = DefaultAzureCredential()
    try:
        token = credential.get_token("https://search.azure.com/.default").token
        with requests.Session() as session:
            session.headers.update(
                {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "x-ms-client-request-id": client_request_id,
                }
            )
            source_results = []
            for source_config in KNOWLEDGE_SOURCES:
                source, source_created = ensure_exact_resource(
                    session,
                    "knowledgesources",
                    source_config["name"],
                    knowledge_source_payload(source_config),
                    comparable_source,
                )
                source_results.append(
                    {"name": source["name"], "createdThisRun": source_created}
                )
            knowledge_base, knowledge_base_created = ensure_exact_resource(
                session,
                "knowledgebases",
                KNOWLEDGE_BASE_NAME,
                knowledge_base_payload(),
                comparable_knowledge_base,
            )

        print(
            json.dumps(
                {
                    "timestampUtc": datetime.now(UTC).isoformat(),
                    "clientRequestId": client_request_id,
                    "knowledgeSources": source_results,
                    "knowledgeBase": knowledge_base["name"],
                    "knowledgeBaseCreatedThisRun": knowledge_base_created,
                    "outputMode": knowledge_base.get("outputMode"),
                    "retrievalReasoningEffort": knowledge_base.get(
                        "retrievalReasoningEffort"
                    ),
                },
                indent=2,
            )
        )
        return 0
    except Exception as error:
        response = getattr(error, "response", None)
        print(
            json.dumps(
                {
                    "timestampUtc": datetime.now(UTC).isoformat(),
                    "clientRequestId": client_request_id,
                    "errorType": type(error).__name__,
                    "error": str(error),
                    "statusCode": getattr(response, "status_code", None),
                    "response": getattr(response, "text", None),
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        credential.close()


if __name__ == "__main__":
    raise SystemExit(main())