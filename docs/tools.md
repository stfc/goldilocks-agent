# Tools

Start a chat and either talk to the model directly, or open one of the
Tool panels on the right (the small icon strip) for a structured
interface into the same underlying capability -- for Find in Databases and
MLIP Playground, both paths call the same backend functions, and the panel
updates live when the model calls a tool on its own. DFT Workbench is the
exception: it has no chat tool-calling of its own -- Goldilocks in chat
only ever gives DFT guidance, the real setup/results live entirely in its
embedded panel.

- **Find in Databases**: search by chemical formula, or type/attach a
  structure and ask about it directly (`find_in_databases`/`get_structure`
  tools, no confirmation needed -- it's a read-only lookup). Searches
  Materials Project, Materials Cloud, NOMAD, and JARVIS.
- **MLIP Playground**: single-point/geometry-optimization/equation-of-
  state/NEB/phonon calculations via MACE. Every calculation -- from the
  chat or from the panel's own buttons -- asks for explicit confirmation
  first, since it's real local compute (`GOLDILOCKS_AGENT_MLIP_ENABLED`
  must be set, see [Configuration](configuration.md)). Phonon results link
  to a "Phonon visualizer" -- also reachable from Post Analysis for a
  `band.yaml` from anywhere else.
- **DFT Workbench**: the embedded goldilocks-core Workbench itself handles
  structure input, analysis, advisors, and bundle download/generation --
  talk to it directly, not through chat (`GOLDILOCKS_AGENT_CORE_AUTOSTART`
  or `GOLDILOCKS_CORE_PATH` must be set; goldilocks-agent auto-starts
  core's backend for you either way, see [Configuration](configuration.md)).
  Chat can still explain DFT concepts/workflows in general, it just can't
  drive this Tool's panel the way it drives Find in Databases/MLIP
  Playground. Its magnetism fields can additionally run on a real ML tier
  (mMACE) instead of heuristic/LLM -- separate opt-in setup, see
  [Configuration](configuration.md).
- **Beyond DFT** and **Post Analysis** are partial today -- panel-only, no
  real backing yet beyond Post Analysis's phonon visualizer.
- **AiiDA** is not built yet.
- Drag a structure file (CIF/XYZ/POSCAR/VASP/XSF/CUBE) onto the window, or
  use a Tool panel's own "Upload structure" button, to add it to the
  current chat.
