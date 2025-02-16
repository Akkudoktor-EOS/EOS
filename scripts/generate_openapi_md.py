#!.venv/bin/python
"""Utility functions for OpenAPI specification conversion tasks."""

import argparse
import json
import os
import sys

import git

if __package__ is None or __package__ == "":
    # uses current directory visibility
    import generate_openapi
else:
    # uses current package visibility
    from . import generate_openapi


def akkudoktoreos_base_branch():
    """Find the remote branch from the Akkudoktor-EOS repository that the current branch is based on.

    This function searches through all branches in the Akkudoktor-EOS remote
    to find the first commit of the remote branch taht is in the current branch and returns the name
    of the remote branch (without the remote name).

    Returns:
        str: The name of the remote branch (without the remote name)
             that the first commit of the current branch is based on.
             Returns None if no matching remote branch is found.
    """
    repo = git.Repo(__file__, search_parent_directories=True)

    # Get the current branch
    try:
        current_branch = repo.active_branch.name
    except:
        # Maybe detached branch that has no name
        return None

    first_commit = next(repo.iter_commits(current_branch, max_count=1)).hexsha

    # Iterate over all remote branches to find the first commit
    for remote in repo.remotes:
        if "https://github.com/Akkudoktor-EOS" in remote.url:
            # Only search for Akkudoktor-EOS
            for ref in remote.refs:
                try:
                    # Check if the first commit of the current branch is in the remote branch history
                    commits = list(repo.iter_commits(ref.name))
                    if any(commit.hexsha == first_commit for commit in commits):
                        # Remove remote name from the branch name
                        branch_name = "/".join(ref.name.split("/")[1:])
                        return branch_name
                except git.exc.GitCommandError:
                    continue

    return None


def extract_info(openapi_json: dict) -> dict:
    """Extract basic information from OpenAPI JSON.

    Args:
        openapi_json (dict): The OpenAPI specification as a Python dictionary.

    Returns:
        dict: A dictionary containing the title, version, description, and base_url.
    """
    info = openapi_json.get("info", {})
    servers = openapi_json.get("servers", [{}])

    return {
        "title": info.get("title", "API Documentation"),
        "version": info.get("version", "1.0.0"),
        "description": info.get("description", "No description provided."),
        "base_url": servers[0].get("url", "No base URL provided."),
    }


def format_authentication(security_schemes: dict) -> str:
    """Format the authentication section for the Markdown.

    Args:
        security_schemes (dict): The security schemes from the OpenAPI spec.

    Returns:
        str: The formatted authentication section in Markdown.
    """
    if not security_schemes:
        return ""

    markdown = "## Authentication\n\n"
    for scheme, details in security_schemes.items():
        auth_type = details.get("type", "unknown")
        markdown += f"- **{scheme}**: {auth_type}\n\n"
    return markdown


def format_parameters(parameters: list) -> str:
    """Format the parameters section for the Markdown.

    Args:
        parameters (list): The list of parameters from an endpoint.

    Returns:
        str: The formatted parameters section in Markdown.
    """
    if not parameters:
        return ""

    markdown = "**Parameters**:\n\n"
    for param in parameters:
        name = param.get("name", "unknown")
        location = param.get("in", "unknown")
        required = param.get("required", False)
        description = param.get("description", "No description provided.")
        markdown += (
            f"- `{name}` ({location}, {'required' if required else 'optional'}): {description}\n\n"
        )
    return markdown


def format_request_body(request_body: dict) -> str:
    """Format the request body section for the Markdown.

    Args:
        request_body (dict): The request body content from an endpoint.

    Returns:
        str: The formatted request body section in Markdown.
    """
    if not request_body:
        return ""

    markdown = "**Request Body**:\n\n"
    for content_type, schema in request_body.items():
        markdown += f"- `{content_type}`: {json.dumps(schema.get('schema', {}), indent=2)}\n\n"
    return markdown


def format_responses(responses: dict) -> str:
    """Format the responses section for the Markdown.

    Args:
        responses (dict): The responses from an endpoint.

    Returns:
        str: The formatted responses section in Markdown.
    """
    if not responses:
        return ""

    markdown = "**Responses**:\n\n"
    for status, response in responses.items():
        desc = response.get("description", "No description provided.")
        markdown += f"- **{status}**: {desc}\n\n"
    return markdown


def format_endpoint(path: str, method: str, details: dict, devel: bool = False) -> str:
    """Format a single endpoint's details for the Markdown.

    Args:
        path (str): The endpoint path.
        method (str): The HTTP method.
        details (dict): The details of the endpoint.
        devel (bool): Include development output.

    Returns:
        str: The formatted endpoint section in Markdown.
    """
    link_summary = (
        details.get("summary", "<summary missing>")
        .lower()
        .strip()
        .replace(" ", "_")
        .replace("-", "_")
    )
    link_path = (
        path.lower().strip().replace("/", "_").replace(".", "_").replace("{", "_").replace("}", "_")
    )
    link_method = f"_{method.lower()})"
    # [local](http://localhost:8503/docs#/default/fastapi_config_get_v1_config_get)
    local_path = (
        "[local](http://localhost:8503/docs#/default/" + link_summary + link_path + link_method
    )
    # [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_strompreis_strompreis_get)
    akkudoktoreos_main_path = (
        "[eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/"
        + link_summary
        + link_path
        + link_method
    )

    markdown = f"## {method.upper()} {path}\n\n"

    markdown += f"**Links**: {local_path}, {akkudoktoreos_main_path}"
    if devel:
        # Add link to akkudoktor branch the development has used
        akkudoktor_branch = akkudoktoreos_base_branch()
        if akkudoktor_branch is not None:
            akkudoktoreos_base_path = (
                f"[devel](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/{akkudoktor_branch}/openapi.json#/default/"
                + link_summary
                + link_path
                + link_method
            )
            markdown += f", {akkudoktoreos_base_path}"
    markdown += "\n\n"

    summary = details.get("summary", None)
    if summary:
        markdown += f"{summary}\n\n"

    description = details.get("description", None)
    if description:
        markdown += "```\n"
        markdown += f"{description}"
        markdown += "\n```\n\n"

    markdown += format_parameters(details.get("parameters", []))
    markdown += format_request_body(details.get("requestBody", {}).get("content", {}))
    markdown += format_responses(details.get("responses", {}))
    markdown += "---\n\n"

    return markdown


def openapi_to_markdown(openapi_json: dict, devel: bool = False) -> str:
    """Convert OpenAPI JSON specification to a Markdown representation.

    Args:
        openapi_json (dict): The OpenAPI specification as a Python dictionary.
        devel (bool): Include development output.

    Returns:
        str: The Markdown representation of the OpenAPI spec.
    """
    info = extract_info(openapi_json)
    markdown = f"# {info['title']}\n\n"
    markdown += f"**Version**: `{info['version']}`\n\n"
    markdown += f"**Description**: {info['description']}\n\n"
    markdown += f"**Base URL**: `{info['base_url']}`\n\n"

    security_schemes = openapi_json.get("components", {}).get("securitySchemes", {})
    markdown += format_authentication(security_schemes)

    markdown += "**Endpoints**:\n\n"
    paths = openapi_json.get("paths", {})
    for path in sorted(paths):
        methods = paths[path]
        for method in sorted(methods):
            details = methods[method]
            markdown += format_endpoint(path, method, details)

    # Assure the is no double \n at end of file
    markdown = markdown.rstrip("\n")
    markdown += "\n"

    return markdown


def generate_openapi_md() -> str:
    """Generate OpenAPI specification in Markdown.

    Returns:
        str: The Markdown representation of the OpenAPI spec.
    """
    openapi_spec = generate_openapi.generate_openapi()
    openapi_md = openapi_to_markdown(openapi_spec)
    return openapi_md


def main():
    """Main function to run the generation of the OpenAPI specification as Markdown."""
    parser = argparse.ArgumentParser(description="Generate OpenAPI Specification as Markdown")
    parser.add_argument(
        "--devel", action="store_true", help="Create swagger link to development branch"
    )
    parser.add_argument(
        "--output-file", type=str, default=None, help="File to write the OpenAPI Specification to"
    )

    args = parser.parse_args()

    try:
        openapi_md = generate_openapi_md()
        if os.name == "nt":
            openapi_md = openapi_md.replace("127.0.0.1", "0.0.0.0")
        if args.output_file:
            # Write to file
            with open(args.output_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(openapi_md)
        else:
            # Write to std output
            print(openapi_md)

    except Exception as e:
        print(f"Error during OpenAPI specification generation: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
