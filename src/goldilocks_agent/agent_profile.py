"""Which agent a deployment runs: the website's chatbot or the local agent.

Junwen, 2026-11-16. One graph serves both (`graph.py`); a profile sets what
differs between them:

- the tools the model is offered;
- its system prompt;
- how far one turn may go: tool calls per turn, and LangGraph's recursion
  limit as a backstop.

The website (`GOLDILOCKS_AGENT_SHARED_DEPLOYMENT`) is a chatbot that changes
Tool parameters from what the user says, in a few tool calls a turn. The
local version is an agent that runs things and decides the next step from
their results, so it gets more tools and a longer turn.

The profile follows the deployment, which is the same for every request a
process serves, so callers ask `current_profile()` rather than threading it
through each call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from goldilocks_agent.config import read_shared_deployment_enabled
from goldilocks_agent.tools import (
    DFT_WORKBENCH_TOOLS,
    MLIP_PLAYGROUND_TOOLS,
    STRUCTURE_SEARCH_TOOLS,
    TOOL_SCHEMAS,
)

# What the assistant is for and how to use its tools (stfc/goldilocks-agent#72,
# design doc 12.2). Without it, the #68 A/B saw small models invent
# parameters instead of calling dft_review, print a fetched structure
# instead of opening it, and -- Ollama's Ministral template fills in its own
# "Le Chat" prompt when a request has none -- introduce themselves as a
# generic assistant. The third paragraph also rules out values of the
# model's own: Ministral 3 14B volunteered cutoffs and k-meshes that
# contradicted core's. Both profiles send it unchanged for now; the local
# agent's own paragraph (planning, checking results) comes with its loop.
_PROMPT = """\
You are the assistant in Goldilocks Workbench. You help users set up DFT \
calculations (Quantum ESPRESSO) with goldilocks-core, which chooses the \
calculation parameters.

Tools:
- find_in_databases, then get_structure: find an existing computed structure \
when the user asks for one.
- dft_open_structure, then dft_review, then dft_download_bundle: open a \
structure, show goldilocks-core's recommended parameters, and generate the \
input files.
- dft_settings and dft_set: see and change any DFT Workbench setting, as the \
user would in its panel: the task (band structure, phonons, a relaxation...), \
code, HPC profile and every parameter. dft_set returns the new review.
- dft_magnetic_orderings: list the magnetic orderings of a spin-polarized \
calculation, with their ranking if the user ran one in DFT Workbench.

The user sees dft_review's values and sources in a card above your reply, so \
don't list them again or put them in a table: say what needs the user's \
attention (warnings, anything blocking generation) and the next step. When you \
name a value, give it and its source as the tools return them. Never invent \
parameter values or choose values of your own, walltime and other job \
resources included: set a value with dft_set only when the user gave it. For \
a warning about a setting, or if the user wants a setting changed without \
giving the value, say which setting it is and ask for the value (dft_settings \
lists the options), never suggesting one, not even as an example. dft_review \
shows what is set: if its task isn't the \
calculation the user asked for, switch it with dft_set. Call \
dft_download_bundle only when the user asks for input files. When generation \
is blocked, explain what needs deciding and ask the user. Reply in the \
language of the user's latest message."""


@dataclass(frozen=True)
class AgentProfile:
    """What one deployment's agent may do in a turn."""

    name: Literal["web", "local"]
    # Offered to the model in this order; `graph.call_tool` refuses any other.
    tools: tuple[str, ...]
    prompt: str
    # Tool calls answered after the turn's user message. Past it, calls get
    # an error and the model is asked to wrap up (`graph.py`).
    max_tool_calls_per_turn: int
    # LangGraph super-steps per run: a backstop for the budget above, not
    # the limit users meet.
    recursion_limit: int

    def tool_schemas(self) -> list[dict]:
        """The tool schemas the model is offered, in order."""
        return [TOOL_SCHEMAS[name] for name in self.tools]

    def offers(self, *names: str) -> bool:
        """Whether this profile offers every tool in `names`."""
        return set(names) <= set(self.tools)


# The website: no MLIP Playground (it runs MACE on the user's own machine;
# Junwen, 2026-11-09), and a short turn.
WEB = AgentProfile(
    name="web",
    tools=(*STRUCTURE_SEARCH_TOOLS, *DFT_WORKBENCH_TOOLS),
    prompt=_PROMPT,
    max_tool_calls_per_turn=6,
    recursion_limit=20,
)

# The local version: every shipped Tool, and room for a multi-step turn.
LOCAL = AgentProfile(
    name="local",
    tools=(*STRUCTURE_SEARCH_TOOLS, *MLIP_PLAYGROUND_TOOLS, *DFT_WORKBENCH_TOOLS),
    prompt=_PROMPT,
    max_tool_calls_per_turn=30,
    recursion_limit=80,
)


def current_profile() -> AgentProfile:
    """The profile for this deployment: WEB on the shared website, else LOCAL."""
    return WEB if read_shared_deployment_enabled() else LOCAL
