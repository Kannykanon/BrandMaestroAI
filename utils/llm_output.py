"""Parsing model output."""
import json
import re


def message_text(content) -> str:
    """Flatten a chat message's content to its text.

    Providers return either a string or a list of content blocks (text,
    thinking, tool calls). Only the text blocks are the answer.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return "" if content is None else str(content)


def _escape_inner_quotes(raw: str) -> str:
    """Escape double quotes that appear inside a JSON string value.

    The commonest way a judgement is lost. The evaluator is asked to quote the
    sentence it objects to, the sentence contains a quotation — a treatment
    saying he understands the "no" isn't really an option — and the quotation
    marks close the JSON string three words early. The whole evaluation is then
    discarded over punctuation in somebody else's prose.

    The prompt already forbids it, which is not the same as preventing it, and
    the enforcer's answer was to spend a second model call asking for the same
    evaluation again. This repairs it for nothing.

    A quote inside a string is told from the one that ends it by what follows:
    a terminator is followed by a comma, a colon, a closing brace or bracket, or
    the end of the document. Anything else is a quote somebody wrote.
    """
    out, in_string, escaped = [], False, False
    for index, char in enumerate(raw):
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char == '"':
            if not in_string:
                in_string = True
                out.append(char)
                continue
            rest = raw[index + 1:].lstrip()
            if not rest or rest[0] in ",:}]":
                in_string = False
                out.append(char)
            else:
                out.append('\\"')       # a quote in the middle of a value
            continue
        out.append(char)
    return "".join(out)


def parse_llm_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from LLM output.

    Raises the original JSONDecodeError when nothing can be salvaged, so a
    caller that wants to retry or fail closed still can.
    """
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        # Valid JSON, but not an evaluation. Callers read it with .get(), so a
        # list would fail later and further from the cause than here.
        raise json.JSONDecodeError("Expected a JSON object", raw, 0)
    except json.JSONDecodeError as first:
        # The object may be wrapped in commentary the prompt asked for and did
        # not get. Take the outermost braces before attempting any repair.
        start, end = raw.find("{"), raw.rfind("}")
        candidate = raw[start:end + 1] if 0 <= start < end else raw
        for attempt in (candidate, _escape_inner_quotes(candidate)):
            try:
                parsed = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise first
