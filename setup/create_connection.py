"""Create the Foundry RemoteTool connection for a knowledge base MCP endpoint."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import requests
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


load_dotenv()


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set {name}.")
    return value


SUBSCRIPTION_ID = required_env("AZURE_SUBSCRIPTION_ID")
RESOURCE_GROUP = required_env("FOUNDRY_RESOURCE_GROUP")
ACCOUNT_NAME = required_env("FOUNDRY_ACCOUNT_NAME")
PROJECT_NAME = required_env("FOUNDRY_PROJECT_NAME")
PROJECT_ENDPOINT = required_env("FOUNDRY_PROJECT_ENDPOINT").rstrip("/")
SEARCH_ENDPOINT = required_env("SEARCH_ENDPOINT").rstrip("/")
SEARCH_API_VERSION = os.environ.get("SEARCH_API_VERSION", "2026-08-01-preview")
KNOWLEDGE_BASE_NAME = required_env("KNOWLEDGE_BASE_NAME")
CONNECTION_NAME = required_env("KB_MCP_CONNECTION_NAME")
CONNECTION_AUDIENCE = os.environ.get(
    "REMOTE_TOOL_AUDIENCE", "https://search.azure.com/"
)
CONNECTION_AUTH_TYPE = os.environ.get(
    "REMOTE_TOOL_AUTH_TYPE", "UserEntraToken"
)
ARM_API_VERSION = "2025-10-01-preview"

# A remote SharePoint knowledge source requires the caller's own token; other modes fail at query time.
SUPPORTED_AUTH_TYPES = {"UserEntraToken"}
if CONNECTION_AUTH_TYPE not in SUPPORTED_AUTH_TYPES:
    raise ValueError(
        f"REMOTE_TOOL_AUTH_TYPE must be one of {sorted(SUPPORTED_AUTH_TYPES)}."
    )

PROJECT_RESOURCE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}/"
    f"providers/Microsoft.CognitiveServices/accounts/{ACCOUNT_NAME}/"
    f"projects/{PROJECT_NAME}"
)
MCP_ENDPOINT = (
    f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/mcp"
    f"?api-version={SEARCH_API_VERSION}"
)


def connection_url() -> str:
    return (
        f"https://management.azure.com{PROJECT_RESOURCE_ID}/connections/"
        f"{CONNECTION_NAME}?api-version={ARM_API_VERSION}"
    )


def expected_payload() -> dict[str, Any]:
    return {
        "name": CONNECTION_NAME,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType": CONNECTION_AUTH_TYPE,
            "category": "RemoteTool",
            "target": MCP_ENDPOINT,
            "isSharedToAll": True,
            "audience": CONNECTION_AUDIENCE,
            "metadata": {"ApiType": "Azure"},
        },
    }


def connection_mismatches(connection: dict[str, Any]) -> dict[str, Any]:
    properties = connection.get("properties", {})
    expected = expected_payload()["properties"]
    fields = (
        "authType",
        "category",
        "target",
        "audience",
        "metadata",
    )
    return {
        field: {"expected": expected[field], "actual": properties.get(field)}
        for field in fields
        if properties.get(field) != expected[field]
    }


def main() -> int:
    client_request_id = str(uuid.uuid4())
    credential = DefaultAzureCredential()
    try:
        token = credential.get_token(
            "https://management.azure.com/.default"
        ).token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-ms-client-request-id": client_request_id,
        }
        response = requests.get(connection_url(), headers=headers, timeout=60)
        connection_missing = response.status_code == 404
        created_this_run = False
        if connection_missing:
            response = requests.put(
                connection_url(),
                headers=headers,
                json=expected_payload(),
                timeout=60,
            )
            response.raise_for_status()
            created_this_run = True
        else:
            response.raise_for_status()
            mismatches = connection_mismatches(response.json())
            if mismatches:
                raise RuntimeError(
                    f"Existing connection does not match: {mismatches!r}"
                )

        connection = response.json()
        with AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=credential
        ) as project:
            sdk_connection = project.connections.get(CONNECTION_NAME)

        print(
            json.dumps(
                {
                    "timestampUtc": datetime.now(UTC).isoformat(),
                    "clientRequestId": client_request_id,
                    "createdThisRun": created_this_run,
                    "connectionName": CONNECTION_NAME,
                    "connectionId": connection.get("id"),
                    "sdkConnectionId": sdk_connection.id,
                    "authType": CONNECTION_AUTH_TYPE,
                    "target": MCP_ENDPOINT,
                },
                indent=2,
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "timestampUtc": datetime.now(UTC).isoformat(),
                    "clientRequestId": client_request_id,
                    "connectionName": CONNECTION_NAME,
                    "errorType": type(error).__name__,
                    "error": str(error),
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