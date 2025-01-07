#!.venv/bin/python
r"""This module extracts a part of a markdown string from an input file or a given input string.

The extraction starts at a line that contains the content specified by the `--start-line` parameter
and ends at a line that contains the content specified by the `--end-line` parameter.
If `--start-line` is not specified, extraction starts from the beginning of the file or string.
If `--end-line` is not specified, extraction goes to the end of the file or string.

The extracted markdown string is written either to stdout or to the specified output file.
Additionally, the heading levels can be adjusted by specifying the `--heading-level` parameter.

Usage:
    scripts/extract_markdown.py [--input-file INPUT_FILE | --input INPUT_STRING] [--start-line START_LINE] [--end-line END_LINE] [--output-file OUTPUT_FILE] [--heading-level HEADING_LEVEL]

Arguments:
    --input-file   : The file path to read the markdown content from.
    --input        : The markdown content as a string.
    --start-line   : Optional. The string content of the start line from where extraction begins.
    --end-line     : Optional. The string content of the end line where extraction ends.
    --output-file  : Optional. The file path to write the extracted markdown content to.
    --heading-level: Optional. The number of additional `#` to add to markdown headings or to remove
        from markdown headings if negative.

Example:
    scripts/extract_markdown.py --input-file input.md --start-line "# Start" --end-line "# End" --output-file output.md --heading-level 1
    scripts/extract_markdown.py --input "# Start\n\nSome content here\n\n# End" --start-line "# Start" --end-line "# End" --output-file output.md --heading-level 1
"""

"""
This module extracts a part of a markdown string from an input file or a given input string.

The extraction starts at a line that contains the content specified by the `--start-line` parameter
and ends at a line that contains the content specified by the `--end-line` parameter.
If `--start-line` is not specified, extraction starts from the beginning of the file or string.
If `--end-line` is not specified, extraction goes to the end of the file or string.

The extracted markdown string is written either to stdout or to the specified output file.
Additionally, the heading levels can be adjusted by specifying the `--heading-level` parameter.

Usage:
    python extract_markdown.py [--input-file INPUT_FILE | --input INPUT_STRING | --input-stdin] [--start-line START_LINE] [--end-line END_LINE] [--output-file OUTPUT_FILE] [--heading-level HEADING_LEVEL]

Arguments:
    --input-file   : The file path to read the markdown content from.
    --input        : The markdown content as a string.
    --input-stdin  : Read markdown content from stdin.
    --start-line   : Optional. The string content of the start line from where extraction begins.
    --end-line     : Optional. The string content of the end line where extraction ends.
    --output-file  : Optional. The file path to write the extracted markdown content to.
    --heading-level: Optional. The number of additional `#` to add to markdown headings or to remove from markdown headings if negative.

Example:
    python extract_markdown.py --input-file input.md --start-line "# Start" --end-line "# End" --output-file output.md --heading-level 1
    python extract_markdown.py --input "# Start\n\nSome content here\n\n# End" --start-line "# Start" --end-line "# End" --output-file output.md --heading-level 1
"""

import argparse
import re
import sys


def adjust_heading_levels(line: str, heading_level: int) -> str:
    """Adjust the heading levels in a markdown line.

    Args:
        line (str): The markdown line.
        heading_level (int): The number of levels to adjust the headings by.

    Returns:
        adjusted_line (str): The line with adjusted heading levels.
    """
    heading_pattern = re.compile(r"^(#+)\s")
    match = heading_pattern.match(line)
    if match:
        current_level = len(match.group(1))
        new_level = current_level + heading_level
        if new_level > 0:
            adjusted_line = "#" * new_level + line[current_level:]
        else:
            adjusted_line = line[current_level:]
    else:
        adjusted_line = line
    return adjusted_line


def extract_markdown(content: str, start_line: str, end_line: str, heading_level: int) -> str:
    """Extract a part of a markdown string from given content.

    Args:
        content (str): The markdown content.
        start_line (str): The string content of the start line from where extraction begins.
        end_line (str): The string content of the end line where extraction ends.
        heading_level (int): The number of levels to adjust the headings by.

    Returns:
        extracted_content (str): Extracted markdown content as a string.
    """
    extracted_content = []
    lines = content.splitlines(True)
    extracting = start_line is None
    for line in lines:
        if not extracting and start_line and start_line in line:
            extracting = True
            extracted_content.append(
                adjust_heading_levels(line, heading_level)
            )  # Include start line in output
            continue
        if extracting and end_line and end_line in line:
            extracting = False
            break
        if extracting:
            extracted_content.append(adjust_heading_levels(line, heading_level))
    return "".join(extracted_content)


def main():
    """Main function to run the extraction of the markdown content."""
    parser = argparse.ArgumentParser(
        description="Extract a part of a markdown string from an input file"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input-file", type=str, help="File to read the markdown content from")
    group.add_argument("--input", type=str, help="Markdown content as a string")
    group.add_argument(
        "--input-stdin", action="store_true", help="Read markdown content from stdin"
    )
    parser.add_argument(
        "--start-line",
        type=str,
        default=None,
        help="Optional. The string content of the start line",
    )
    parser.add_argument(
        "--end-line", type=str, default=None, help="Optional. The string content of the end line"
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="File to write the extracted markdown content to",
    )
    parser.add_argument(
        "--heading-level",
        type=int,
        default=0,
        help="The number of additional `#` to add to markdown headings or to remove from markdown headings if negative",
    )

    args = parser.parse_args()

    try:
        if args.input_file:
            with open(args.input_file, "r", encoding="utf8") as f:
                content = f.read()
        elif args.input:
            content = args.input
        elif args.input_stdin:
            content = sys.stdin.read()
        else:
            raise ValueError("No valid input source provided.")

        extracted_content = extract_markdown(
            content, args.start_line, args.end_line, args.heading_level
        )
        if args.output_file:
            # Write to file
            with open(args.output_file, "w", encoding="utf8") as f:
                f.write(extracted_content)
        else:
            # Write to std output
            print(extracted_content)

    except Exception as e:
        print(f"Error during markdown extraction: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
