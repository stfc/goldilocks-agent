"""The structure files a chat holds, found by name for `dft_open_structure`
on the server (#142).

The model names a file; the text comes from the chat itself, never from
the model re-typing it (#73). A chat's structure files are:

- the files its user attached, which the composer embeds in the message as
  `[Attached file: NAME]` followed by a fenced block (a truncated one is
  broken, not smaller, so it is left out);
- the structures its `get_structure` calls fetched, whose tool messages
  keep the file's name and text.

Ported from `app/src/utils/structureFiles.ts` and `dftWorkbench.ts`, which
found them in the browser.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from goldilocks_agent.dft_draft import StructureInput

# The formats the chat's structure viewer reads (`WEAS_SUPPORTED_EXTS`).
SUPPORTED_EXTENSIONS = frozenset(
    {".cif", ".cube", ".poscar", ".vasp", ".xsf", ".xyz", ".weas-json"}
)

# Marks an attachment the composer cut to its budget (App.tsx's send()).
TRUNCATED_ATTACHMENT_MARKER = "[Truncated attachment:"

_ATTACHMENT_FENCE = re.compile(
    r"\[Attached file: ([^\]]+)\]\n```([^\n]*)\n(.+?)```", re.S
)


@dataclass(frozen=True)
class ChatStructure:
    """A structure file the chat holds, and its inferred extension."""

    name: str
    content: str
    ext: str


def _raw_extension(name: str) -> str:
    trimmed = name.strip()
    dot = trimmed.rfind(".")
    if dot <= 0 or dot == len(trimmed) - 1:
        return ""
    return trimmed[dot:].lower()


def _floats(line: str, count: int) -> bool:
    fields = line.split()[:count]
    if len(fields) < count:
        return False
    try:
        [float(field) for field in fields]
    except ValueError:
        return False
    return True


def _looks_like_poscar(content: str) -> bool:
    """A scale factor, then three lattice vectors (POSCAR's first lines)."""
    lines = content.strip().splitlines()
    return (
        len(lines) >= 6
        and _floats(lines[1], 1)
        and all(_floats(lines[i], 3) for i in (2, 3, 4))
    )


def infer_extension(name: str, content: str) -> str:
    """The structure format of a file, from its name, else its text; the raw
    extension when neither says it is a structure."""
    lower = name.strip().lower()
    if lower in ("poscar", "contcar"):
        return ".poscar"
    raw = _raw_extension(name)
    if raw in SUPPORTED_EXTENSIONS:
        return raw
    text = content.strip()
    if re.search(r"^data_", text, re.I | re.M) or re.search(
        r"_cell_length_a|_atom_site_", text, re.I
    ):
        return ".cif"
    if re.search(r"PRIMVEC|PRIMCOORD|BEGIN_BLOCK_DATAGRID_3D", content, re.I):
        return ".xsf"
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().isdigit():
        count = int(lines[0].strip())
        if (
            count > 0
            and len(lines) >= count + 2
            and _floats(" ".join(lines[2].split()[1:4]), 3)
        ):
            return ".xyz"
    if len(lines) >= 6 and all(_floats(line, 4) for line in lines[2:6]):
        return ".cube"
    if _looks_like_poscar(content):
        return ".poscar"
    return raw


def _structure_of(name: str, content: str) -> ChatStructure | None:
    ext = infer_extension(name, content)
    return ChatStructure(name, content, ext) if ext in SUPPORTED_EXTENSIONS else None


def _message_text(content: object) -> str:
    """A message's text: the string itself, or a multimodal message's text
    part (which carries the attachments)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text", ""))
    return ""


def attached_structures(content: object) -> list[ChatStructure]:
    """The structure attachments of one user message, in order; recognized
    formats only, truncated attachments left out."""
    text = _message_text(content)
    found = []
    for match in _ATTACHMENT_FENCE.finditer(text):
        if text[match.end() :].startswith(f"\n{TRUNCATED_ATTACHMENT_MARKER}"):
            continue
        structure = _structure_of(match.group(1), match.group(3))
        if structure is not None:
            found.append(structure)
    return found


def _fetched_structure(message: ToolMessage) -> ChatStructure | None:
    try:
        fetched = json.loads(message.content)
    except (TypeError, ValueError):
        return None
    if not isinstance(fetched, dict):
        return None
    name, content = fetched.get("filename"), fetched.get("content")
    if not isinstance(name, str) or not isinstance(content, str):
        return None  # a failed fetch: its result is {"error": ...}
    return _structure_of(name, content)


def chat_structures(messages: Sequence[BaseMessage]) -> list[ChatStructure]:
    """The structure files a chat's messages hold -- attachments and
    get_structure's fetched structures -- in the order each name first
    appears; a name seen again keeps its place and takes the latest text."""
    get_structure_calls: set[str] = set()
    by_name: dict[str, ChatStructure] = {}
    for message in messages:
        found: list[ChatStructure] = []
        if isinstance(message, HumanMessage):
            found = attached_structures(message.content)
        elif isinstance(message, AIMessage):
            get_structure_calls |= {
                call["id"]
                for call in message.tool_calls
                if call["name"] == "get_structure"
            }
        elif (
            isinstance(message, ToolMessage)
            and message.tool_call_id in get_structure_calls
        ):
            fetched = _fetched_structure(message)
            found = [fetched] if fetched else []
        for structure in found:
            by_name[structure.name] = structure
    return list(by_name.values())


def structure_input(structure: ChatStructure) -> StructureInput:
    """The structure as goldilocks-core's `/inspect` takes it."""
    return StructureInput(
        structure_content=structure.content,
        structure_name=structure.name,
        structure_format="cif" if structure.ext == ".cif" else "poscar",
    )


def missing_structure_text(name: object, structures: Sequence[ChatStructure]) -> str:
    """Why no file of that name was found, for the model: the names it can
    use instead, or that the chat has none yet."""
    asked = f"No structure file named {json.dumps(name)} in this chat"
    if not structures:
        return (
            f"{asked}, and the chat has no structure files yet. Ask the user to "
            "attach one, or find one with find_in_databases and get_structure."
        )
    names = ", ".join(json.dumps(s.name) for s in structures)
    return (
        f"{asked}. Structure files in this chat: {names}. Call dft_open_structure "
        "again with one of these names, or ask the user to attach the file."
    )
