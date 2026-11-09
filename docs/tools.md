# Tools

Start a chat and either talk to the model directly, or open one of the
Tool panels on the right (the small icon strip) for a structured
interface into the same underlying capability -- for Find in Databases,
both paths call the same backend functions, and the panel updates live
when the model calls a tool on its own. DFT Workbench is the
exception: it has no chat tool-calling of its own -- Goldilocks in chat
only ever gives DFT guidance, the real setup/results live entirely in its
embedded panel.

- **Find in Databases**: search by chemical formula, or type/attach a
  structure and ask about it directly (`find_in_databases`/`get_structure`
  tools, no confirmation needed -- it's a read-only lookup). Searches
  Materials Project, Materials Cloud, NOMAD, and JARVIS.
- **DFT Workbench**: the embedded goldilocks-core Workbench itself handles
  structure input, analysis, advisors, and bundle download/generation --
  talk to it directly, not through chat (`GOLDILOCKS_AGENT_CORE_AUTOSTART`
  or `GOLDILOCKS_CORE_PATH` must be set; goldilocks-agent auto-starts
  core's backend for you either way, see [Configuration](configuration.md)).
  Chat can still explain DFT concepts/workflows in general, it just can't
  drive this Tool's panel the way it drives Find in Databases. Its magnetism fields can additionally run on a real ML tier
  (mMACE) instead of heuristic/LLM -- separate opt-in setup, see
  [Configuration](configuration.md).
- **Post Analysis** is partial today -- only its phonon visualizer (open
  any phonopy `band.yaml`) is real.
- **Local version only**: **MLIP Playground** (MACE single-point/geomopt/
  EOS/NEB/phonons, confirmation-gated; running it also needs
  `GOLDILOCKS_AGENT_MLIP_ENABLED`, see Configuration above) and
  **AiiDAlab-QE**. Their `TOOLS` entries in `app/src/App.tsx` are
  `localOnly`: the STFC Cloud web deployment lists them under "Coming soon
  (available in the local version)", hides MLIP Playground's `run_mlip_*`
  tools from the chat model and refuses its `/api/mlip/*` routes
  (`mlip_playground_offered()` in `src/goldilocks_agent/tools/__init__.py`).
- **Coming soon** everywhere (shown only in the full-page All Tools view):
  **Beyond DFT**. To re-enable it, drop `comingSoon: true` from its `TOOLS`
  entry in `app/src/App.tsx`.
- **AiiDAlab-QE** has its entry but no backend yet.
- Drag a structure file (CIF/XYZ/POSCAR/VASP/XSF/CUBE) onto the window, or
  use a Tool panel's own "Upload structure" button, to add it to the
  current chat.
