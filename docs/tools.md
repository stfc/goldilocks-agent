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
- **Coming soon** (shown only in the full-page All Tools view, not
  openable this release): **MLIP Playground** (MACE single-point/geomopt/
  EOS/NEB/phonons, confirmation-gated -- code and tests intact) and
  **Beyond DFT**. To re-enable either, drop
  `comingSoon: true` from its `TOOLS` entry in `app/src/App.tsx`; MLIP
  Playground also needs `MLIP_PLAYGROUND_RELEASED = True` in
  `src/goldilocks_agent/tools/__init__.py`, which is what keeps its
  `run_mlip_*` LLM tools and `/api/mlip/*` routes off meanwhile -- and,
  on the STFC Cloud web deployment, `GOLDILOCKS_AGENT_MLIP_ENABLED` plus
  the `mlip_cli_venv` volume uncommented in
  `deploy/stfc-cloud/docker-compose.yml`.
- **AiiDA** is not built yet.
- Drag a structure file (CIF/XYZ/POSCAR/VASP/XSF/CUBE) onto the window, or
  use a Tool panel's own "Upload structure" button, to add it to the
  current chat.
