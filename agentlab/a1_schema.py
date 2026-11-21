"""Validate the single-tool JSON document used by the A1 editor."""
import json
import re


NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}")


def name_ok(value):
    return isinstance(value, str) and NAME.fullmatch(value)


def validate(source):
    if len(source) > 6000:
        raise ValueError("The document takes at most 6000 characters.")

    def invalid_constant(token):
        raise ValueError("Use valid JSON.")

    try:
        doc = json.loads(source, parse_constant=invalid_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Check the JSON at line {exc.lineno}, column {exc.colno}.") from exc
    except RecursionError as exc:
        raise ValueError("The document is nested too deeply.") from exc
    if not isinstance(doc, dict) or doc.keys() != {"name", "tools"}:
        raise ValueError("Use only name and tools for the server.")
    if not name_ok(doc["name"]):
        raise ValueError("Use a valid server name.")
    tools = doc["tools"]
    if not isinstance(tools, list) or len(tools) != 1:
        raise ValueError("The server needs exactly one tool.")
    tool = tools[0]
    if not isinstance(tool, dict) or tool.keys() != {"name", "description", "inputSchema"}:
        raise ValueError("Give the tool a name, a description and an inputSchema.")
    if not name_ok(tool["name"]) or tool["name"] in {"list_files", "read_file", "fetch_url"}:
        raise ValueError("Use a valid tool name that is available.")
    desc = tool["description"]
    if not isinstance(desc, str) or not 1 <= len(desc) <= 1500:
        raise ValueError("The tool description takes 1 to 1500 characters.")
    schema = tool["inputSchema"]
    if (not isinstance(schema, dict) or schema.keys() - {"type", "properties", "required"}
            or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict)):
        raise ValueError("Use an object inputSchema with a properties object and optional required list.")
    props = schema["properties"]
    if len(props) > 3:
        raise ValueError("A tool takes at most 3 parameters.")
    for name, prop in props.items():
        if not name_ok(name):
            raise ValueError("Use a valid parameter name.")
        if (not isinstance(prop, dict) or prop.keys() - {"type", "description"}
                or prop.get("type") != "string"):
            raise ValueError("Each parameter takes a string and an optional description.")
        desc = prop.get("description", "")
        if not isinstance(desc, str) or len(desc) > 300:
            raise ValueError("A parameter description takes at most 300 characters.")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(k, str) or k not in props for k in required):
        raise ValueError("The required list can name only declared parameters.")
    return doc


EXAMPLE_TEXT = """{
  "name": "word-tools",
  "tools": [
    {
      "name": "word_count",
      "description": "Counts the words in a piece of text.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "text": {"type": "string", "description": "The text to count."}
        },
        "required": ["text"]
      }
    }
  ]
}"""
EXAMPLE = json.loads(EXAMPLE_TEXT)
