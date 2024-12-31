"""Utility functions for openapi specification conversion tasks."""

import json


def openapi_to_markdown(openapi_json: dict) -> str:
    """Convert OpenAPI JSON specification to a Markdown representation.

    Args:
        openapi_json (dict): The OpenAPI specification as a Python dictionary.

    Returns:
        str: The Markdown representation of the OpenAPI spec.
    """
    # Extract basic info
    title = openapi_json.get("info", {}).get("title", "API Documentation")
    version = openapi_json.get("info", {}).get("version", "1.0.0")
    description = openapi_json.get("info", {}).get("description", "No description provided.")
    base_url = openapi_json.get("servers", [{}])[0].get("url", "No base URL provided.")

    markdown = f"# {title}\n\n"
    markdown += f"**Version**: `{version}`\n\n"
    markdown += f"**Description**: {description}\n\n"
    markdown += f"**Base URL**: `{base_url}`\n\n"

    # Authentication
    security_schemes = openapi_json.get("components", {}).get("securitySchemes", {})
    if security_schemes:
        markdown += "## Authentication\n\n"
        for scheme, details in security_schemes.items():
            auth_type = details.get("type", "unknown")
            markdown += f"- **{scheme}**: {auth_type}\n\n"

    # Paths
    markdown += "## Endpoints\n\n"
    paths = openapi_json.get("paths", {})
    for path, methods in paths.items():
        for method, details in methods.items():
            markdown += f"### `{method.upper()} {path}`\n\n"

            summary = details.get("summary", None)
            if summary:
                markdown += f"{summary}\n\n"

            description = details.get("description", None)
            if description:
                markdown += "```\n"
                markdown += f"{description}"
                markdown += "\n```\n\n"

            # Parameters
            parameters = details.get("parameters", [])
            if parameters:
                markdown += "**Parameters**:\n\n"
                for param in parameters:
                    name = param.get("name", "unknown")
                    location = param.get("in", "unknown")
                    required = param.get("required", False)
                    description = param.get("description", "No description provided.")
                    markdown += f"- `{name}` ({location}, {'required' if required else 'optional'}): {description}\n\n"

            # Request body
            request_body = details.get("requestBody", {}).get("content", {})
            if request_body:
                markdown += "**Request Body**:\n\n"
                for content_type, schema in request_body.items():
                    markdown += (
                        f"- `{content_type}`: {json.dumps(schema.get('schema', {}), indent=2)}\n\n"
                    )

            # Responses
            responses = details.get("responses", {})
            if responses:
                markdown += "**Responses**:\n\n"
                for status, response in responses.items():
                    desc = response.get("description", "No description provided.")
                    markdown += f"- **{status}**: {desc}\n\n"

            markdown += "---\n\n"

    return markdown
