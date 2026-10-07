"""Create a searchable Foundry Toolbox containing the knowledge base MCP tool."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPToolboxTool, ToolSearchToolboxTool
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


load_dotenv()


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set {name}.")
    return value


PROJECT_ENDPOINT = required_env("FOUNDRY_PROJECT_ENDPOINT").rstrip("/")
SEARCH_ENDPOINT = required_env("SEARCH_ENDPOINT").rstrip("/")
SEARCH_API_VERSION = os.environ.get("SEARCH_API_VERSION", "2026-08-01-preview")
KNOWLEDGE_BASE_NAME = required_env("KNOWLEDGE_BASE_NAME")
CONNECTION_NAME = required_env("KB_MCP_CONNECTION_NAME")
TOOLBOX_NAME = required_env("TOOLBOX_NAME")
SERVER_LABEL = os.environ.get("TOOLBOX_SERVER_LABEL", "knowledge-base")
EXPECTED_TOOL = "knowledge_base_retrieve"
MCP_ENDPOINT = (
    f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/mcp"
    f"?api-version={SEARCH_API_VERSION}"
)


def as_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "as_dict"):
        return model.as_dict()
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True, exclude_none=True)
    raise TypeError(f"Cannot serialize {type(model).__name__}")


def validate_version(version: Any, connection_id: str) -> dict[str, Any]:
    data = as_dict(version)
    tools = data.get("tools", [])
    if not any(tool.get("type") == "toolbox_search" for tool in tools):
        raise RuntimeError("Toolbox version is missing toolbox_search.")

    matching = [tool for tool in tools if tool.get("server_label") == SERVER_LABEL]
    if len(matching) != 1:
        raise RuntimeError(
            f"Expected one MCP tool labeled {SERVER_LABEL!r}; found {tools!r}"
        )

    tool = matching[0]
    expected = {
        "server_url": MCP_ENDPOINT,
        "project_connection_id": connection_id,
        "require_approval": "never",
        "allowed_tools": [EXPECTED_TOOL],
    }
    mismatches = {
        field: {"expected": value, "actual": tool.get(field)}
        for field, value in expected.items()
        if tool.get(field) != value
    }
    if mismatches:
        raise RuntimeError(f"Toolbox version does not match: {mismatches!r}")
    return data


def main() -> int:
    credential = DefaultAzureCredential()
    try:
        with AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=credential
        ) as project:
            connection = project.connections.get(CONNECTION_NAME)
            existing = next(
                (item for item in project.toolboxes.list() if item.name == TOOLBOX_NAME),
                None,
            )
            toolbox_missing = existing is None
            created_this_run = False
            if toolbox_missing:
                version = project.toolboxes.create_version(
                    name=TOOLBOX_NAME,
                    description="Azure AI Search knowledge base Toolbox.",
                    tools=[
                        ToolSearchToolboxTool(),
                        MCPToolboxTool(
                            server_label=SERVER_LABEL,
                            server_url=MCP_ENDPOINT,
                            server_description=(
                                "Retrieve grounded content from the configured "
                                "knowledge base."
                            ),
                            require_approval="never",
                            allowed_tools=[EXPECTED_TOOL],
                            project_connection_id=connection.id,
                        ),
                    ],
                )
                created_this_run = True
            else:
                version = project.toolboxes.get_version(
                    TOOLBOX_NAME, existing.default_version
                )

            data = validate_version(version, connection.id)
            version_number = str(data.get("version", version.version))

        print(
            json.dumps(
                {
                    "timestampUtc": datetime.now(UTC).isoformat(),
                    "createdThisRun": created_this_run,
                    "name": TOOLBOX_NAME,
                    "version": version_number,
                    "connectionId": connection.id,
                    "consumerEndpoint": (
                        f"{PROJECT_ENDPOINT}/toolboxes/{TOOLBOX_NAME}/mcp"
                        "?api-version=v1"
                    ),
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
                    "toolboxName": TOOLBOX_NAME,
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