import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  HttpCoreClient,
  MantineProvider,
  WorkbenchContent as CoreWorkbenchContent,
  WorkspaceProvider as CoreWorkspaceProvider,
  colorSchemeManager as coreColorSchemeManager,
  createWorkspace as createCoreWorkspace,
  workbenchTheme,
} from "goldilocks-workbench";
import "goldilocks-workbench/style.css";
import WeasStructureViewport from "./components/WeasStructureViewport";
import {
  WEAS_SUPPORTED_EXTS,
  formatStructureLabel,
  getRawFileExtension,
  getStructureFenceLanguage,
  inferStructureExtension,
} from "./utils/structureFiles";

// React's CSSProperties type doesn't include arbitrary custom properties
// (e.g. `--tool-color`, read by App.css) -- this widened alias documents
// the handful of `style` objects below that set one intentionally.
type CSSPropertiesWithVars = CSSProperties & Record<`--${string}`, string | number>;

// --- Core recurring data shapes -------------------------------------------
// These mirror the informal shapes already implied by createSession(),
// the `role`/`content` literals sent to the backend, and the per-Tool
// `modeState` slices (DEFAULT_MLIP_STATE/DEFAULT_DFT_STATE/etc. below).
// Backend payloads (`content`, `modeState[...]`, tool-call results) are
// still genuinely dynamic JSON from goldilocks-agent's Python side and the
// underlying LLM/tool responses, so fields that carry those stay `any`/
// loosely-typed with an index signature rather than fully modeled --
// getting those exactly right would mean keeping this in lockstep with the
// Python backend's schemas, which is out of scope for a frontend-only,
// types-only migration under tonight's deadline.

// A chat message as stored in `session.messages`. `content` is either a
// plain string (the common case) or -- when rehydrated straight from the
// LangGraph checkpointer -- a multimodal "parts" array (see
// getMessageDisplayParts/extractStructureFromMessageContent for the actual
// shape-sniffing this ambiguity forces on every reader).
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: any;
  display?: string;
  images?: { name: string; dataUrl?: string; url?: string }[];
  [key: string]: any;
}

// A project groups chats together (see handleCreateProject/loadProjects).
// `sources` is client-side-only for now -- the `projects` table has no
// column for it yet (design doc 16.5).
export interface Project {
  id: string;
  name: string;
  desc?: string;
  color: string;
  sources?: any;
  [key: string]: any;
}

// One chat/conversation. `modeState` is a per-Tool bag (keyed by Tool id,
// e.g. "ml-analysis"/"dft-workbench"/"structure-search") whose shape is
// defined per-Tool by DEFAULT_MLIP_STATE/DEFAULT_DFT_STATE/etc. above and
// below -- deliberately untyped here (`Record<string, any>`) rather than a
// union of all of them, since each Tool only ever reads its own key.
export interface Session {
  id: string;
  title: string;
  messages: ChatMessage[];
  messagesLoaded?: boolean;
  createdAt: string;
  projectId: string | null;
  tool: string | null;
  rightPanelOpen: boolean;
  rightPanelView: string | null;
  modeState: Record<string, any>;
  [key: string]: any;
}

// Order (both the group list and each group's items) is the user's explicit
// priority (2026-09-15): Claude > OpenAI > Gemini > Ollama (local) >
// Goldilocks LLM. `selectedModel`'s default (`MODEL_GROUPS[0].items[0]`)
// falls straight out of this ordering -- Claude is the default engine.
const MODEL_GROUPS = [
  {
    group: "Cloud (requires API key in Settings)",
    items: [
      { id: "anthropic-claude", label: "Claude", desc: "Requires an Anthropic API key", tag: "anthropic" },
      { id: "openai-gpt", label: "OpenAI", desc: "Requires an OpenAI API key", tag: "openai" },
      { id: "google-gemini", label: "Gemini", desc: "Requires a Google API key", tag: "google" },
    ],
  },
  {
    group: "Local (Ollama)",
    items: [
      { id: "qwen3.8-27b", label: "Qwen3.8-27B", desc: "Local foundation model — runs on this machine", tag: "default" },
    ],
  },
  {
    group: "Fine-Tuned Models",
    items: [
      // No backend routing exists for this id yet -- `disabled: true` keeps
      // it from silently falling back to the local model like the old
      // `goldilocks-dft`/`goldilocks-mlip` placeholders did. Wire it up for
      // real once `goldilocks-ml` actually publishes a fine-tune (design doc
      // 十一之四: PSDI verification chain, only then is this selectable).
      { id: "goldilocks-llm", label: "Goldilocks LLM", desc: "Coming soon — not available yet", tag: "default", disabled: true },
    ],
  },
];

// Mirrors goldilocks_agent.tools.structure_search.grouping._PROPERTY_FIELDS --
// only the fields a CandidateGroup can actually carry, in display order.
const DB_PROPERTY_DISPLAY = [
  { key: "band_gap", label: "Band gap", unit: "eV", methodKey: "band_gap_method" },
  { key: "formation_energy_per_atom", label: "Formation energy", unit: "eV/atom" },
  { key: "energy_above_hull", label: "E above hull", unit: "eV/atom" },
  { key: "density", label: "Density", unit: "g/cm³" },
  { key: "total_magnetization", label: "Total magnetization", unit: "μB" },
  { key: "bulk_modulus", label: "Bulk modulus", unit: "GPa" },
  { key: "shear_modulus", label: "Shear modulus", unit: "GPa" },
  { key: "refractive_index", label: "Refractive index", unit: "" },
  { key: "dielectric_constant", label: "Dielectric constant", unit: "" },
];

function formatDbPropertyValue(value) {
  if (typeof value !== "number") return String(value);
  // Trim to ~4 significant figures without scientific notation for the
  // common range these properties live in, then drop trailing zeros.
  return value.toPrecision(4).replace(/\.?0+$/, "").replace(/\.$/, "");
}

// `session.modeState["structure-search"]`'s shape -- per-chat so switching
// chats doesn't leak one chat's search into another, and `searchHistory`
// keeps every search run in this chat (not just the latest).
const DEFAULT_STRUCTURE_SEARCH_STATE = {
  formula: "",
  queryMode: "structure",
  propertyTags: [],
  searchHistory: [], // [{ formula, properties, result }]
  activeSearchIndex: -1, // -1 = "most recent"
  importedEntries: [], // "source:entry_id" keys already imported in this chat
};

// Appends one search to history and syncs `formula` to it -- shared by all
// three ways a search can land (panel button, element-picker shortcut, LLM
// tool call from chat) so the formula box always matches what's on screen,
// not just whatever the user last typed by hand.
function withStructureSearchEntry(prevState, entry) {
  const prev = prevState ?? DEFAULT_STRUCTURE_SEARCH_STATE;
  const history = [...prev.searchHistory, entry];
  return {
    ...prev,
    formula: entry.formula ?? prev.formula,
    // Every entry that reaches this function came from a formula search
    // (the "Structure" file-mode 501 path never calls it) -- switch the
    // toggle so the panel actually shows the result instead of leaving
    // "Structure" mode selected with nothing behind it.
    queryMode: "formula",
    searchHistory: history,
    activeSearchIndex: history.length - 1,
  };
}

// Maps an LLM tool's raw name to the UI Tool id whose panel should pop open
// when that tool is called -- the only place this mapping has to grow when
// a second real Tool gets wired up.
const TOOL_CALL_TO_UI_TOOL = {
  find_in_databases: "structure-search",
  get_structure: "structure-search",
  run_mlip_singlepoint: "ml-analysis",
  run_mlip_geometry_optimization: "ml-analysis",
  run_mlip_equation_of_state: "ml-analysis",
  run_mlip_neb: "ml-analysis",
  run_mlip_phonons: "ml-analysis",
  dft_explain: "dft-workbench",
  dft_generate: "dft-workbench",
};

// Only MACE is actually wired up (janus-core supports more, but exposing a
// picker option that silently can't work is the same "placeholder brand"
// problem already fixed once for the model selector -- so the list only
// grows once a potential is real here, not before).
const MLIP_MODELS = [
  { id: "mace-mp", label: "MACE-MP-0", desc: "Universal foundation model" },
];

// `session.modeState["ml-analysis"]`'s shape -- per-chat so switching chats
// doesn't leak one chat's calc-type/params/results into another (the same
// bug class fixed for structure-search's formula box).
const DEFAULT_MLIP_STATE = {
  selectedMlipModelId: MLIP_MODELS[0].id,
  mlipCalcType: "singlepoint",
  mlipResultsList: [],
  mlipStructIdx: 0,
  mlipNebInitIdx: 0,
  mlipNebFinalIdx: 1,
  mlipFmax: 0.1,
  mlipSteps: 1000,
  mlipRelaxMode: "ionic",
  mlipSupercell: 2,
  mlipDisplacement: 0.01,
  mlipMinVol: 0.95,
  mlipMaxVol: 1.05,
  mlipNVolumes: 7,
  mlipNImages: 15,
  mlipNebFmax: 0.1,
};

// Beyond DFT's own "Code"/"Machine" pickers are purely decorative context
// (it already carries the honest `.workspace-no-backing` badge -- nothing
// it shows is backed by a real API either way), kept deliberately separate
// from DFT Workbench's real, session-scoped state above so redoing DFT
// Workspace against v2 doesn't have to touch Beyond DFT at all. `scarf` is
// the one real HPC profile confirmed installed on this checkout (verified
// 2026-09-15 via `/capabilities`'s `hpc_profiles[]`) -- not a discovered
// list, just a single honest suggestion for an already-unbacked panel.
const DFT_HPC_GROUPS = [
  {
    label: "HPC profile",
    items: [
      { id: "scarf", label: "SCARF", desc: "STFC's in-house HPC cluster at Rutherford Appleton Laboratory.", recommended: true },
    ],
  },
];

const DFT_ADVISOR_MODELS = [];

const MODEL_TAG_COLORS = {
  default: "#2b7de0",
  openai: "#74aa9c",
  anthropic: "#c07a4a",
  google: "#4285f4",
};

const EXPERIENCE_OPTIONS = [
  {
    id: "new",
    title: "New to computational materials",
    desc: "I'm new to computational materials and want more guidance.",
  },
  {
    id: "familiar",
    title: "Familiar with workflows",
    desc: "I know the basics and want practical help with common workflows.",
  },
  {
    id: "advanced",
    title: "Advanced user",
    desc: "I'm comfortable with computational materials workflows and want concise, expert-oriented responses.",
  },
];

const TOOLS = [
  {
    id: "structure-search",
    icon: "🔭",
    label: "Find in Databases",
    color: "#8b5cf6",
    desc: "Search materials databases for structures matching or similar to the one you provide.",
    launcherDesc: "Search Materials Cloud, Materials Project, NOMAD, and JARVIS",
    placeholder: "Describe a structure or attach a file to find matching materials and database links.",
    defaultPanel: "results",
  },
  {
    id: "ml-analysis",
    icon: "🤖",
    label: "MLIP Playground",
    color: "#10b981",
    desc: "Use machine learning interatomic potentials to explore materials research, compare predictions, and experiment with behaviour.",
    launcherDesc: "Explore materials research with machine learning interatomic potentials",
    placeholder: "Upload structures or results to explore machine learning interatomic potential behaviour and compare predictions.",
    defaultPanel: "analysis",
  },
  {
    id: "dft-workbench",
    icon: "📄",
    label: "DFT Workbench",
    color: "#f59e0b",
    desc: "Ask anything about DFT workflows, inputs, convergence, and results. Goldilocks provides guidance rather than running jobs.",
    launcherDesc: "Guidance for DFT workflows, inputs, convergence, and results",
    placeholder: "Ask anything about DFT methods, inputs, convergence, errors, or how to interpret results.",
    defaultPanel: "setup",
  },
  {
    id: "beyond-dft",
    icon: "⚡",
    label: "Beyond DFT",
    color: "#3b82f6",
    desc: "Guidance for methods beyond standard DFT: GW, BSE, QMC, TDDFT, Wannier, DFT+DMFT, QM/MM, and more.",
    launcherDesc: "GW, BSE, QMC, TDDFT, Wannier, DMFT, QM/MM, and beyond",
    placeholder: "Ask about cutting-edge methods, when to use them, and how to set them up.",
    defaultPanel: "setup",
  },
  {
    id: "post-analysis",
    icon: "📊",
    label: "Post Analysis",
    color: "#ec4899",
    desc: "Parse, plot, and interpret results from calculations you've already run — whether from this session or brought in from elsewhere.",
    launcherDesc: "Parse and interpret DFT/MLIP outputs, plots, and convergence data",
    placeholder: "Upload or describe your output files (pw.out, OUTCAR, ...) and ask what they mean.",
    defaultPanel: "setup",
  },
  {
    id: "aiida",
    icon: "⚙️",
    label: "AiiDA",
    color: "#14b8a6",
    desc: "Browse and monitor your AiiDA workflows — submissions, status, and provenance across your profile.",
    launcherDesc: "Monitor AiiDA processes, diagnose failures, and browse provenance",
    placeholder: "Ask about a running or past AiiDA process, or check on a submitted calculation.",
    defaultPanel: "setup",
  },
];

const TOOL_ICON_SOURCES = {
  "dft-workbench": "/mode-icons/dft.svg",
  "ml-analysis": "/mode-icons/janus-core.png",
  "structure-search": "/mode-icons/structure-search.svg",
  "beyond-dft": "/mode-icons/beyond-dft.svg",
  "post-analysis": "/mode-icons/post-analysis.svg",
  aiida: "/mode-icons/aiida.png",
};

// Real vocabulary, replacing a fake v1 catalog (17 codes, 19 tasks, 10 UK
// HPC machines, none of which exist in real goldilocks-core v2 -- see
// design doc twelve / implementation plan Step 2, 2026-09-15 redone).
// `codes`/`tasks` are hardcoded here (not fetched) only because
// goldilocks-core's CLI has no single command returning them the way
// HTTP/MCP's `/capabilities` does (goldilocks-core#62) -- `--task`'s own
// argparse `choices` is the source for the 4 task ids below, so this list
// only grows when core's CLI actually adds one, not from a guess.
const DFT_CODE_GROUPS = [
  {
    label: "Codes",
    items: [
      { id: "quantum_espresso", label: "Quantum ESPRESSO", desc: "The only code goldilocks-core v2 generates inputs for today.", recommended: true },
    ],
  },
];

const DFT_TASK_GROUPS = [
  {
    label: "Tasks",
    items: [
      { id: "scf_single_point", label: "Single-point SCF", desc: "One self-consistent-field calculation, no relaxation.", recommended: true },
      { id: "dos", label: "Density of states", desc: "scf, then a denser nscf pass, then dos.x -- three steps sharing one prefix/outdir." },
      { id: "relax", label: "Ionic relaxation", desc: "One pw.x run, calculation='relax': scf plus ionic-position optimisation." },
      { id: "vc-relax", label: "Variable-cell relaxation", desc: "One pw.x run, calculation='vc-relax': scf plus ionic and cell relaxation together." },
    ],
  },
];

// `session.modeState["dft-workbench"]`'s shape -- same per-chat fix as MLIP/
// structure-search. `overrides` mirrors goldilocks-core's own sparse
// `--set`/`overrides: {}` model (advisors auto-resolve everything not
// listed here) -- not a flat copy of every setting like the old fake
// per-setting pickers were. Must be declared after DFT_CODE_GROUPS/
// DFT_TASK_GROUPS (2026-09-15: an earlier version of this file declared it
// *before* them, a temporal-dead-zone ReferenceError at module load that
// blanked the whole app -- both are plain top-level `const`s evaluated in
// file order, not hoisted the way function declarations are).
const DEFAULT_DFT_STATE = {
  code: DFT_CODE_GROUPS[0].items[0].id,
  task: DFT_TASK_GROUPS[0].items[0].id,
  hpc: "", // free-text override -- hpc_profiles[] isn't reachable via CLI, goldilocks-core#62
  overrides: {}, // { settingKey: value }
  explainResult: null, // { records, warnings } from the last /api/dft/explain
  runResult: null, // { files } from the last /api/dft/run
};

const BEYOND_DFT_METHOD_GROUPS = [
  {
    label: "Correlated and corrected DFT",
    items: [
      { id: "dft-u", label: "DFT+U", desc: "Hubbard U correction for strongly correlated d/f-electron systems." },
      { id: "dmft", label: "DFT+DMFT", desc: "Dynamical mean-field theory — captures dynamic correlations in strongly correlated d/f-electron systems beyond the static DFT+U picture." },
      { id: "hybrid", label: "Hybrid functionals", desc: "PBE0, HSE06 — partial exact exchange for better band gaps and energetics." },
      { id: "sic", label: "SIC", desc: "Self-interaction correction to remove spurious self-repulsion in DFT." },
    ],
  },
  {
    label: "Many-body perturbation theory",
    items: [
      { id: "gw", label: "GW", desc: "Green's function + screened Coulomb for quasiparticle energies and band structures.", recommended: true },
      { id: "bse", label: "BSE", desc: "Bethe-Salpeter equation for optical spectra and excitons." },
      { id: "rpa", label: "RPA", desc: "Random phase approximation for accurate correlation energies." },
      { id: "mp2", label: "MP2", desc: "Second-order Møller-Plesset perturbation theory." },
    ],
  },
  {
    label: "Time-dependent",
    items: [
      { id: "tddft", label: "TDDFT", desc: "Time-dependent DFT for optical response, excitations, and dynamics." },
    ],
  },
  {
    label: "Quantum Monte Carlo",
    items: [
      { id: "qmc", label: "QMC", desc: "Diffusion or variational Monte Carlo for benchmark-quality total energies." },
    ],
  },
  {
    label: "Model and embedding",
    items: [
      { id: "mft", label: "MFT", desc: "Mean-field theory approaches for magnetic and exchange interactions." },
      { id: "wannier", label: "Wannier functions", desc: "Maximally localised Wannier functions for tight-binding, transport, and topological analysis." },
      { id: "qmmm", label: "QM/MM", desc: "Quantum mechanics / molecular mechanics — embeds a DFT region in a classical force-field environment for large systems such as surface reactions or biomolecules." },
    ],
  },
];

function findOptionInGroups(groups, id) {
  for (const group of groups) {
    const match = group.items.find((item) => item.id === id);
    if (match) return match;
  }
  return null;
}

const PROJECT_COLORS = ["#f59e0b", "#3b82f6", "#10b981", "#8b5cf6", "#ef4444", "#14b8a6"];

const PERIODIC_TABLE_ROWS = [
  ["H", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "He"],
  ["Li", "Be", "", "", "", "", "", "", "", "", "", "", "B", "C", "N", "O", "F", "Ne"],
  ["Na", "Mg", "", "", "", "", "", "", "", "", "", "", "Al", "Si", "P", "S", "Cl", "Ar"],
  ["K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr"],
  ["Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe"],
  ["Cs", "Ba", "La", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn"],
  ["Fr", "Ra", "Ac", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og"],
  ["", "", "", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu"],
  ["", "", "", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"],
];
const ELEMENT_SYMBOLS = PERIODIC_TABLE_ROWS.flat().filter(Boolean);
const ELEMENT_CATEGORY_GROUPS = {
  alkali: ["Li", "Na", "K", "Rb", "Cs", "Fr"],
  alkaline: ["Be", "Mg", "Ca", "Sr", "Ba", "Ra"],
  transition: [
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
  ],
  post: ["Al", "Ga", "In", "Tl", "Sn", "Pb", "Bi", "Nh", "Fl", "Mc", "Lv"],
  metalloid: ["B", "Si", "Ge", "As", "Sb", "Te", "Po"],
  nonmetal: ["H", "C", "N", "O", "P", "S", "Se"],
  halogen: ["F", "Cl", "Br", "I", "At", "Ts"],
  noble: ["He", "Ne", "Ar", "Kr", "Xe", "Rn", "Og"],
  lanthanide: ["La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu"],
  actinide: ["Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"],
};

const STORAGE_KEYS = {
  experience: "goldilocks-experience-level",
  theme: "goldilocks-theme-choice",
  language: "goldilocks-language",
  sidebarWidth: "goldilocks-sidebar-width",
  toolsWidth: "goldilocks-tools-width",
};

const TRANSLATIONS = {
  en: {
    // Navigation
    new_chat: "New chat", projects: "Projects", new_project: "New project",
    open_project: "Open project", new_chat_in_project: "New chat in project",
    chats: "Chats", sources: "Sources", general_chat: "General chat",
    search: "Search", see_less: "See less",
    // Settings
    settings: "Settings", profile: "Profile", experience_level: "Experience level",
    not_selected: "Not selected yet", change: "Change",
    contribute_heading: "I want to contribute!",
    contribute_desc: "Found a bug, have an idea, or want to collaborate? Raise a GitHub issue or reach out to the team directly — we'd love to hear from you.",
    github_issues: "GitHub Issues — report bugs or request features",
    acknowledgements: "Acknowledgements",
    ack_desc: "Goldilocks is a collaborative effort. We thank everyone who has contributed their time, expertise, and ideas.",
    ack_note: "Alphabetical order · no ranking implied",
    language: "Language", save: "Save", cancel: "Cancel", confirm: "Confirm",
    // Onboarding & experience
    welcome: "Welcome to Goldilocks",
    welcome_desc: "Choose the option that best describes your experience with computational materials research. This helps Goldilocks tailor its responses.",
    continue: "Continue",
    exp_new_title: "New to computational materials",
    exp_new_desc: "I'm new to computational materials and want more guidance.",
    exp_familiar_title: "Familiar with workflows",
    exp_familiar_desc: "I know the basics and want practical help with common workflows.",
    exp_advanced_title: "Advanced user",
    exp_advanced_desc: "I'm comfortable with computational materials workflows and want concise, expert-oriented responses.",
    // Projects
    projects_desc: "Group chats, structures, and workflows into focused research spaces.",
    no_chats_yet: "No chats yet. Type in the chat box below to start one.",
    no_sources_yet: "No sources added yet.",
    create_project: "Create project", project_name: "Project name",
    project_desc_label: "Description (optional)",
    add_to_project: "Add to Project", choose_project: "Choose a project to save this chat into.",
    no_projects_yet: "No projects yet.", create_project_first: "Create a project first using the New project button in the sidebar.",
    // Workspace
    pending: "Pending", powered_by: "Powered by",
    task_builder: "Task builder",
    structure_or_formula: "Structure or formula", formula: "Formula",
    query_structure: "Query structure", candidate_structures: "Candidate structures",
    search_databases: "Search databases", searching: "Searching…",
    no_structure_in_chat: "No structure in chat",
    no_structure_msg: "No structure in this chat yet. Attach a file in the chat first.",
    dismiss_tool: "Dismiss tool",
    generated_preview: "Generated preview", validation: "Validation",
    mlip_model: "MLIP Model",
    analysis_summary: "Analysis summary",
    analysis_prototype: "Prototype panel for convergence trends, anomaly highlighting, and dataset comparison.",
    derived_computation: "Derived computation",
    reference_value: "Reference value", predicted_value: "Predicted value", computed_delta: "Computed delta",
    tab_setup: "Setup", tab_inputs: "Inputs", tab_checks: "Explain",
    tab_analysis: "Analysis", tab_metrics: "Metrics", tab_compute: "Compute",
    // Mode launcher descriptions
    tool_structure_search_launcher: "Search Materials Cloud, Materials Project, NOMAD, and JARVIS",
    tool_ml_analysis_launcher: "Explore materials research with machine learning interatomic potentials",
    tool_dft_workbench_launcher: "Guidance for DFT workflows, inputs, convergence, and results",
    tool_beyond_dft_launcher: "GW, BSE, QMC, TDDFT, Wannier, DMFT, QM/MM, and beyond",
    tool_post_analysis_launcher: "Parse and interpret DFT/MLIP outputs, plots, and convergence data",
    tool_aiida_launcher: "Monitor AiiDA processes, diagnose failures, and browse provenance",
    settings_model_heading: "Model",
    settings_model_desc: "Qwen3.8-27B runs locally by default — your structures and conversations never leave this machine. Add a cloud API key below only if you want to switch to OpenAI, Claude, or Gemini for a chat.",
    settings_model_key_placeholder: "Not set",
    settings_model_key_hint: "Stored locally on this machine only — a key is only sent to the provider it belongs to, never anywhere else.",
    settings_databases_heading: "Databases",
    settings_databases_desc: "Materials Project needs a free API key to search. Get one at materialsproject.org/api.",
    settings_compute_heading: "Compute",
    settings_compute_label: "AiiDA / HPC connection",
    settings_compute_connected: "Connected — Goldilocks can submit and monitor calculations for you",
    settings_compute_disconnected: "Not connected — Goldilocks can still prepare input files for you to run yourself",
    settings_compute_connect: "Connect",
    settings_compute_disconnect: "Disconnect",
    beyond_dft_no_backing: "No goldilocks-core backing",
    beyond_dft_no_backing_hint: "goldilocks-core doesn't cover these methods. What you see here are common starting points from the literature, not recommended values — you must verify convergence yourself.",
  },
  fr: {
    new_chat: "Nouvelle conversation", projects: "Projets", new_project: "Nouveau projet",
    open_project: "Ouvrir le projet", new_chat_in_project: "Nouvelle conversation dans le projet",
    chats: "Conversations", sources: "Sources", general_chat: "Chat général",
    search: "Rechercher", see_less: "Voir moins",
    settings: "Paramètres", profile: "Profil", experience_level: "Niveau d'expérience",
    not_selected: "Non sélectionné", change: "Modifier",
    contribute_heading: "Je veux contribuer !",
    contribute_desc: "Un bug, une idée ou envie de collaborer ? Ouvrez un ticket GitHub ou contactez l'équipe directement.",
    github_issues: "GitHub Issues — signaler des bugs ou proposer des fonctionnalités",
    acknowledgements: "Remerciements",
    ack_desc: "Goldilocks est un effort collaboratif. Nous remercions tous ceux qui ont contribué leur temps, expertise et idées.",
    ack_note: "Ordre alphabétique · aucun classement implicite",
    language: "Langue", save: "Enregistrer", cancel: "Annuler", confirm: "Confirmer",
    welcome: "Bienvenue sur Goldilocks",
    welcome_desc: "Choisissez l'option qui décrit le mieux votre expérience en recherche computationnelle des matériaux. Cela aide Goldilocks à adapter ses réponses.",
    continue: "Continuer",
    exp_new_title: "Nouveau en matériaux computationnels",
    exp_new_desc: "Je suis nouveau en matériaux computationnels et souhaite plus de conseils.",
    exp_familiar_title: "Familier avec les workflows",
    exp_familiar_desc: "Je connais les bases et veux une aide pratique pour les workflows courants.",
    exp_advanced_title: "Utilisateur avancé",
    exp_advanced_desc: "Je maîtrise les workflows et souhaite des réponses concises orientées expert.",
    projects_desc: "Regroupez conversations, structures et workflows dans des espaces de recherche dédiés.",
    no_chats_yet: "Pas encore de conversation. Tapez ci-dessous pour en démarrer une.",
    no_sources_yet: "Aucune source ajoutée pour l'instant.",
    create_project: "Créer un projet", project_name: "Nom du projet",
    project_desc_label: "Description (facultative)",
    add_to_project: "Ajouter au projet", choose_project: "Choisissez un projet pour y enregistrer cette conversation.",
    no_projects_yet: "Aucun projet pour l'instant.", create_project_first: "Créez d'abord un projet via le bouton Nouveau projet dans la barre latérale.",
    pending: "En attente", powered_by: "Propulsé par",
    task_builder: "Constructeur de tâches",
    structure_or_formula: "Structure ou formule", formula: "Formule",
    query_structure: "Structure requête", candidate_structures: "Structures candidates",
    search_databases: "Rechercher dans les bases", searching: "Recherche…",
    no_structure_in_chat: "Aucune structure dans le chat",
    no_structure_msg: "Aucune structure dans ce chat. Joignez d'abord un fichier dans le chat.",
    dismiss_tool: "Fermer l'outil",
    generated_preview: "Aperçu généré", validation: "Validation",
    mlip_model: "Modèle MLIP",
    analysis_summary: "Résumé de l'analyse",
    analysis_prototype: "Panneau prototype pour tendances de convergence, anomalies et comparaison de jeux de données.",
    derived_computation: "Calcul dérivé",
    reference_value: "Valeur de référence", predicted_value: "Valeur prédite", computed_delta: "Delta calculé",
    tab_setup: "Configuration", tab_inputs: "Entrées", tab_checks: "Explication",
    tab_analysis: "Analyse", tab_metrics: "Métriques", tab_compute: "Calcul",
    tool_structure_search_launcher: "Rechercher dans Materials Cloud, Materials Project, NOMAD et JARVIS",
    tool_ml_analysis_launcher: "Explorer la recherche sur les matériaux avec des potentiels interatomiques ML",
    tool_dft_workbench_launcher: "Aide pour les workflows DFT, entrées, convergence et résultats",
    tool_beyond_dft_launcher: "GW, BSE, QMC, TDDFT, Wannier, DMFT, QM/MM et au-delà",
    tool_post_analysis_launcher: "Analyser et interpréter les sorties DFT/MLIP, les graphiques et les données de convergence",
    tool_aiida_launcher: "Surveiller les processus AiiDA, diagnostiquer les échecs et parcourir la provenance",
    settings_model_heading: "Modèle",
    settings_model_desc: "Qwen3.8-27B s'exécute localement par défaut — vos structures et conversations ne quittent jamais cette machine. Ajoutez une clé API cloud ci-dessous uniquement si vous souhaitez utiliser OpenAI, Claude ou Gemini pour une conversation.",
    settings_model_key_placeholder: "Non définie",
    settings_model_key_hint: "Stockée uniquement en local sur cette machine — une clé n'est envoyée qu'au fournisseur auquel elle appartient, jamais ailleurs.",
    settings_databases_heading: "Bases de données",
    settings_databases_desc: "Materials Project nécessite une clé API gratuite pour la recherche. Obtenez-en une sur materialsproject.org/api.",
    settings_compute_heading: "Calcul",
    settings_compute_label: "Connexion AiiDA / HPC",
    settings_compute_connected: "Connecté — Goldilocks peut soumettre et surveiller des calculs pour vous",
    settings_compute_disconnected: "Non connecté — Goldilocks peut tout de même préparer les fichiers d'entrée que vous exécuterez vous-même",
    settings_compute_connect: "Connecter",
    settings_compute_disconnect: "Déconnecter",
    beyond_dft_no_backing: "Aucun appui de goldilocks-core",
    beyond_dft_no_backing_hint: "goldilocks-core ne couvre pas ces méthodes. Ce qui est affiché ici correspond à des points de départ courants de la littérature, pas à des valeurs recommandées — vous devez vérifier vous-même la convergence.",
  },
  de: {
    new_chat: "Neuer Chat", projects: "Projekte", new_project: "Neues Projekt",
    open_project: "Projekt öffnen", new_chat_in_project: "Neuer Chat im Projekt",
    chats: "Chats", sources: "Quellen", general_chat: "Allgemeiner Chat",
    search: "Suchen", see_less: "Weniger anzeigen",
    settings: "Einstellungen", profile: "Profil", experience_level: "Erfahrungsstufe",
    not_selected: "Noch nicht ausgewählt", change: "Ändern",
    contribute_heading: "Ich möchte beitragen!",
    contribute_desc: "Einen Bug gefunden oder eine Idee? Erstellen Sie ein GitHub-Issue oder wenden Sie sich direkt an das Team.",
    github_issues: "GitHub Issues — Bugs melden oder Funktionen vorschlagen",
    acknowledgements: "Danksagungen",
    ack_desc: "Goldilocks ist ein gemeinschaftliches Projekt. Wir danken allen, die Zeit, Fachwissen und Ideen eingebracht haben.",
    ack_note: "Alphabetische Reihenfolge · keine Rangfolge impliziert",
    language: "Sprache", save: "Speichern", cancel: "Abbrechen", confirm: "Bestätigen",
    welcome: "Willkommen bei Goldilocks",
    welcome_desc: "Wählen Sie die Option, die Ihre Erfahrung mit computergestützter Materialforschung am besten beschreibt. Dies hilft Goldilocks, seine Antworten anzupassen.",
    continue: "Weiter",
    exp_new_title: "Neu in der computergestützten Materialforschung",
    exp_new_desc: "Ich bin neu und möchte mehr Anleitung.",
    exp_familiar_title: "Mit Workflows vertraut",
    exp_familiar_desc: "Ich kenne die Grundlagen und möchte praktische Hilfe bei gängigen Workflows.",
    exp_advanced_title: "Erfahrener Nutzer",
    exp_advanced_desc: "Ich bin vertraut mit Materialworkflows und möchte präzise, expertenorientierte Antworten.",
    projects_desc: "Gruppieren Sie Chats, Strukturen und Workflows in fokussierte Forschungsbereiche.",
    no_chats_yet: "Noch keine Chats. Tippen Sie unten, um einen zu starten.",
    no_sources_yet: "Noch keine Quellen hinzugefügt.",
    create_project: "Projekt erstellen", project_name: "Projektname",
    project_desc_label: "Beschreibung (optional)",
    add_to_project: "Zum Projekt hinzufügen", choose_project: "Wählen Sie ein Projekt, um diesen Chat zu speichern.",
    no_projects_yet: "Noch keine Projekte.", create_project_first: "Erstellen Sie zuerst ein Projekt über die Schaltfläche Neues Projekt in der Seitenleiste.",
    pending: "Ausstehend", powered_by: "Unterstützt von",
    task_builder: "Aufgaben-Builder",
    structure_or_formula: "Struktur oder Formel", formula: "Formel",
    query_structure: "Abfragestruktur", candidate_structures: "Kandidatenstrukturen",
    search_databases: "Datenbanken durchsuchen", searching: "Suche…",
    no_structure_in_chat: "Keine Struktur im Chat",
    no_structure_msg: "Noch keine Struktur in diesem Chat. Fügen Sie zuerst eine Datei im Chat an.",
    dismiss_tool: "Werkzeug schließen",
    generated_preview: "Generierte Vorschau", validation: "Validierung",
    mlip_model: "MLIP-Modell",
    analysis_summary: "Analyseübersicht",
    analysis_prototype: "Prototyp-Panel für Konvergenztrends, Anomalie-Hervorhebung und Datensatzvergleich.",
    derived_computation: "Abgeleitete Berechnung",
    reference_value: "Referenzwert", predicted_value: "Vorhergesagter Wert", computed_delta: "Berechnetes Delta",
    tab_setup: "Einrichtung", tab_inputs: "Eingaben", tab_checks: "Erklärung",
    tab_analysis: "Analyse", tab_metrics: "Metriken", tab_compute: "Berechnen",
    tool_structure_search_launcher: "In Materials Cloud, Materials Project, NOMAD und JARVIS suchen",
    tool_ml_analysis_launcher: "Materialforschung mit maschinellen interatomaren Potentialen erkunden",
    tool_dft_workbench_launcher: "Anleitung für DFT-Workflows, Eingaben, Konvergenz und Ergebnisse",
    tool_beyond_dft_launcher: "GW, BSE, QMC, TDDFT, Wannier, DMFT, QM/MM und darüber hinaus",
    tool_post_analysis_launcher: "DFT/MLIP-Ausgaben, Diagramme und Konvergenzdaten analysieren und interpretieren",
    tool_aiida_launcher: "AiiDA-Prozesse überwachen, Fehler diagnostizieren und Herkunft durchsuchen",
    settings_model_heading: "Modell",
    settings_model_desc: "Qwen3.8-27B läuft standardmäßig lokal — Ihre Strukturen und Unterhaltungen verlassen diesen Rechner nie. Fügen Sie unten nur dann einen Cloud-API-Schlüssel hinzu, wenn Sie für einen Chat zu OpenAI, Claude oder Gemini wechseln möchten.",
    settings_model_key_placeholder: "Nicht festgelegt",
    settings_model_key_hint: "Wird nur lokal auf diesem Rechner gespeichert — ein Schlüssel wird ausschließlich an den zugehörigen Anbieter gesendet, niemals anderswohin.",
    settings_databases_heading: "Datenbanken",
    settings_databases_desc: "Materials Project benötigt einen kostenlosen API-Schlüssel für die Suche. Holen Sie sich einen unter materialsproject.org/api.",
    settings_compute_heading: "Rechenressourcen",
    settings_compute_label: "AiiDA-/HPC-Verbindung",
    settings_compute_connected: "Verbunden — Goldilocks kann Berechnungen für Sie einreichen und überwachen",
    settings_compute_disconnected: "Nicht verbunden — Goldilocks kann dennoch Eingabedateien vorbereiten, die Sie selbst ausführen",
    settings_compute_connect: "Verbinden",
    settings_compute_disconnect: "Trennen",
    beyond_dft_no_backing: "Keine goldilocks-core-Unterstützung",
    beyond_dft_no_backing_hint: "goldilocks-core deckt diese Methoden nicht ab. Was Sie hier sehen, sind übliche Ausgangspunkte aus der Literatur, keine Empfehlungswerte — die Konvergenz müssen Sie selbst prüfen.",
  },
  zh: {
    new_chat: "新建对话", projects: "项目", new_project: "新建项目",
    open_project: "打开项目", new_chat_in_project: "在项目中新建对话",
    chats: "对话", sources: "来源", general_chat: "普通对话",
    search: "搜索", see_less: "收起",
    settings: "设置", profile: "个人资料", experience_level: "经验等级",
    not_selected: "尚未选择", change: "修改",
    contribute_heading: "我想贡献！",
    contribute_desc: "发现了 bug，有新想法，或者想合作？在 GitHub 提 issue 或直接联系团队——我们很乐意听取意见。",
    github_issues: "GitHub Issues — 报告 bug 或请求功能",
    acknowledgements: "致谢",
    ack_desc: "Goldilocks 是一项协作成果。我们感谢所有贡献了时间、专业知识和想法的人。",
    ack_note: "字母顺序 · 无排名含义",
    language: "语言", save: "保存", cancel: "取消", confirm: "确认",
    welcome: "欢迎使用 Goldilocks",
    welcome_desc: "选择最能描述您在计算材料研究方面经验的选项，这将帮助 Goldilocks 为您定制回答。",
    continue: "继续",
    exp_new_title: "计算材料研究新手",
    exp_new_desc: "我是计算材料研究新手，需要更多指导。",
    exp_familiar_title: "熟悉工作流程",
    exp_familiar_desc: "我了解基础知识，需要常见工作流程的实用帮助。",
    exp_advanced_title: "高级用户",
    exp_advanced_desc: "我熟悉计算材料工作流程，希望获得简洁、以专家为导向的回答。",
    projects_desc: "将对话、结构和工作流程整理到专注的研究空间中。",
    no_chats_yet: "暂无对话。在下方输入框中开始第一条对话。",
    no_sources_yet: "尚未添加来源。",
    create_project: "创建项目", project_name: "项目名称",
    project_desc_label: "描述（可选）",
    add_to_project: "添加到项目", choose_project: "选择一个项目以保存此对话。",
    no_projects_yet: "暂无项目。", create_project_first: "请先通过侧边栏的「新建项目」按钮创建项目。",
    pending: "待发送", powered_by: "技术支持：",
    task_builder: "任务构建器",
    structure_or_formula: "结构或化学式", formula: "化学式",
    query_structure: "查询结构", candidate_structures: "候选结构",
    search_databases: "搜索数据库", searching: "搜索中…",
    no_structure_in_chat: "聊天中无结构",
    no_structure_msg: "此聊天中暂无结构。请先在聊天中附加一个文件。",
    dismiss_tool: "关闭工具",
    generated_preview: "生成预览", validation: "验证",
    mlip_model: "MLIP 模型",
    analysis_summary: "分析摘要",
    analysis_prototype: "收敛趋势、异常高亮和数据集比较的原型面板。",
    derived_computation: "衍生计算",
    reference_value: "参考值", predicted_value: "预测值", computed_delta: "计算差值",
    tab_setup: "配置", tab_inputs: "输入", tab_checks: "解释",
    tab_analysis: "分析", tab_metrics: "指标", tab_compute: "计算",
    tool_structure_search_launcher: "搜索 Materials Cloud、Materials Project、NOMAD 和 JARVIS",
    tool_ml_analysis_launcher: "用机器学习原子间势探索材料研究",
    tool_dft_workbench_launcher: "DFT 工作流、输入、收敛和结果指导",
    tool_beyond_dft_launcher: "GW、BSE、QMC、TDDFT、Wannier、DMFT、QM/MM 及更多",
    tool_post_analysis_launcher: "解析并解读 DFT/MLIP 输出、图表和收敛数据",
    tool_aiida_launcher: "监控 AiiDA 流程，诊断失败，浏览溯源信息",
    settings_model_heading: "模型",
    settings_model_desc: "Qwen3.8-27B 默认在本地运行——你的结构和对话内容不会离开这台机器。只有当你想切换到 OpenAI、Claude 或 Gemini 进行对话时，才需要在下方添加云端 API 密钥。",
    settings_model_key_placeholder: "未设置",
    settings_model_key_hint: "仅保存在本机——密钥只会发送给其所属的服务商，绝不会发往其他任何地方。",
    settings_databases_heading: "数据库",
    settings_databases_desc: "搜索 Materials Project 需要一个免费的 API key，可以在 materialsproject.org/api 申请。",
    settings_compute_heading: "计算",
    settings_compute_label: "AiiDA / HPC 连接",
    settings_compute_connected: "已连接 — Goldilocks 可以为你提交并监控计算任务",
    settings_compute_disconnected: "未连接 — Goldilocks 仍可以为你准备输入文件，由你自己运行",
    settings_compute_connect: "连接",
    settings_compute_disconnect: "断开连接",
    beyond_dft_no_backing: "没有 goldilocks-core 背书",
    beyond_dft_no_backing_hint: "goldilocks-core 不覆盖这些方法。这里显示的是文献中常见的起点，不是推荐值——收敛性需要你自己验证。",
  },
  it: {
    new_chat: "Nuova chat", projects: "Progetti", new_project: "Nuovo progetto",
    open_project: "Apri progetto", new_chat_in_project: "Nuova chat nel progetto",
    chats: "Chat", sources: "Fonti", general_chat: "Chat generale",
    search: "Cerca", see_less: "Mostra meno",
    settings: "Impostazioni", profile: "Profilo", experience_level: "Livello di esperienza",
    not_selected: "Non ancora selezionato", change: "Modifica",
    contribute_heading: "Voglio contribuire!",
    contribute_desc: "Trovato un bug, hai un'idea o vuoi collaborare? Apri una issue su GitHub o contatta direttamente il team.",
    github_issues: "GitHub Issues — segnala bug o richiedi funzionalità",
    acknowledgements: "Ringraziamenti",
    ack_desc: "Goldilocks è un progetto collaborativo. Ringraziamo tutti coloro che hanno contribuito con tempo, competenze e idee.",
    ack_note: "Ordine alfabetico · nessuna classifica implicita",
    language: "Lingua", save: "Salva", cancel: "Annulla", confirm: "Conferma",
    welcome: "Benvenuto in Goldilocks",
    welcome_desc: "Scegli l'opzione che descrive meglio la tua esperienza nella ricerca computazionale sui materiali. Questo aiuta Goldilocks ad adattare le risposte.",
    continue: "Continua",
    exp_new_title: "Nuovo alla ricerca computazionale sui materiali",
    exp_new_desc: "Sono nuovo e vorrei ricevere maggiore assistenza.",
    exp_familiar_title: "Familiare con i workflow",
    exp_familiar_desc: "Conosco le basi e voglio aiuto pratico con i workflow più comuni.",
    exp_advanced_title: "Utente avanzato",
    exp_advanced_desc: "Ho dimestichezza con i workflow e voglio risposte concise e orientate agli esperti.",
    projects_desc: "Organizza chat, strutture e workflow in spazi di ricerca dedicati.",
    no_chats_yet: "Nessuna chat ancora. Scrivi nel campo qui sotto per iniziarne una.",
    no_sources_yet: "Nessuna fonte aggiunta.",
    create_project: "Crea progetto", project_name: "Nome progetto",
    project_desc_label: "Descrizione (facoltativa)",
    add_to_project: "Aggiungi al progetto", choose_project: "Scegli un progetto in cui salvare questa chat.",
    no_projects_yet: "Nessun progetto.", create_project_first: "Crea prima un progetto tramite il pulsante Nuovo progetto nella barra laterale.",
    pending: "In attesa", powered_by: "Powered by",
    task_builder: "Task builder",
    structure_or_formula: "Struttura o formula", formula: "Formula",
    query_structure: "Struttura di ricerca", candidate_structures: "Strutture candidate",
    search_databases: "Cerca nei database", searching: "Ricerca in corso…",
    no_structure_in_chat: "Nessuna struttura nella chat",
    no_structure_msg: "Nessuna struttura in questa chat. Allega prima un file nella chat.",
    dismiss_tool: "Chiudi strumento",
    generated_preview: "Anteprima generata", validation: "Validazione",
    mlip_model: "Modello MLIP",
    analysis_summary: "Riepilogo analisi",
    analysis_prototype: "Pannello prototipo per tendenze di convergenza, rilevamento anomalie e confronto dataset.",
    derived_computation: "Calcolo derivato",
    reference_value: "Valore di riferimento", predicted_value: "Valore previsto", computed_delta: "Delta calcolato",
    tab_setup: "Configurazione", tab_inputs: "Input", tab_checks: "Spiegazione",
    tab_analysis: "Analisi", tab_metrics: "Metriche", tab_compute: "Calcolo",
    tool_structure_search_launcher: "Cerca su Materials Cloud, Materials Project, NOMAD e JARVIS",
    tool_ml_analysis_launcher: "Esplora la ricerca sui materiali con potenziali interatomici ML",
    tool_dft_workbench_launcher: "Guida per workflow DFT, input, convergenza e risultati",
    tool_beyond_dft_launcher: "GW, BSE, QMC, TDDFT, Wannier, DMFT, QM/MM e oltre",
    tool_post_analysis_launcher: "Analizza e interpreta output DFT/MLIP, grafici e dati di convergenza",
    tool_aiida_launcher: "Monitora i processi AiiDA, diagnostica gli errori ed esplora la provenienza",
    settings_model_heading: "Modello",
    settings_model_desc: "Qwen3.8-27B viene eseguito localmente per impostazione predefinita — le tue strutture e conversazioni non lasciano mai questa macchina. Aggiungi una chiave API cloud qui sotto solo se vuoi passare a OpenAI, Claude o Gemini per una chat.",
    settings_model_key_placeholder: "Non impostata",
    settings_model_key_hint: "Salvata solo localmente su questa macchina — una chiave viene inviata solo al fornitore a cui appartiene, mai altrove.",
    settings_databases_heading: "Banche dati",
    settings_databases_desc: "Materials Project richiede una chiave API gratuita per la ricerca. Ottienine una su materialsproject.org/api.",
    settings_compute_heading: "Calcolo",
    settings_compute_label: "Connessione AiiDA / HPC",
    settings_compute_connected: "Connesso — Goldilocks può inviare e monitorare i calcoli per te",
    settings_compute_disconnected: "Non connesso — Goldilocks può comunque preparare i file di input che eseguirai tu stesso",
    settings_compute_connect: "Connetti",
    settings_compute_disconnect: "Disconnetti",
    beyond_dft_no_backing: "Nessun supporto da goldilocks-core",
    beyond_dft_no_backing_hint: "goldilocks-core non copre questi metodi. Quello che vedi qui sono punti di partenza comuni dalla letteratura, non valori raccomandati — la convergenza va sempre verificata da te.",
  },
};

const EMPTY_MESSAGES = [];
const EMPTY_ARRAY = [];

function readStorage(key, fallback = "") {
  if (typeof window === "undefined") return fallback;
  return window.localStorage.getItem(key) ?? fallback;
}

function writeStorage(key, value) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(key, value);
}

// localStorage is string-only -- readStorage's raw string would produce
// invalid unitless CSS (React only auto-appends "px" to actual numbers),
// so pane widths need explicit parsing/guarding on the way back out.
function readStoredWidth(key, fallback) {
  const n = Number(readStorage(key, ""));
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

// Shared drag-to-resize wiring for the sidebar/tools pane dividers -- one
// mousedown starts a global mousemove/mouseup pair that self-removes on
// mouseup, rather than duplicating this per handle. onStart/onEnd toggle a
// "resizing" flag so the pane's own CSS width transition (used for the
// open/close collapse animation) can be suspended for the drag -- otherwise
// every mousemove sets a new target width for that transition to ease
// towards, and the pane perpetually lags behind the cursor instead of
// tracking it directly.
function startPaneResize(startEvent, { startWidth, min, max, direction, onChange, onStart, onEnd }) {
  const startX = startEvent.clientX;
  onStart?.();
  function onMove(e) {
    const delta = direction === "right" ? e.clientX - startX : startX - e.clientX;
    onChange(Math.min(max, Math.max(min, startWidth + delta)));
  }
  function onUp() {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    onEnd?.();
  }
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}


function getElementCategory(symbol) {
  for (const [category, symbols] of Object.entries(ELEMENT_CATEGORY_GROUPS)) {
    if (symbols.includes(symbol)) return category;
  }
  return "unknown";
}

function createId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Qwen3.8 is a vision-language model (verified 2026-09-15 via a real litellm
// + ollama_chat/qwen3.8 call) -- images go through their own attach path,
// not the structure-file one, since a photo/screenshot isn't a crystal
// structure and reading it with file.text() just garbles its bytes.
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

function isImageFile(file) {
  if (file.type?.startsWith("image/")) return true;
  return IMAGE_EXTENSIONS.has(getRawFileExtension(file.name));
}

// `file.text()` never throws -- it decodes anything as UTF-8, replacing
// invalid byte sequences with U+FFFD. A real binary file (zip, docx, an
// image we didn't recognize by extension, ...) decodes to mostly garbage:
// a high density of replacement/control characters. This is the backstop
// that would have caught the original PNG-misread bug even without special
// image handling, and it's what now catches "we genuinely can't read this."
function looksLikeBinaryText(text) {
  if (!text) return false;
  const sample = text.slice(0, 4000);
  let suspicious = 0;
  for (let i = 0; i < sample.length; i += 1) {
    const code = sample.charCodeAt(i);
    const isReplacementChar = code === 0xfffd;
    const isControlChar = code < 32 && code !== 9 && code !== 10 && code !== 13;
    if (isReplacementChar || isControlChar) suspicious += 1;
  }
  return suspicious / sample.length > 0.01;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// `display`/`images` only exist on a message sent earlier in this browser
// session -- after a reload, `GET /api/chat/{id}` rehydrates messages from
// the checkpointer with only `content` (design doc 16: the checkpointer is
// the one source of truth), so rendering has to be able to fall back to
// deriving text/images straight from a multipart `content` array too.
function getMessageDisplayParts(message) {
  if (!Array.isArray(message.content)) {
    return { text: message.display || message.content, images: message.images ?? [] };
  }
  const text = message.content.find((part) => part.type === "text")?.text ?? "";
  const images = message.content
    .filter((part) => part.type === "image_url")
    .map((part, index) => ({ name: `image-${index}`, dataUrl: part.image_url?.url }));
  return { text: message.display || text, images: message.images ?? images };
}

// Recovers a structure attachment from a past user message so it still
// shows up in the Structure Viewer after a reload (checkpointer only stores
// `content`, never the local `sessionFiles` state). `content` may be a
// plain string or a multimodal parts array (see getMessageDisplayParts) --
// only the text part can carry a fenced structure attachment.
function extractStructureFromMessageContent(content) {
  const text = typeof content === "string" ? content : Array.isArray(content) ? content.find((part) => part.type === "text")?.text ?? "" : "";
  const match = text.match(/\[Attached file: ([^\]]+)\]\n```([^\n]*)\n([\s\S]+?)```/);
  if (!match) return null;
  const [, name, fenceLang, fileContent] = match;
  const rawExt = getRawFileExtension(name);
  const inferredExt = inferStructureExtension(name, fileContent);
  if (!WEAS_SUPPORTED_EXTS.has(inferredExt)) return null; // e.g. a DFT output log, not a real structure
  const fencedExt = fenceLang && fenceLang !== "text" ? `.${fenceLang.toLowerCase()}` : "";
  return { name, rawExt, ext: inferredExt || fencedExt || rawExt, content: fileContent };
}

// Generic version of the "already sent" check -- works for any uploaded
// file or image, not just recognized structures (unlike
// extractStructureFromMessageContent, which intentionally returns null for
// non-structure attachments).
function isFileSentInHistory(messages, name) {
  return messages.some((msg) => {
    if (msg.role !== "user") return false;
    const parts = getMessageDisplayParts(msg);
    if (parts.text.includes(`[Attached file: ${name}]`)) return true;
    return parts.images.some((img) => img.name === name);
  });
}

function createSession(projectId: string | null = null): Session {
  return {
    id: createId(),
    title: "New chat",
    messages: [],
    messagesLoaded: true,
    createdAt: new Date().toISOString(),
    projectId,
    tool: null,
    rightPanelOpen: false,
    rightPanelView: null,
    modeState: {},
  };
}

function LogoImage({ className = "", alt = "Goldilocks logo" }) {
  return <img src="/logo.svg" alt={alt} className={`logo-image ${className}`.trim()} />;
}

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
      <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
  );
}

function UserIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function GridIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="4" y="4" width="6" height="6" rx="1.2" />
      <rect x="14" y="4" width="6" height="6" rx="1.2" />
      <rect x="4" y="14" width="6" height="6" rx="1.2" />
      <rect x="14" y="14" width="6" height="6" rx="1.2" />
    </svg>
  );
}

function ClipIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66L9.41 17.41a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3.5L22 20.5H2L12 3.5Z" />
      <line x1="12" y1="9.5" x2="12" y2="14" />
      <circle cx="12" cy="17.2" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="3" y1="6" x2="21" y2="6" />
      <line x1="3" y1="12" x2="21" y2="12" />
      <line x1="3" y1="18" x2="21" y2="18" />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 3 21 3 21 9" />
      <polyline points="9 21 3 21 3 15" />
      <line x1="21" y1="3" x2="14" y2="10" />
      <line x1="3" y1="21" x2="10" y2="14" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function CaretDownIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6M9 6V4h6v2" />
    </svg>
  );
}

function ChevDown() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function CrystalIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2" />
      <line x1="12" y1="2" x2="12" y2="22" />
      <line x1="2" y1="8.5" x2="22" y2="8.5" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82L4.21 7.2a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function ToolGlyph({ tool, size = 18 }) {
  const iconSrc = tool ? TOOL_ICON_SOURCES[tool.id] : null;

  if (iconSrc) {
    return (
      <img
        src={iconSrc}
        alt=""
        className="tool-glyph"
        style={{ width: size, height: size }}
      />
    );
  }

  return (
    <span className="tool-glyph-fallback" style={{ fontSize: size }}>
      {tool?.icon}
    </span>
  );
}

// Shared shape for the option-picker family (WorkspacePicker/SimpleSelect)
// below. Different pickers across the app attach different extra fields to
// their items (`desc`, `recommended`, `tag`, `disabled`...), so that part
// stays a loose index signature. `id` is generic rather than a bare
// `string | number` union: each *individual* picker instance is consistent
// (structure-list pickers key off the array index as a number; every other
// picker uses a string id), and its `onSelect`/`value` feed a single
// same-typed `useState` -- a bare union would let a number id flow into a
// string-only setter (and vice versa) without TS complaining. `label` is
// always plain text everywhere it's constructed, so that's typed precisely.
interface PickerItem<TId extends string | number = string | number> {
  id: TId;
  label: string;
  [key: string]: any;
}

interface PickerGroup<TId extends string | number = string | number> {
  label: string;
  items: PickerItem<TId>[];
}

function WorkspacePicker<TId extends string | number = string | number>({
  label,
  value,
  option,
  groups,
  isOpen,
  onToggle,
  onSelect,
  onAsk,
  onRecommend,
  disabled,
}: {
  label: string;
  value: TId;
  option: PickerItem<TId>;
  groups: PickerGroup<TId>[];
  isOpen: boolean;
  onToggle: () => void;
  onSelect: (id: TId) => void;
  onAsk?: (item: PickerItem<TId>) => void;
  onRecommend?: (item: PickerItem<TId>) => void;
  disabled?: boolean;
}) {
  const recItem = onRecommend && !disabled ? groups.flatMap((g) => g.items).find((i) => i.recommended) : null;
  return (
    <div className={`workspace-picker${isOpen ? " open" : ""}${disabled ? " disabled" : ""}`}>
      <button className="workspace-picker-trigger" type="button" onClick={disabled ? undefined : onToggle} aria-expanded={isOpen} disabled={disabled}>
        <div className="workspace-picker-copy">
          <span className="workspace-picker-label">{label}</span>
          <span className="workspace-picker-value">{option.label}</span>
        </div>
        <div className="workspace-picker-actions">
          {recItem && (
            <span
              className="workspace-picker-recommend"
              role="button"
              data-tooltip="Ask Goldilocks to recommend"
              onClick={(e) => { e.stopPropagation(); onSelect(recItem.id); onRecommend(recItem); }}
            >✦</span>
          )}
          <span className="workspace-picker-caret">
            <CaretDownIcon />
          </span>
        </div>
      </button>

      {isOpen && (
        <div className="workspace-picker-menu">
          {groups.map((group) => (
            <div key={group.label} className="workspace-picker-group">
              <div className="workspace-picker-group-label">{group.label}</div>
              <div className="workspace-picker-options">
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`workspace-picker-option${item.id === value ? " active" : ""}`}
                    onClick={() => onSelect(item.id)}
                  >
                    <span className="workspace-picker-option-title">{item.label}</span>
                    {onAsk && (
                      <span
                        className="workspace-picker-option-ask"
                        role="button"
                        data-tooltip={`Ask about ${item.label}`}
                        onClick={(e) => { e.stopPropagation(); onAsk(item); }}
                      >
                        ✦
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SimpleSelect<TId extends string | number = string | number>({
  label,
  value,
  items,
  isOpen,
  onToggle,
  onSelect,
  onAsk,
  disabled,
}: {
  label: string;
  value: TId;
  items: PickerItem<TId>[];
  isOpen: boolean;
  onToggle: () => void;
  onSelect: (id: TId) => void;
  onAsk?: (item: PickerItem<TId>) => void;
  disabled?: boolean;
}) {
  const displayValue = items.find((i) => i.id === value)?.label ?? "—";
  return (
    <div className={`workspace-picker${isOpen ? " open" : ""}${disabled ? " disabled" : ""}`}>
      <button className="workspace-picker-trigger" type="button" onClick={disabled ? undefined : onToggle} disabled={disabled}>
        <div className="workspace-picker-copy">
          <span className="workspace-picker-label">{label}</span>
          <span className="workspace-picker-value">{displayValue}</span>
        </div>
        <span className="workspace-picker-caret"><CaretDownIcon /></span>
      </button>
      {isOpen && (
        <div className="workspace-picker-menu">
          <div className="workspace-picker-group">
            <div className="workspace-picker-options">
              {items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`workspace-picker-option${item.id === value ? " active" : ""}`}
                  onClick={() => onSelect(item.id)}
                >
                  <span className="workspace-picker-option-title">{item.label}</span>
                  {onAsk && (
                    <span
                      className="workspace-picker-option-ask"
                      role="button"
                      data-tooltip={`Ask about ${item.label}`}
                      onClick={(e) => { e.stopPropagation(); onAsk(item); }}
                    >✦</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Every Tool's own "Structure" picker only ever let you *choose among*
// structures already in the chat -- getting one in there in the first
// place meant knowing you could drag a file onto the browser window
// somewhere (the app-wide drop zone, see `handleDrop`/`dragOver`) or use
// the composer's own attach button, neither of which is visible from
// inside a Tool panel. This gives each picker its own visible, local
// "upload" button and drop target, both calling the same `onFile` (in
// practice always the top-level `readFile`) -- no new file-reading logic,
// just a discoverable way to trigger the existing one from where a user
// is actually looking (2026-09-16, user-requested across every Tool).
// Also reused as-is by Post Analysis's phonon-file upload (a different
// `accept`/`onFile`/copy, not a structure at all -- the drop-zone/button
// shell is identical either way).
function StructureUploadControl({
  onFile,
  label = "Upload structure",
  hint = "or drag a file here (CIF, XYZ, POSCAR, VASP, XSF, CUBE)",
  accept,
}: {
  onFile: (file: File) => void;
  label?: string;
  hint?: string;
  accept?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  return (
    <div
      className={`structure-upload-zone${isDragOver ? " drag-over" : ""}`}
      onDragOver={(event) => { event.preventDefault(); setIsDragOver(true); }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setIsDragOver(false);
        const file = event.dataTransfer.files?.[0];
        if (file) onFile(file);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept ?? [...WEAS_SUPPORTED_EXTS].join(",")}
        style={{ display: "none" }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = "";
        }}
      />
      <button type="button" className="structure-upload-btn" onClick={() => inputRef.current?.click()}>
        ⬆ {label}
      </button>
      <span className="structure-upload-hint">{hint}</span>
    </div>
  );
}

function getToolById(id) {
  return TOOLS.find((tool) => tool.id === id) ?? null;
}

export default function App() {
  const [themeChoice, setThemeChoice] = useState(() => {
    const stored = readStorage(STORAGE_KEYS.theme, "light");
    return stored === "auto" ? "light" : stored;
  });
  const [language, setLanguage] = useState(() => readStorage(STORAGE_KEYS.language, "en"));
  const [experienceLevel, setExperienceLevel] = useState(() => readStorage(STORAGE_KEYS.experience, ""));
  const [pendingExperience, setPendingExperience] = useState(() => readStorage(STORAGE_KEYS.experience, "new") || "new");
  const [showOnboarding, setShowOnboarding] = useState(() => !readStorage(STORAGE_KEYS.experience, ""));
  const [sbOpen, setSbOpen] = useState(true);
  const [toolsOpen, setToolsOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(() => readStoredWidth(STORAGE_KEYS.sidebarWidth, 260));
  const [toolsWidth, setToolsWidth] = useState(() => readStoredWidth(STORAGE_KEYS.toolsWidth, 440));
  const [resizingPane, setResizingPane] = useState(null);
  // Tracks which resize boundary ("sidebar" | "tools") the pointer is over,
  // independent of resizingPane (which only reflects an active drag). Each
  // boundary has two DOM handles (one in the top header, one in the row
  // below) that must highlight together as one continuous bar -- CSS
  // :hover/:active on either element alone can't do that, since they're
  // separate elements, so hover state is lifted here and applied via a
  // shared class.
  const [hoveredHandle, setHoveredHandle] = useState(null);
  // One workspace/client instance per app lifetime -- recreating it every
  // time DFT Workbench's detail page is opened would drop in-progress state
  // (see junwen94/goldilocks-agent#1 -- this is the embedded
  // goldilocks-workbench package DFT Workbench's full-page detail reuses).
  const coreWorkspace = useMemo(() => createCoreWorkspace(new HttpCoreClient()), []);
  // Drives the full-page "takeover" navigation (header's new expand-all-tools
  // button -> a grid of all six Tools -> a Tool's own full-page detail page):
  // "none" is the everyday chat+sidebar+Tools-panel shell, "overview" is the
  // grid, "detail" is a specific Tool's full page (which Tool is just
  // `activeTool`, the same session.tool the inline side panel already
  // tracks -- see openToolFullPage). Purely additive: the inline "pick a
  // tool from the side panel" path below doesn't read this state at all.
  const [fullPageView, setFullPageView] = useState<"none" | "overview" | "detail">("none");
  const [view, setView] = useState("chats");
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState(null);
  const [projectTab, setProjectTab] = useState("chats");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState(null);
  const [draftProjectId, setDraftProjectId] = useState(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [toolStatus, setToolStatus] = useState(null);
  const abortRef = useRef(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Persisted server-side to ~/.config/goldilocks/config.toml on blur
  // (agent design doc §11.2) via POST /api/credentials -- this state is
  // just what's currently typed into the field, not the source of truth.
  const [cloudApiKeys, setCloudApiKeys] = useState({ openai: "", anthropic: "", google: "", materials_project: "" });
  const [credentialStatus, setCredentialStatus] = useState({});
  const [configuredProviders, setConfiguredProviders] = useState({});
  const [computeConnected, setComputeConnected] = useState(false);
  const [showExperiencePicker, setShowExperiencePicker] = useState(false);
  const [addToProjectOpen, setAddToProjectOpen] = useState(false);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [pendingProjectId, setPendingProjectId] = useState(null);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDesc, setNewProjectDesc] = useState("");
  const [newProjectColor, setNewProjectColor] = useState(PROJECT_COLORS[0]);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [newProjectWithChat, setNewProjectWithChat] = useState(true);
  const [newProjectChatTitle, setNewProjectChatTitle] = useState("");
  const [newProjectWithSources, setNewProjectWithSources] = useState(false);
  const [newProjectSourcesText, setNewProjectSourcesText] = useState("");
  const [selectedModel, setSelectedModel] = useState(MODEL_GROUPS[0].items[0]);
  // Ephemeral UI-only state (which dropdown is open, is a calc in flight) --
  // fine to stay global, same as structure-search's own loading flag/open
  // dropdown state. Everything else MLIP-related lives in
  // `session.modeState["ml-analysis"]`, see `mlipState`/`updateMlipState`
  // below (mirrors structure-search's per-chat state fix).
  const [openMlipPicker, setOpenMlipPicker] = useState(null);
  const [mlipCalcLoading, setMlipCalcLoading] = useState(false);
  const [openDftPicker, setOpenDftPicker] = useState(null);
  const [dftLoading, setDftLoading] = useState(false);
  // Fetched once via /api/dft/capabilities (goldilocks-core#62, closed
  // 2026-09-15 -- `goldilocks capabilities --json` now gives codes/tasks/
  // settings/pseudopotential_tables/hpc_profiles/facts/warnings-catalog in
  // one call), not per-chat: it's static per goldilocks-core checkout, same
  // for every session.
  const [dftCapabilities, setDftCapabilities] = useState(null);
  const [dftCapabilitiesLoading, setDftCapabilitiesLoading] = useState(false);
  const [dftCapabilitiesError, setDftCapabilitiesError] = useState(null);
  // "add an override" dropdown's own transient selection -- not part of
  // session state, resets after each add.
  const [dftOverrideDraftKey, setDftOverrideDraftKey] = useState("");
  const [dftBundleLoading, setDftBundleLoading] = useState(false);
  const [dftBundleError, setDftBundleError] = useState(null);
  // DFT Workbench's own `code`/`task`/`hpc`/`overrides`/results are
  // session-scoped below (see `dftState`/`updateDftState`) -- these two
  // are Beyond DFT's own decorative, deliberately-decoupled state (see
  // `DEFAULT_DFT_STATE`'s comment).
  const [beyondDftCode, setBeyondDftCode] = useState(DFT_CODE_GROUPS[0].items[0].id);
  const [beyondDftMachine, setBeyondDftMachine] = useState(DFT_HPC_GROUPS[0].items[0].id);
  const [selectedBeyondDftMethod, setSelectedBeyondDftMethod] = useState("gw");
  const [selectedDftAdvisorModel, setSelectedDftAdvisorModel] = useState(null);
  // sessionFiles/attachedFiles moved to session.modeState (see below,
  // near structureSearchState) -- they used to leak across chats the same
  // way structure-search's formula box did.
  const [attachedImages, setAttachedImages] = useState([]);
  // Images had no session-level home before -- `attachedImages` was both
  // "uploaded this session" and "staged for the next message" at once. This
  // splits that apart the same way sessionFiles/attachedFiles already are
  // for text/structure files, so an image stays visible in Files > Uploaded
  // after it's been sent, not just while staged.
  const [sessionImages, setSessionImages] = useState([]);
  const [structureFileContents, setStructureFileContents] = useState({});
  const [dragOver, setDragOver] = useState(false);
  const [showElementPicker, setShowElementPicker] = useState(false);
  const [showStructureViewer, setShowStructureViewer] = useState(false);
  const [showFilesPanel, setShowFilesPanel] = useState(false);
  const [filesTab, setFilesTab] = useState("uploaded");
  const [viewerIdx, setViewerIdx] = useState(0);
  const [structureMatchLoading, setStructureMatchLoading] = useState(false);
  const [dbGroupOpen, setDbGroupOpen] = useState({});
  const [importingEntries, setImportingEntries] = useState(new Set());
  const [pickerElements, setPickerElements] = useState<Record<string, number>>({});

  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  const fileRef = useRef(null);
  const plusRef = useRef(null);
  const modelRef = useRef(null);
  const widgetsAreaRef = useRef(null);
  const chatAreaRef = useRef(null);
  const dftPickerRef = useRef(null);

  const resolvedTheme = themeChoice;
  const currentActiveId = activeId ?? null;
  const session = activeId ? (sessions.find((item) => item.id === activeId) ?? null) : null;
  const messages = session?.messages ?? EMPTY_MESSAGES;
  const hasMessages = messages.length > 0;
  const activeTool = getToolById(session?.tool);
  // Find in Databases' formula/query-mode/property-filter/import state and
  // search history live in `session.modeState["structure-search"]` -- a
  // per-session slot that already existed (`createSession`'s `modeState: {}`)
  // but was never used, so this was leaking as global state before: switching
  // chats used to show whichever chat last ran a search, not the chat you're
  // actually in, and there was no way to look back at an earlier search in
  // the same chat. `dbSearchHistory` keeps every search this chat has run;
  // `dbActiveSearchIndex` -1 means "show the most recent."
  const structureSearchState = session?.modeState?.["structure-search"] ?? DEFAULT_STRUCTURE_SEARCH_STATE;
  const dbFormulaInput = structureSearchState.formula;
  const dbQueryMode = structureSearchState.queryMode;
  const dbPropertyTags = structureSearchState.propertyTags;
  const dbSearchHistory = structureSearchState.searchHistory;
  const dbActiveSearchIndex = structureSearchState.activeSearchIndex >= 0
    ? structureSearchState.activeSearchIndex
    : dbSearchHistory.length - 1;
  const structureMatchResults = dbSearchHistory[dbActiveSearchIndex]?.result ?? null;
  const importedEntries = structureSearchState.importedEntries;

  function updateStructureSearchState(patch) {
    updateCurrentSession((current) => {
      const prev = current.modeState?.["structure-search"] ?? DEFAULT_STRUCTURE_SEARCH_STATE;
      const next = typeof patch === "function" ? patch(prev) : { ...prev, ...patch };
      return { ...current, modeState: { ...current.modeState, "structure-search": next } };
    });
  }

  // sessionFiles/attachedFiles used to leak across chats the same way
  // structure-search's formula box did before its own fix -- switching
  // chats didn't reset or restore either one. Moved into session.modeState
  // (2026-09-15, found while wiring up MLIP Playground, whose geomopt ->
  // import-back loop depends on chatStructures, which is itself derived
  // from these). Kept as plain `const`/`function` pairs (not full
  // `useState`) so every existing call site's `setAttachedFiles(prev => ...)`
  // / `setAttachedFiles([])` usage keeps working unchanged.
  const sessionFiles = session?.modeState?.sessionFiles ?? EMPTY_ARRAY;
  const attachedFiles = session?.modeState?.attachedFiles ?? EMPTY_ARRAY;
  function setSessionFiles(updater) {
    updateCurrentSession((current) => {
      const prev = current.modeState?.sessionFiles ?? [];
      const next = typeof updater === "function" ? updater(prev) : updater;
      return { ...current, modeState: { ...current.modeState, sessionFiles: next } };
    });
  }
  function setAttachedFiles(updater) {
    updateCurrentSession((current) => {
      const prev = current.modeState?.attachedFiles ?? [];
      const next = typeof updater === "function" ? updater(prev) : updater;
      return { ...current, modeState: { ...current.modeState, attachedFiles: next } };
    });
  }

  // MLIP Playground panel state -- same per-chat fix, same reasoning
  // (2026-09-15, all 16 of these were plain global useState before).
  const mlipState = session?.modeState?.["ml-analysis"] ?? DEFAULT_MLIP_STATE;
  function updateMlipState(key, updaterOrValue) {
    updateCurrentSession((current) => {
      const prev = current.modeState?.["ml-analysis"] ?? DEFAULT_MLIP_STATE;
      const nextValue =
        typeof updaterOrValue === "function" ? updaterOrValue(prev[key]) : updaterOrValue;
      return {
        ...current,
        modeState: { ...current.modeState, "ml-analysis": { ...prev, [key]: nextValue } },
      };
    });
  }
  const selectedMlipModel =
    MLIP_MODELS.find((m) => m.id === mlipState.selectedMlipModelId) ?? MLIP_MODELS[0];
  const setSelectedMlipModel = (model) => updateMlipState("selectedMlipModelId", model.id);
  const mlipCalcType = mlipState.mlipCalcType;
  const setMlipCalcType = (v) => updateMlipState("mlipCalcType", v);
  const mlipResultsList = mlipState.mlipResultsList;
  const setMlipResultsList = (v) => updateMlipState("mlipResultsList", v);
  const mlipStructIdx = mlipState.mlipStructIdx;
  const setMlipStructIdx = (v) => updateMlipState("mlipStructIdx", v);
  const mlipNebInitIdx = mlipState.mlipNebInitIdx;
  const setMlipNebInitIdx = (v) => updateMlipState("mlipNebInitIdx", v);
  const mlipNebFinalIdx = mlipState.mlipNebFinalIdx;
  const setMlipNebFinalIdx = (v) => updateMlipState("mlipNebFinalIdx", v);
  const mlipFmax = mlipState.mlipFmax;
  const setMlipFmax = (v) => updateMlipState("mlipFmax", v);
  const mlipSteps = mlipState.mlipSteps;
  const setMlipSteps = (v) => updateMlipState("mlipSteps", v);
  const mlipRelaxMode = mlipState.mlipRelaxMode;
  const setMlipRelaxMode = (v) => updateMlipState("mlipRelaxMode", v);
  const mlipSupercell = mlipState.mlipSupercell;
  const setMlipSupercell = (v) => updateMlipState("mlipSupercell", v);
  const mlipDisplacement = mlipState.mlipDisplacement;
  const setMlipDisplacement = (v) => updateMlipState("mlipDisplacement", v);
  const mlipMinVol = mlipState.mlipMinVol;
  const setMlipMinVol = (v) => updateMlipState("mlipMinVol", v);
  const mlipMaxVol = mlipState.mlipMaxVol;
  const setMlipMaxVol = (v) => updateMlipState("mlipMaxVol", v);
  const mlipNVolumes = mlipState.mlipNVolumes;
  const setMlipNVolumes = (v) => updateMlipState("mlipNVolumes", v);
  const mlipNImages = mlipState.mlipNImages;
  const setMlipNImages = (v) => updateMlipState("mlipNImages", v);
  const mlipNebFmax = mlipState.mlipNebFmax;
  const setMlipNebFmax = (v) => updateMlipState("mlipNebFmax", v);

  // DFT Workbench panel state -- same per-chat fix, same reasoning as MLIP
  // (2026-09-15, redone against goldilocks-core's real v2 CLI -- see
  // DEFAULT_DFT_STATE's own comment for why `overrides` is sparse, not a
  // flat copy of every setting).
  const dftState = session?.modeState?.["dft-workbench"] ?? DEFAULT_DFT_STATE;
  function updateDftState(key, updaterOrValue) {
    updateCurrentSession((current) => {
      const prev = current.modeState?.["dft-workbench"] ?? DEFAULT_DFT_STATE;
      const nextValue =
        typeof updaterOrValue === "function" ? updaterOrValue(prev[key]) : updaterOrValue;
      return {
        ...current,
        modeState: { ...current.modeState, "dft-workbench": { ...prev, [key]: nextValue } },
      };
    });
  }
  const dftCode = dftState.code;
  const setDftCode = (v) => updateDftState("code", v);
  const dftTask = dftState.task;
  const setDftTask = (v) => updateDftState("task", v);
  const dftHpc = dftState.hpc;
  const setDftHpc = (v) => updateDftState("hpc", v);
  const dftOverrides = dftState.overrides;
  const setDftOverrides = (v) => updateDftState("overrides", v);
  const dftExplainResult = dftState.explainResult;
  const setDftExplainResult = (v) => updateDftState("explainResult", v);
  const dftRunResult = dftState.runResult;
  const setDftRunResult = (v) => updateDftState("runResult", v);

  const activeProject = projects.find((project) => project.id === activeProjectId) ?? null;
  // DFT Workbench's own Code/Task groups are derived from the real fetched
  // /api/dft/capabilities payload now that goldilocks-core#62 closed --
  // DFT_CODE_GROUPS/DFT_TASK_GROUPS stay as the loading/error fallback (and
  // Beyond DFT's own decorative picker keeps using them unconditionally,
  // since that panel has no real backing at all, see BEYOND_DFT_METHOD_GROUPS).
  const dftCodeGroupsFromCapabilities = dftCapabilities
    ? [{ label: "Codes", items: dftCapabilities.codes.map((c) => ({ id: c.id, label: c.name, desc: `Tasks: ${c.tasks.join(", ")}`, recommended: true })) }]
    : DFT_CODE_GROUPS;
  const dftTaskGroupsFromCapabilities = dftCapabilities
    ? [{ label: "Tasks", items: dftCapabilities.tasks.map((t) => ({ id: t.id, label: t.name, desc: t.description, recommended: t.id === "scf_single_point" })) }]
    : DFT_TASK_GROUPS;
  // Same WorkspacePicker component as Code/Task (not a raw <select>) so the
  // whole Setup form reads as one consistent control family -- "" means
  // "auto", real when exactly one HPC profile is installed.
  const dftHpcGroupsFromCapabilities = [{
    label: "HPC profile",
    items: [
      { id: "", label: "Auto", desc: "Only valid when exactly one HPC profile is installed.", recommended: (dftCapabilities?.hpc_profiles?.length ?? 0) <= 1 },
      ...(dftCapabilities?.hpc_profiles ?? []).map((h) => ({ id: h.id, label: h.name, desc: `${h.scheduler} · partitions: ${h.partitions.join(", ")}` })),
    ],
  }];
  const dftCodeMeta = findOptionInGroups(dftCodeGroupsFromCapabilities, dftCode) ?? dftCodeGroupsFromCapabilities[0].items[0];
  const dftTaskMeta = findOptionInGroups(dftTaskGroupsFromCapabilities, dftTask) ?? dftTaskGroupsFromCapabilities[0].items[0];
  const dftHpcMeta = findOptionInGroups(dftHpcGroupsFromCapabilities, dftHpc) ?? dftHpcGroupsFromCapabilities[0].items[0];
  const beyondDftCodeMeta = findOptionInGroups(DFT_CODE_GROUPS, beyondDftCode) ?? DFT_CODE_GROUPS[0].items[0];
  const beyondDftMachineMeta = findOptionInGroups(DFT_HPC_GROUPS, beyondDftMachine) ?? DFT_HPC_GROUPS[0].items[0];
  const selectedBeyondDftMethodMeta = findOptionInGroups(BEYOND_DFT_METHOD_GROUPS, selectedBeyondDftMethod) ?? BEYOND_DFT_METHOD_GROUPS[1].items[0];
  const recentSessions = useMemo(() => sessions.filter((item) => item.projectId === null), [sessions]);
  const visibleProjects = showAllProjects ? projects : projects.slice(0, 3);
  const hiddenProjectCount = Math.max(projects.length - 3, 0);
  const selectedElements = useMemo(() => Object.keys(pickerElements).filter((s) => pickerElements[s] > 0), [pickerElements]);
  // A pending confirmation card leaves the graph genuinely paused
  // (design doc 17.10) -- sending a plain new message instead of
  // answering it discards that paused tool call from LangGraph's own
  // point of view (a fresh, non-resume input) while the assistant
  // message that requested it is already committed to the checkpointer,
  // permanently orphaning it (2026-09-16 incident, see graph.py's
  // `_repair_orphaned_tool_calls`). Blocking send here is the cheap fix;
  // the backend repair is the one that actually makes it unbreakable.
  const hasPendingConfirmation = messages.some(
    (m) => m.role === "confirmation" && m.resolved === null
  );
  const canSend = Boolean(input.trim() || attachedFiles.length > 0 || attachedImages.length > 0) && !hasPendingConfirmation;
  const currentExperience = EXPERIENCE_OPTIONS.find((option) => option.id === experienceLevel);
  const chatStructures = useMemo(() => {
    const found = [];
    for (const sf of sessionFiles) {
      // Only genuinely recognized structure formats are viewable here --
      // a DFT output log or other plain-text attachment is still sent to
      // the model as context (see attachedFiles), it just isn't something
      // WEAS can render, so it doesn't belong in this picker.
      if (sf.isStructure) found.push({ ...sf, inserted: attachedFiles.some(f => f.name === sf.name) });
    }
    for (const msg of messages) {
      if (msg.role !== "user") continue;
      const structure = extractStructureFromMessageContent(msg.content);
      if (structure && !found.some(f => f.name === structure.name)) found.push(structure);
    }
    return found;
  }, [sessionFiles, attachedFiles, messages]);

  // Files > Uploaded: every file/image ever uploaded this session, structure
  // or not -- unlike chatStructures, this doesn't filter by WEAS support.
  const uploadedFiles = useMemo(() => {
    const files = sessionFiles.map((f) => ({
      ...f,
      kind: "file",
      inserted: attachedFiles.some((af) => af.name === f.name),
    }));
    const images = sessionImages.map((img) => ({
      ...img,
      kind: "image",
      inserted: attachedImages.some((ai) => ai.name === img.name),
    }));
    return [...files, ...images];
  }, [sessionFiles, sessionImages, attachedFiles, attachedImages]);

  const safeViewerIdx = chatStructures.length ? Math.min(viewerIdx, chatStructures.length - 1) : 0;
  const activeViewerSource = chatStructures[safeViewerIdx] ?? null;

  const t = (key) => TRANSLATIONS[language]?.[key] ?? TRANSLATIONS.en[key] ?? key;

  const workspaceContent = renderWorkspace();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    document.body.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);

  useEffect(() => {
    refreshConfiguredProviders();
    refreshProjects();
    loadConversationsOnce();
    hydrateExperienceLevel();
  }, []);

  useEffect(() => {
    if (session && !session.messagesLoaded) loadMessagesForSession(session.id);
    // Deps intentionally narrowed to the two fields that matter: `session` is
    // a fresh object every render and `loadMessagesForSession` isn't memoized,
    // so listing them would refire mid-fetch on unrelated re-renders (e.g. a
    // keystroke) and fire duplicate concurrent fetches for the same thread.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.messagesLoaded]);

  useEffect(() => {
    const handleClick = (event) => {
      if (plusRef.current && !plusRef.current.contains(event.target)) setPlusOpen(false);
      if (modelRef.current && !modelRef.current.contains(event.target)) setModelOpen(false);
      if (
        chatAreaRef.current && chatAreaRef.current.contains(event.target) &&
        !(widgetsAreaRef.current && widgetsAreaRef.current.contains(event.target))
      ) {
        setShowElementPicker(false);
        setShowStructureViewer(false);
        setShowFilesPanel(false);
      }
      if (dftPickerRef.current && !dftPickerRef.current.contains(event.target)) setOpenDftPicker(null);
    };

    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (activeTool?.id !== "dft-workbench" || (session?.rightPanelView ?? activeTool.defaultPanel) !== "setup") {
      setOpenDftPicker(null);
    }
  }, [activeTool, session?.rightPanelView]);

  // Fetched once, lazily, the first time the DFT Workbench panel is
  // actually opened -- not on app load, since most sessions may never
  // touch this Tool and goldilocks-core might not even be configured.
  useEffect(() => {
    if (activeTool?.id !== "dft-workbench" || dftCapabilities || dftCapabilitiesLoading) return;
    setDftCapabilitiesLoading(true);
    setDftCapabilitiesError(null);
    fetch("/api/dft/capabilities")
      .then(async (resp) => {
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return resp.json();
      })
      .then(setDftCapabilities)
      .catch((err) => setDftCapabilitiesError(err.message))
      .finally(() => setDftCapabilitiesLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTool?.id]);

  function replaceSession(id, updater) {
    setSessions((prev) => prev.map((item) => (item.id === id ? updater(item) : item)));
  }

  function updateCurrentSession(updater) {
    if (!session) return;
    replaceSession(session.id, updater);
  }

  function newChat(projectId = null) {
    setActiveId(null);
    setDraftProjectId(projectId);
    setActiveProjectId(projectId);
    setView("chats");
    setInput("");
    setAttachedFiles([]);
    setSessionFiles([]);
    setAttachedImages([]);
    setSessionImages([]);
    setShowElementPicker(false);
    setShowStructureViewer(false);
    setShowFilesPanel(false);
    setPlusOpen(false);
  }

  async function createProject() {
    const trimmedName = newProjectName.trim();
    if (!trimmedName) return;
    const parsedSources = newProjectWithSources
      ? newProjectSourcesText
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
      : [];
    const description = newProjectDesc.trim() || "New research project";
    const color = newProjectColor;

    setCreateProjectOpen(false);
    setNewProjectName("");
    setNewProjectDesc("");
    setNewProjectColor(PROJECT_COLORS[0]);
    setNewProjectWithChat(true);
    setNewProjectChatTitle("");
    setNewProjectWithSources(false);
    setNewProjectSourcesText("");
    setView("chats");

    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmedName, color, description }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const project = await res.json();
      // `sources` has no column in the `projects` table yet (design doc 16.5
      // only defines id/name/color/created_at) -- kept client-side only, same
      // as before this project list was made persistent.
      setProjects((prev) => [
        { id: project.id, name: project.name, desc: project.description, color: project.color, sources: parsedSources },
        ...prev,
      ]);
      setActiveProjectId(project.id);
      setPendingProjectId(project.id);
      setActiveId(null);
      setDraftProjectId(project.id);
    } catch {
      window.alert("Couldn't create the project -- is the agent server running?");
    }
  }

  function openProject(projectId) {
    setActiveProjectId(projectId);
    setDraftProjectId(projectId);
    setActiveId(null);
    setView("project");
    setProjectTab("chats");
    setShowElementPicker(false);
    setShowStructureViewer(false);
    setShowFilesPanel(false);
  }

  function addCurrentChatToProject(projectId) {
    if (!session || !projectId) return;
    replaceSession(session.id, (current) => ({ ...current, projectId }));
    setActiveProjectId(projectId);
    setAddToProjectOpen(false);
    // Fire-and-forget: the chat's own local state already reflects the move,
    // and the next /api/chat turn would re-derive it anyway (design doc 16 --
    // the index is a queryable mirror, not a second source of truth).
    fetch(`/api/conversations/${session.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    }).catch(() => {});
  }

  function deleteProject(projectId) {
    setProjects((prev) => prev.filter((project) => project.id !== projectId));
    setPendingProjectId((prev) => (prev === projectId ? null : prev));
    setShowAllProjects(false);

    // Deleting a project unassigns its chats, it doesn't delete them --
    // the backend does the same (`ON DELETE SET NULL`), so mirror that
    // here instead of dropping the sessions from the sidebar.
    setSessions((prev) =>
      prev.map((item) => (item.projectId === projectId ? { ...item, projectId: null } : item))
    );

    if (activeProjectId === projectId) {
      setActiveProjectId(null);
      setDraftProjectId(null);
      setView("chats");
    }

    fetch(`/api/projects/${projectId}`, { method: "DELETE" }).catch(() => {});
  }

  function deleteSession(id) {
    setSessions((prev) => {
      const remaining = prev.filter((item) => item.id !== id);
      if (currentActiveId === id) {
        setActiveId(remaining[0]?.id ?? null);
        if (!remaining[0]) setDraftProjectId(null);
      }
      return remaining;
    });
    fetch(`/api/conversations/${id}`, { method: "DELETE" }).catch(() => {});
  }

  async function readFile(file) {
    if (isImageFile(file)) {
      if (file.size > MAX_IMAGE_BYTES) {
        window.alert(`${file.name} is larger than 8MB — please attach a smaller image.`);
        return;
      }
      try {
        const dataUrl = await readFileAsDataUrl(file);
        const imageEntry = { name: file.name, dataUrl };
        setSessionImages((prev) => [...prev.filter((img) => img.name !== file.name), imageEntry]);
        setAttachedImages((prev) => [...prev.filter((img) => img.name !== file.name), imageEntry]);
      } catch (error) {
        console.error("Failed to read uploaded image.", error);
        window.alert("Couldn't read that image. Please try another file.");
      }
      return;
    }

    try {
      const content = await file.text();
      if (looksLikeBinaryText(content)) {
        window.alert(
          `Couldn't read ${file.name} -- it looks like a binary file we can't interpret. ` +
          `Supported today: images (PNG/JPG/...), crystal structures (CIF, XYZ, XSF, CUBE, POSCAR, VASP), ` +
          `and plain text (e.g. calculation output logs).`
        );
        return;
      }

      const rawExt = getRawFileExtension(file.name);
      const inferredExt = inferStructureExtension(file.name, content);
      // `inferStructureExtension` always returns *something* -- it falls back
      // to the raw extension when nothing matches, so only a value that's
      // actually in WEAS_SUPPORTED_EXTS means "we really recognized this as
      // a structure." Anything else (a DFT output log, a .txt file, ...) is
      // still attached as text context for the model, just not claimed as
      // something the WEAS viewer can render.
      const isStructure = WEAS_SUPPORTED_EXTS.has(inferredExt);
      const ext = inferredExt || rawExt;

      const fileEntry = { name: file.name, content, rawExt, ext, isStructure };
      setStructureFileContents(prev => ({ ...prev, [file.name]: content }));
      setSessionFiles(prev => {
        const filtered = prev.filter(f => f.name !== file.name);
        return [...filtered, fileEntry];
      });
      setAttachedFiles(prev => {
        const filtered = prev.filter(f => f.name !== file.name);
        return [...filtered, fileEntry];
      });
      if (isStructure) setViewerIdx(0);
    } catch (error) {
      console.error("Failed to read uploaded file.", error);
      window.alert("Couldn't read that file. Please try another one.");
    }
  }

  async function handleImportStructure(source, entry_id, formula) {
    const key = `${source}:${entry_id}`;
    setImportingEntries(prev => new Set(prev).add(key));
    try {
      const resp = await fetch(
        `/api/fetch-structure?source=${encodeURIComponent(source)}&entry_id=${encodeURIComponent(entry_id)}`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const fileEntry = { name: data.filename, content: data.content, rawExt: ".cif", ext: ".cif", isStructure: true };
      setStructureFileContents(prev => ({ ...prev, [data.filename]: data.content }));
      // `chatStructures`'s length right now (before this update lands) is
      // where the new entry will end up, since it's a fresh filename being
      // appended -- select it so the Structure Viewer shows what was just
      // imported instead of leaving whatever was open before.
      setSessionFiles(prev => {
        const filtered = prev.filter(f => f.name !== data.filename);
        if (filtered.length === prev.length) setViewerIdx(chatStructures.length);
        return [...filtered, fileEntry];
      });
      updateStructureSearchState(prev => ({
        ...prev,
        // "Formula" and "Structure" describe the same search target -- keep
        // the formula box in sync with whichever structure was just pulled
        // in, rather than leaving it as whatever was typed to find it.
        formula: formula || prev.formula,
        importedEntries: prev.importedEntries.includes(key) ? prev.importedEntries : [...prev.importedEntries, key],
      }));
    } catch (err) {
      console.error("Import failed:", err);
      window.alert(`Could not import structure from ${source}: ${err.message}`);
    } finally {
      setImportingEntries(prev => { const next = new Set(prev); next.delete(key); return next; });
    }
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer.files[0];
    if (file) readFile(file);
  }

  function activateTool(tool) {
    let targetId = session?.id ?? null;
    if (!targetId) {
      const newSession = createSession(draftProjectId);
      setSessions((prev) => [newSession, ...prev]);
      setActiveId(newSession.id);
      targetId = newSession.id;
    }
    replaceSession(targetId, (current) => ({
      ...current,
      tool: tool.id,
      rightPanelOpen: true,
      // A stale rightPanelView from a *different* tool isn't meaningful (each
      // tool has its own panel names) -- only carry it over when re-picking
      // the tool that's already active; switching modes always resets to the
      // new tool's own default.
      rightPanelView: current.tool === tool.id ? (current.rightPanelView ?? tool.defaultPanel) : tool.defaultPanel,
    }));
    setView("chats");
    setInput("");
    setPlusOpen(false);
    setShowElementPicker(false);
    setShowStructureViewer(false);
    setShowFilesPanel(false);
    inputRef.current?.focus();
  }

  function clearTool() {
    updateCurrentSession((current) => ({
      ...current,
      tool: null,
      rightPanelOpen: false,
      rightPanelView: null,
    }));
  }

  // Full-page tools navigation (header's expand-all-tools button, see
  // its JSX for the icon-btn that calls openToolsOverview). Kept separate
  // from activateTool/clearTool above only in name -- opening a Tool's full
  // page reuses activateTool itself, so it's still the exact same
  // session.tool/rightPanelOpen state the inline side panel reads (design
  // constraint: this navigation is additive, not a fork of Tool state).
  function openToolsOverview() {
    setFullPageView("overview");
  }

  function openToolFullPage(tool) {
    activateTool(tool);
    setFullPageView("detail");
  }

  function backToToolsOverview() {
    setFullPageView("overview");
  }

  function closeFullPage() {
    setFullPageView("none");
  }

  function setRightPanelView(viewName) {
    updateCurrentSession((current) => ({
      ...current,
      rightPanelOpen: true,
      rightPanelView: viewName,
    }));
  }

  function pickerFormulaStr(elems: Record<string, number>) {
    return Object.entries(elems)
      .filter(([, n]) => n > 0)
      .map(([sym, n]) => (n === 1 ? sym : `${sym}${n}`))
      .join(" ");
  }

  function clickPickerElement(symbol) {
    setPickerElements((prev) => ({ ...prev, [symbol]: (prev[symbol] || 0) + 1 }));
  }

  function clearPickerElements() {
    setPickerElements({});
  }

  function insertFormulaIntoInput() {
    const formula = pickerFormulaStr(pickerElements).replace(/\s+/g, "");
    if (!formula) return;
    setInput((prev) => (prev ? `${prev} ${formula}` : formula));
    setPickerElements({});
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      if (inputRef.current) {
        inputRef.current.style.height = "auto";
        inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 140)}px`;
      }
    });
  }

  async function handleRunMlipCalc() {
    const arch = selectedMlipModel.id === "mace-mp" ? "mace_mp" : selectedMlipModel.id;
    const struct = chatStructures[mlipStructIdx];
    if (!struct && mlipCalcType !== "neb") return;

    setMlipCalcLoading(true);
    try {
      let endpoint, body;
      if (mlipCalcType === "singlepoint") {
        endpoint = "/api/mlip/singlepoint";
        body = { structure_content: struct.content, structure_name: struct.name, arch };
      } else if (mlipCalcType === "geomopt") {
        endpoint = "/api/mlip/geomopt";
        body = { structure_content: struct.content, structure_name: struct.name, arch, fmax: mlipFmax, steps: mlipSteps, relax_mode: mlipRelaxMode };
      } else if (mlipCalcType === "phonons") {
        endpoint = "/api/mlip/phonons";
        body = { structure_content: struct.content, structure_name: struct.name, arch, supercell: mlipSupercell, displacement: mlipDisplacement };
      } else if (mlipCalcType === "eos") {
        endpoint = "/api/mlip/eos";
        body = { structure_content: struct.content, structure_name: struct.name, arch, min_volume: mlipMinVol, max_volume: mlipMaxVol, n_volumes: mlipNVolumes };
      } else if (mlipCalcType === "neb") {
        const initStruct = chatStructures[mlipNebInitIdx];
        const finalStruct = chatStructures[mlipNebFinalIdx];
        if (!initStruct || !finalStruct) return;
        endpoint = "/api/mlip/neb";
        body = {
          init_structure_content: initStruct.content, init_structure_name: initStruct.name,
          final_structure_content: finalStruct.content, final_structure_name: finalStruct.name,
          arch, n_images: mlipNImages, fmax: mlipNebFmax,
        };
      }
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      // MLIP endpoint responses are heterogeneous per calc type (singlepoint
      // vs geomopt vs phonons vs eos vs neb each return a different `raw`
      // shape) -- `any` here rather than modeling five distinct backend
      // response schemas under tonight's deadline.
      const resultEntry: {
        id: number;
        type: string;
        arch: string;
        structureName: string;
        error?: string;
        raw?: any;
        summary?: any;
      } = {
        id: Date.now() + Math.random(),
        type: mlipCalcType,
        arch: selectedMlipModel.label,
        structureName: mlipCalcType === "neb"
          ? `${chatStructures[mlipNebInitIdx]?.name} → ${chatStructures[mlipNebFinalIdx]?.name}`
          : struct.name,
      };
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        resultEntry.error = err.detail || `HTTP ${resp.status}`;
      } else {
        const data = await resp.json();
        resultEntry.raw = data.raw;
        resultEntry.summary = data.summary;
        // No auto-post into chat (2026-09-15 decision) -- running a
        // calculation directly from the panel is a direct user action, same
        // as structure-search's "Search databases" button: it stays silent
        // in chat unless the user explicitly clicks ✦ (handleDiscussMlipResult).
      }
      setMlipResultsList((prev) => [resultEntry, ...prev]);
    } catch (err) {
      setMlipResultsList((prev) => [{
        id: Date.now(),
        type: mlipCalcType,
        arch: selectedMlipModel.label,
        structureName: struct?.name ?? "",
        error: err.message,
      }, ...prev]);
    } finally {
      setMlipCalcLoading(false);
    }
  }

  function handleDismissMlipResult(id) {
    setMlipResultsList((prev) => prev.filter((r) => r.id !== id));
  }

  function handleDiscussMlipResult(result) {
    // Same "friendly bubble vs. real LLM content" split as structure-search's
    // ✦ button: the LLM gets the full raw result (already fetched -- it
    // shouldn't re-run the calculation), the user sees a short human bubble.
    const calcLabel = result.type === "singlepoint" ? "Singlepoint"
      : result.type === "geomopt" ? "Geometry Optimisation"
      : result.type === "phonons" ? "Phonons"
      : result.type === "eos" ? "Equation of State" : "NEB";
    const prompt = `Here is the result of an MLIP ${calcLabel} calculation on ` +
      `${result.structureName} using ${result.arch} (already run -- interpret ` +
      `this data, don't run it again):\n\n` +
      "```json\n" + JSON.stringify(result.raw) + "\n```";
    send(prompt, `Help me interpret this ${calcLabel} result for ${result.structureName}`);
  }

  function handleImportMlipStructure(raw, structureName) {
    if (!raw?.optimised_structure) return;
    const content = raw.optimised_structure;
    if (typeof content !== "string" || !content.trim()) {
      console.error("Unrecognised optimised_structure format", content);
      return;
    }
    const baseName = structureName
      ? structureName.replace(/\.[^.]+$/, "")
      : String(Date.now());
    const name = `geo-opt-${baseName}.cif`;
    const file = { name, content, source: "mlip", rawExt: ".cif", ext: ".cif", isStructure: true };
    setSessionFiles((prev) => [...prev, file]);
  }

  function dftRequestBody(struct) {
    return {
      structure_content: struct.content,
      structure_name: struct.name,
      code: dftCode,
      task: dftTask,
      hpc: dftHpc || null,
      overrides: Object.keys(dftOverrides).length > 0 ? dftOverrides : null,
    };
  }

  async function dftPostJson(path, body) {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  // One button, both results: goldilocks-core's `explain` (analysis +
  // advisors, for the Explain tab) and `run` (real generated files, for
  // the Inputs tab) are two separate CLI calls under the hood, but from
  // the user's side "Generate" should produce everything at once, not
  // require a second click into a tab to populate it.
  async function handleDftGenerate() {
    const struct = chatStructures[safeViewerIdx];
    if (!struct) return;
    setDftLoading(true);
    const body = dftRequestBody(struct);
    const [explainOutcome, runOutcome] = await Promise.allSettled([
      dftPostJson("/api/dft/explain", body),
      dftPostJson("/api/dft/run", body),
    ]);
    setDftExplainResult(
      explainOutcome.status === "fulfilled" ? explainOutcome.value : { error: explainOutcome.reason.message }
    );
    setDftRunResult(
      runOutcome.status === "fulfilled" ? runOutcome.value : { error: runOutcome.reason.message }
    );
    setDftLoading(false);
  }

  async function handleDftDownloadBundle() {
    const struct = chatStructures[safeViewerIdx];
    if (!struct) return;
    setDftBundleLoading(true);
    try {
      const resp = await fetch("/api/dft/bundle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(dftRequestBody(struct)),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const stem = struct.name.replace(/\.[^.]+$/, "");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${stem}-bundle.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDftBundleError(err.message);
    } finally {
      setDftBundleLoading(false);
    }
  }

  // Shared by the search panel's button and the element-picker's "search"
  // shortcut. Takes an explicit `sessionId` (captured by the caller before
  // any `await`) rather than reading `session` at completion time -- a slow
  // search shouldn't land in whichever chat happens to be active when the
  // response comes back if the user switched chats while waiting.
  async function runStructureSearch(sessionId, formula, properties) {
    setStructureMatchLoading(true);
    let result;
    try {
      const resp = await fetch("/api/structure-match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: "formula",
          formula,
          properties: properties?.length ? properties : null,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      result = await resp.json();
    } catch (err) {
      result = {
        query_formula: null,
        groups: [],
        ungrouped: [],
        truncated_group_count: 0,
        unavailable_properties: [],
        errors: { network: err.message },
      };
    }
    replaceSession(sessionId, (current) => ({
      ...current,
      modeState: {
        ...current.modeState,
        "structure-search": withStructureSearchEntry(
          current.modeState?.["structure-search"],
          { formula, properties: properties ?? [], result }
        ),
      },
    }));
    setStructureMatchLoading(false);
  }

  async function handleFormulaSearch() {
    const formula = pickerFormulaStr(pickerElements).replace(/\s+/g, "");
    if (!formula || !session) return;

    const tool = TOOLS.find((m) => m.id === "structure-search");
    activateTool(tool);
    updateStructureSearchState({ queryMode: "formula", formula });
    setPickerElements({});

    await runStructureSearch(session.id, formula, []);
  }

  function updateTheme(nextTheme) {
    setThemeChoice(nextTheme);
    writeStorage(STORAGE_KEYS.theme, nextTheme);
  }

  function updateSidebarWidth(width) {
    setSidebarWidth(width);
    writeStorage(STORAGE_KEYS.sidebarWidth, String(width));
  }

  function updateToolsWidth(width) {
    setToolsWidth(width);
    writeStorage(STORAGE_KEYS.toolsWidth, String(width));
  }

  function updateLanguage(lang) {
    setLanguage(lang);
    writeStorage(STORAGE_KEYS.language, lang);
  }

  async function hydrateExperienceLevel() {
    // Server is the real source of truth (design doc 12.2) -- this covers a
    // fresh browser profile / cleared localStorage still finding the
    // preference that was already saved against this same backend.
    try {
      const res = await fetch("/api/preferences");
      if (!res.ok) return;
      const data = await res.json();
      if (data.experience_level) {
        setExperienceLevel(data.experience_level);
        setPendingExperience(data.experience_level);
        writeStorage(STORAGE_KEYS.experience, data.experience_level);
        setShowOnboarding(false);
      }
    } catch {
      // Agent server not running yet -- keep whatever localStorage already had.
    }
  }

  function saveExperience(nextExperience) {
    setExperienceLevel(nextExperience);
    setPendingExperience(nextExperience);
    writeStorage(STORAGE_KEYS.experience, nextExperience);
    setShowOnboarding(false);
    // The model only ever reads this from the server (design doc 12.2: a
    // user preference like an API key, not conversation state) -- localStorage
    // above is just so the UI doesn't flash the onboarding modal before this
    // request finishes.
    fetch("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ experience_level: nextExperience }),
    }).catch(() => {});
  }

  async function saveCredential(provider, apiKey) {
    if (!apiKey) return;
    setCredentialStatus((prev) => ({ ...prev, [provider]: "saving" }));
    try {
      const res = await fetch("/api/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, api_key: apiKey }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCredentialStatus((prev) => ({ ...prev, [provider]: "saved" }));
      refreshConfiguredProviders();
    } catch {
      setCredentialStatus((prev) => ({ ...prev, [provider]: "error" }));
    }
  }

  // Explicit Save button, not save-on-blur -- a stray click into the field
  // (to peek at it, to test something) used to commit whatever was typed the
  // moment focus left, silently overwriting a real saved key with no undo.
  function renderCredentialRow(id, label) {
    const canSave = Boolean(cloudApiKeys[id]?.trim()) && credentialStatus[id] !== "saving";
    return (
      <div className="credential-row" key={id}>
        <span className="credential-label">{label}</span>
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          className="credential-input"
          placeholder={
            configuredProviders[id]
              ? "•••••••• (saved — leave blank to keep it)"
              : t("settings_model_key_placeholder")
          }
          value={cloudApiKeys[id]}
          onChange={(event) => {
            const value = event.target.value;
            setCloudApiKeys((prev) => ({ ...prev, [id]: value }));
            setCredentialStatus((prev) => ({ ...prev, [id]: null }));
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && canSave) saveCredential(id, cloudApiKeys[id]);
          }}
        />
        <button
          type="button"
          className="secondary-btn compact"
          disabled={!canSave}
          onClick={() => saveCredential(id, cloudApiKeys[id])}
        >
          {t("save")}
        </button>
        {credentialStatus[id] === "saving" && (
          <span className="credential-status">Saving…</span>
        )}
        {credentialStatus[id] === "saved" && (
          <span className="credential-status credential-status-ok">Saved</span>
        )}
        {credentialStatus[id] === "error" && (
          <span className="credential-status credential-status-error">
            Couldn't save — is the agent server running?
          </span>
        )}
        {!credentialStatus[id] && configuredProviders[id] && (
          <span className="credential-status credential-status-ok">✓ Configured</span>
        )}
      </div>
    );
  }

  async function refreshConfiguredProviders() {
    try {
      const res = await fetch("/api/credentials");
      if (!res.ok) return;
      setConfiguredProviders(await res.json());
    } catch {
      // Agent server not running yet -- leave everything showing as unconfigured.
    }
  }

  async function refreshProjects() {
    try {
      const res = await fetch("/api/projects");
      if (!res.ok) return;
      const data = await res.json();
      setProjects(data.map((p) => ({ id: p.id, name: p.name, desc: p.description, color: p.color })));
    } catch {
      // Agent server not running yet -- projects list stays empty until it is.
    }
  }

  // Only called once, on mount: it seeds `sessions` from the server's index
  // (design doc 16: that index, not this component, is the source of truth
  // for title/project membership). Calling it again later would clobber any
  // draft chat that hasn't sent its first message yet, since drafts have no
  // row in `conversations` until then -- so later changes go through the
  // local setSessions updaters plus their own API calls instead.
  async function loadConversationsOnce() {
    try {
      const res = await fetch("/api/conversations");
      if (!res.ok) return;
      const data = await res.json();
      setSessions(
        data.map((c) => ({
          id: c.id,
          title: c.title,
          messages: [],
          messagesLoaded: false,
          createdAt: c.updated_at,
          projectId: c.project_id,
          tool: null,
          rightPanelOpen: false,
          rightPanelView: null,
          modeState: {},
        }))
      );
    } catch {
      // Agent server not running yet -- start with an empty chat list.
    }
  }

  async function loadMessagesForSession(sessionId) {
    try {
      const res = await fetch(`/api/chat/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      replaceSession(sessionId, (current) => ({
        ...current,
        // `role: "tool"` entries are the raw tool-call result JSON fed back
        // to the LLM (design doc 12.3: chat never shows raw args/results,
        // only a narrated reply) -- the live SSE path never adds these to
        // `messages` (they go to `modeState` instead), so rehydrating a
        // past conversation from the checkpointer must filter them out the
        // same way, or reopening any chat that ever called a tool dumps
        // the whole JSON payload as its own bubble. Same reasoning for a
        // content-less assistant turn (one that only carried tool_calls,
        // no lead-in text) -- the live path never renders an empty bubble
        // for it either (`writer(delta.content)` only fires when there's
        // real content), so this keeps rehydration consistent with that.
        messages: data.messages
          .filter((m) => m.role !== "tool" && !(m.role === "assistant" && !m.content))
          .map((m) => ({ role: m.role, content: m.content })),
        messagesLoaded: true,
      }));
    } catch {
      // Agent server not running -- leave it empty; sending a new message still works.
    }
  }

  // `displayOverride` lets a caller send a data-heavy `text` (e.g. the
  // Find in Databases "✦" button embedding the current search result as
  // context) while the chat bubble shows a short, human sentence instead of
  // the raw payload -- same real-content/friendly-display split `images`
  // already uses below, applied to a text-only case.
  async function send(text?: string, displayOverride?: string) {
    const rawText = (text ?? input).trim();
    if ((!rawText && attachedFiles.length === 0 && attachedImages.length === 0) || loading || hasPendingConfirmation) return;

    // Images render as thumbnails in the bubble (via `images` below), not as
    // a text label -- only structure-file attachments need a text stand-in.
    let display = displayOverride ?? rawText;
    if (attachedFiles.length > 0) {
      const fileNames = attachedFiles.map(f => `📎 ${f.name}`).join(" · ");
      display = display ? `${display} · ${fileNames}` : fileNames;
    }

    let textContent = rawText;
    if (activeTool && attachedFiles.length > 0) textContent = `[Mode: ${activeTool.label}]\n\n${textContent}`;
    for (const af of attachedFiles) {
      const fenceLanguage = getStructureFenceLanguage(af.ext || af.rawExt);
      // "Attached file", not "attached structure file" -- this fence now also
      // carries generic text attachments (e.g. a DFT output log) that aren't
      // structures at all; extractStructureFromMessageContent still decides
      // "is it a structure" from the content itself, not this label.
      textContent = `${textContent}${textContent ? "\n\n" : ""}[Attached file: ${af.name}]\n\`\`\`${fenceLanguage}\n${af.content.slice(0, 3000)}\n\`\`\``;
    }

    // Qwen3.8 is vision-capable and litellm/ollama_chat accept OpenAI-style
    // image_url content parts (verified 2026-09-15 with a real call) -- only
    // switch `content` to the multipart array shape when there's actually an
    // image, so the common text-only case keeps sending a plain string.
    const content = attachedImages.length > 0
      ? [
          { type: "text", text: textContent || "Describe what you see in the attached image(s)." },
          ...attachedImages.map((img) => ({ type: "image_url", image_url: { url: img.dataUrl } })),
        ]
      : textContent;

    let targetSession = session;
    if (!targetSession) {
      targetSession = createSession(draftProjectId);
      setSessions((prev) => [targetSession, ...prev]);
      setActiveId(targetSession.id);
      setDraftProjectId(null);
    }
    setView("chats");

    const nextMessages = [...targetSession.messages, { role: "user", content, display, images: attachedImages }];
    const nextTitle = targetSession.messages.length
      ? targetSession.title
      : (rawText || activeTool?.label || attachedFiles[0]?.name || attachedImages[0]?.name || "New chat").slice(0, 48);

    replaceSession(targetSession.id, (current) => ({
      ...current,
      messages: nextMessages,
      title: nextTitle,
      rightPanelOpen: current.tool ? true : current.rightPanelOpen,
      rightPanelView: current.rightPanelView ?? getToolById(current.tool)?.defaultPanel ?? null,
    }));

    setInput("");
    setAttachedFiles([]);
    setAttachedImages([]);
    setShowElementPicker(false);
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: targetSession.id,
          message: { role: "user", content },
          model_id: selectedModel.id,
          title: nextTitle,
          project_id: targetSession.projectId,
          tool: targetSession.tool ?? null,
          // Real ids ("dft-workbench"/"ml-analysis", not "dft"/"mlip") -- the
          // old checks here never matched anything, so this was always
          // `null` regardless of which Tool was active (2026-09-15 fix).
          // Still accepted-but-unused server-side until DFT/MLIP get an
          // LLM tool node (design doc Step 3, not this pass) -- see
          // ChatRequest.workspace_state in server.py.
          workspace_state: targetSession.tool === "dft-workbench" ? {
            code: dftCode,
            task: dftTask,
            hpc: dftHpc || null,
            overrides: dftOverrides,
          } : targetSession.tool === "beyond-dft" ? {
            method: selectedBeyondDftMethod,
          } : targetSession.tool === "ml-analysis" ? {
            model: selectedMlipModel?.id ?? null,
          } : null,
        }),
        signal: controller.signal,
      });

      if (!response.ok) throw new Error(`API error ${response.status}`);
      await readChatStream(response, targetSession.id, nextMessages);
    } catch (err) {
      if (err.name !== "AbortError") {
        console.error("LLM error:", err);
        const errMsg = "Error: could not reach the model.";
        replaceSession(targetSession.id, (current) => ({
          ...current,
          messages: [...nextMessages, { role: "assistant", content: errMsg, display: errMsg }],
        }));
      }
    } finally {
      abortRef.current = null;
      setLoading(false);
      setToolStatus(null);
      inputRef.current?.focus();
    }
  }

  // Shared by `send()` and `respondToConfirmation()` -- both POST to
  // /api/chat and get back the same SSE contract (plain text deltas, typed
  // `tool_status`/`tool_result`/`confirmation_needed` events), so both read
  // it the same way. `baseMessages` is what a plain-text delta's assistant
  // bubble gets appended after -- for `send()` that's the just-sent user
  // turn (`nextMessages`); for a resume it's the transcript as of the
  // moment the confirmation card was answered (no new user message).
  async function readChatStream(response, targetSessionId, baseMessages) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let assistantContent = "";
    let buffer = "";
    let currentEventType = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEventType = line.slice(7).trim();
          continue;
        }
        if (!line.startsWith("data: ")) {
          if (!line) currentEventType = null;
          continue;
        }
        const data = line.slice(6);
        if (data === "[DONE]") break;
        if (currentEventType === "tool_status") {
          try {
            const payload = JSON.parse(data);
            setToolStatus(payload.label);
            // Pop the tool's own panel open the moment it's known to be
            // running, not only once the result lands -- same session-id
            // capture reasoning as the tool_result branch below.
            const uiTool = TOOL_CALL_TO_UI_TOOL[payload.tool];
            const tool = uiTool && TOOLS.find((t) => t.id === uiTool);
            if (tool) {
              replaceSession(targetSessionId, (current) => ({
                ...current,
                tool: tool.id,
                rightPanelOpen: true,
                rightPanelView: current.tool === tool.id ? (current.rightPanelView ?? tool.defaultPanel) : tool.defaultPanel,
              }));
            }
          } catch { /* malformed chunk -- skip */ }
          currentEventType = null;
          continue;
        }
        if (currentEventType === "tool_result") {
          // Design doc 12.3: the panel is a second consumer of the same
          // tool result the chat reply narrates -- independent of whether
          // the LLM call was organic chat or the "ask AI" (✦) shortcut.
          // Appends into `targetSessionId`'s own history (not necessarily
          // whatever chat is active *now*), same reasoning as
          // `runStructureSearch`.
          try {
            const payload = JSON.parse(data);
            if (payload.tool === "find_in_databases") {
              replaceSession(targetSessionId, (current) => ({
                ...current,
                modeState: {
                  ...current.modeState,
                  "structure-search": withStructureSearchEntry(current.modeState?.["structure-search"], {
                    formula: payload.result?.query_formula ?? null,
                    properties: [],
                    result: payload.result,
                  }),
                },
              }));
            } else if (Object.hasOwn(TOOL_CALL_TO_UI_TOOL, payload.tool) && TOOL_CALL_TO_UI_TOOL[payload.tool] === "ml-analysis") {
              const resultEntry = {
                id: Date.now() + Math.random(),
                type: payload.tool.replace("run_mlip_", "").replace("geometry_optimization", "geomopt").replace("equation_of_state", "eos"),
                arch: "MACE-MP-0",
                structureName: payload.args?.structure_name
                  ?? (payload.args?.init_structure_name && payload.args?.final_structure_name
                    ? `${payload.args.init_structure_name} → ${payload.args.final_structure_name}` : ""),
                raw: payload.result,
                summary: payload.result?.error ? null : undefined,
                error: payload.result?.error,
              };
              replaceSession(targetSessionId, (current) => ({
                ...current,
                modeState: {
                  ...current.modeState,
                  "ml-analysis": {
                    ...(current.modeState?.["ml-analysis"] ?? DEFAULT_MLIP_STATE),
                    mlipResultsList: [resultEntry, ...(current.modeState?.["ml-analysis"]?.mlipResultsList ?? [])],
                  },
                },
              }));
            } else if (payload.tool === "dft_explain" || payload.tool === "dft_generate") {
              // Same "panel is a second consumer" reasoning as above --
              // `payload.result` here is always the *full* RunResult (with
              // pseudo/* included), never the LLM-trimmed copy, since
              // graph.py's call_tool streams `panel_output` over this event
              // and reserves `model_dump_for_llm()` for the LLM's own copy.
              const key = payload.tool === "dft_explain" ? "explainResult" : "runResult";
              replaceSession(targetSessionId, (current) => ({
                ...current,
                modeState: {
                  ...current.modeState,
                  "dft-workbench": {
                    ...(current.modeState?.["dft-workbench"] ?? DEFAULT_DFT_STATE),
                    [key]: payload.result,
                  },
                },
              }));
            }
          } catch { /* malformed chunk -- skip */ }
          currentEventType = null;
          continue;
        }
        if (currentEventType === "confirmation_needed") {
          // Design doc 17.10: a structured card in the transcript, not a
          // modal -- the graph is genuinely paused (server.py's
          // `snapshot.next`) waiting for exactly this answer.
          try {
            const payload = JSON.parse(data);
            replaceSession(targetSessionId, (current) => ({
              ...current,
              messages: [...current.messages, {
                role: "confirmation", tool: payload.tool, args: payload.args,
                label: payload.label, resolved: null,
              }],
            }));
          } catch { /* malformed chunk -- skip */ }
          currentEventType = null;
          continue;
        }
        currentEventType = null;
        if (!assistantContent) setToolStatus(null);
        try { assistantContent += JSON.parse(data); } catch { assistantContent += data; }
        replaceSession(targetSessionId, (current) => ({
          ...current,
          messages: [...baseMessages, { role: "assistant", content: assistantContent, display: assistantContent }],
        }));
      }
    }
  }

  // Answers a pending confirmation card (design doc 17.10) -- resumes the
  // paused graph via /api/chat's `resume` field, no new user message.
  async function respondToConfirmation(sessionId, messageIndex, approved) {
    let baseMessages = null;
    replaceSession(sessionId, (current) => {
      const messages = current.messages.map((m, i) =>
        i === messageIndex ? { ...m, resolved: approved } : m
      );
      baseMessages = messages;
      return { ...current, messages };
    });
    setLoading(true);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: sessionId, resume: { approved } }),
      });
      if (!response.ok) throw new Error(`API error ${response.status}`);
      await readChatStream(response, sessionId, baseMessages ?? []);
    } catch (err) {
      console.error("LLM error:", err);
      const errMsg = "Error: could not reach the model.";
      replaceSession(sessionId, (current) => ({
        ...current,
        messages: [...(baseMessages ?? current.messages), { role: "assistant", content: errMsg, display: errMsg }],
      }));
    } finally {
      setLoading(false);
      setToolStatus(null);
    }
  }

  function renderProjectsView() {
    return (
      <div className="projects-view">
        <div className="projects-head">
          <div>
            <h1>Projects</h1>
            <p>Group chats, structures, and workflows into focused research spaces.</p>
          </div>
          <button className="primary-btn" onClick={() => setCreateProjectOpen(true)}>
            New project
          </button>
        </div>
        <div className="projects-grid">
          {projects.map((project) => {
            const count = sessions.filter((item) => item.projectId === project.id).length;
            return (
            <div key={project.id} className="project-card">
              <div className="project-card-top">
                <div className="project-card-title">
                  <div className="project-color" style={{ background: project.color }} />
                  <div>
                    <div className="project-name">{project.name}</div>
                    <div className="project-meta">{count} chat{count === 1 ? "" : "s"}</div>
                  </div>
                </div>
                <button
                  className="ghost-icon-btn"
                  onClick={() => deleteProject(project.id)}
                  aria-label={`Delete ${project.name}`}
                >
                  <TrashIcon />
                </button>
              </div>
              <div className="project-desc">{project.desc}</div>
              <button className="ghost-btn" onClick={() => openProject(project.id)}>
                Open project
              </button>
            </div>
          )})}
        </div>
      </div>
    );
  }

  function renderProjectHome() {
    if (!activeProject) return null;
    const projectChats = sessions.filter((s) => s.projectId === activeProject.id);
    return (
      <div className="project-home">
        <div className="project-home-head">
          <div className="project-home-title">
            <span className="project-dot-lg" style={{ background: activeProject.color }} />
            <h1>{activeProject.name}</h1>
          </div>
          <button className="ghost-icon-btn" onClick={() => deleteProject(activeProject.id)} aria-label="Delete project">
            <TrashIcon />
          </button>
        </div>

        <div className="project-home-tabs">
          <button className={`project-tab-btn${projectTab === "chats" ? " active" : ""}`} onClick={() => setProjectTab("chats")}>{t("chats")}</button>
          <button className={`project-tab-btn${projectTab === "sources" ? " active" : ""}`} onClick={() => setProjectTab("sources")}>{t("sources")}</button>
        </div>

        {projectTab === "chats" ? (
          <div className="project-home-body">
            {projectChats.length === 0 ? (
              <div className="project-home-empty">{t("no_chats_yet")}</div>
            ) : (
              projectChats.map((chat) => {
                const tool = getToolById(chat.tool);
                return (
                  <div
                    key={chat.id}
                    className="project-chat-row"
                    onClick={() => { setActiveId(chat.id); setView("chats"); setActiveProjectId(activeProject.id); }}
                  >
                    <div className="project-chat-info">
                      <div className="project-chat-title">{chat.title}</div>
                      <div className="project-chat-meta">{tool ? tool.label : t("general_chat")}</div>
                    </div>
                    <button
                      className="ghost-icon-btn"
                      onClick={(e) => { e.stopPropagation(); deleteSession(chat.id); }}
                      aria-label="Delete chat"
                    >
                      <TrashIcon />
                    </button>
                  </div>
                );
              })
            )}
          </div>
        ) : (
          <div className="project-home-body">
            {activeProject.sources?.length ? (
              activeProject.sources.map((source) => (
                <div key={source} className="project-source-row">{source}</div>
              ))
            ) : (
              <div className="project-home-empty">{t("no_sources_yet")}</div>
            )}
          </div>
        )}
      </div>
    );
  }

  function renderToolPicker() {
    return (
      <div className="workspace-content">
        {TOOLS.map((tool) => (
          <div key={tool.id} className="menu-item" onClick={() => activateTool(tool)}>
            <div className="menu-item-icon" style={{ color: tool.color }}>
              <ToolGlyph tool={tool} size={18} />
            </div>
            <div className="menu-item-copy">
              <strong>{tool.label}</strong>
              <span>{t("tool_" + tool.id.replace(/-/g, "_") + "_launcher") || tool.launcherDesc}</span>
            </div>
          </div>
        ))}
      </div>
    );
  }

  function renderWorkspace() {
    if (!activeTool || !session.rightPanelOpen) return renderToolPicker();

    const panelView = session.rightPanelView ?? activeTool.defaultPanel;

    if (activeTool.id === "structure-search") {
      return (
        <div className="workspace-content">
          <div className="workspace-tool-header" style={{ "--tool-color": activeTool.color } as CSSPropertiesWithVars}>
            <div className="workspace-tool-header-top">
              <div className="workspace-tool-title">
                <ToolGlyph tool={activeTool} size={18} />
                <span>{activeTool.label}</span>
              </div>
              <button className="ghost-icon-btn" onClick={clearTool} title={t("dismiss_tool")}>
                <CloseIcon />
              </button>
            </div>
          </div>
          <div className="workspace-stack">
            <div className="workspace-section">
              <div className="workspace-title">{t("structure_or_formula")}</div>
              <div className="db-query-toggle">
                <button
                  className={`db-query-toggle-btn${dbQueryMode === "structure" ? " active" : ""}`}
                  onClick={() => updateStructureSearchState({ queryMode: "structure" })}
                >Structure</button>
                <button
                  className={`db-query-toggle-btn${dbQueryMode === "formula" ? " active" : ""}`}
                  onClick={() => updateStructureSearchState({ queryMode: "formula" })}
                >Formula</button>
              </div>
              {dbQueryMode === "structure" ? (
                <>
                  <SimpleSelect
                    label="Structure"
                    value={safeViewerIdx}
                    items={chatStructures.length === 0
                      ? [{ id: -1, label: "No structures loaded" }]
                      : chatStructures.map((s, i) => ({ id: i, label: s.name }))
                    }
                    isOpen={openDftPicker === "db-struct"}
                    onToggle={() => setOpenDftPicker((p) => p === "db-struct" ? null : "db-struct")}
                    onSelect={(id) => { setViewerIdx(id); setOpenDftPicker(null); }}
                    disabled={chatStructures.length === 0}
                  />
                  <StructureUploadControl onFile={readFile} />
                </>
              ) : (
                <div className={`workspace-picker${openDftPicker === "db-formula-history" ? " open" : ""}`} style={{ marginTop: 6 }}>
                  <div className="workspace-picker-trigger">
                    <div className="workspace-picker-copy" style={{ flex: 1 }}>
                      <input
                        className="db-formula-input"
                        value={dbFormulaInput}
                        onChange={e => updateStructureSearchState({ formula: e.target.value })}
                        placeholder="e.g. Fe2O3"
                        spellCheck={false}
                        autoFocus
                      />
                    </div>
                    {dbFormulaInput && (
                      <button className="ghost-icon-btn" style={{ flexShrink: 0 }} onClick={() => updateStructureSearchState({ formula: "" })}>
                        <CloseIcon />
                      </button>
                    )}
                    {dbSearchHistory.length > 0 && (
                      <button
                        className="workspace-picker-caret"
                        type="button"
                        onClick={() => setOpenDftPicker((p) => p === "db-formula-history" ? null : "db-formula-history")}
                      ><CaretDownIcon /></button>
                    )}
                  </div>
                  {openDftPicker === "db-formula-history" && dbSearchHistory.length > 0 && (
                    <div className="workspace-picker-menu">
                      <div className="workspace-picker-group">
                        <div className="workspace-picker-options">
                          {dbSearchHistory.map((entry, i) => (
                            <button
                              key={i}
                              type="button"
                              className={`workspace-picker-option${i === dbActiveSearchIndex ? " active" : ""}`}
                              onClick={() => {
                                updateStructureSearchState({ activeSearchIndex: i, formula: entry.formula ?? "" });
                                setOpenDftPicker(null);
                              }}
                            >
                              <span className="workspace-picker-option-title">
                                {entry.formula || "?"}
                                {entry.properties?.length > 0 && ` (${entry.properties.join(", ")})`}
                              </span>
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <div className="db-property-section">
                <div className="db-property-label">What are you looking for?</div>
                <div className="db-property-chips">
                  {[
                    { id: "electronic", label: "Electronic structure" },
                    { id: "stability",  label: "Stability & energy" },
                    { id: "magnetic",   label: "Magnetic" },
                    { id: "elastic",    label: "Elastic & mechanical" },
                    { id: "phonons",    label: "Phonons & thermal" },
                    { id: "optical",    label: "Optical" },
                  ].map(({ id, label }) => {
                    const active = dbPropertyTags.includes(id);
                    return (
                      <button
                        key={id}
                        className={`db-property-chip${active ? " active" : ""}`}
                        onClick={() => updateStructureSearchState(prev => ({
                          ...prev,
                          propertyTags: active ? prev.propertyTags.filter(t => t !== id) : [...prev.propertyTags, id],
                        }))}
                      >{label}</button>
                    );
                  })}
                </div>
              </div>
              <button
                className={`search-databases-btn${structureMatchLoading ? " loading" : ""}`}
                disabled={(dbQueryMode === "structure" ? chatStructures.length === 0 : !dbFormulaInput.trim()) || structureMatchLoading || !session}
                onClick={async () => {
                  if (dbQueryMode === "structure") {
                    // File-mode matching always 501s today (needs the core
                    // MCP client, implementation plan Step 1) -- kept as a
                    // direct one-off call rather than routed through
                    // `runStructureSearch`/history, since there's no grouped
                    // result shape to store yet.
                    setStructureMatchLoading(true);
                    try {
                      const resp = await fetch("/api/structure-match", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                          mode: "file",
                          structure_content: structureFileContents[activeViewerSource.name] ?? activeViewerSource.content,
                          structure_name: activeViewerSource.name,
                        }),
                      });
                      if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        throw new Error(err.detail || `HTTP ${resp.status}`);
                      }
                    } catch (err) {
                      window.alert(err.message);
                    } finally {
                      setStructureMatchLoading(false);
                    }
                    return;
                  }
                  await runStructureSearch(session.id, dbFormulaInput.trim(), dbPropertyTags);
                }}
              >
                {structureMatchLoading ? t("searching") : t("search_databases")}
              </button>
            </div>

            <div className="workspace-section">
              <div className="workspace-task-builder-header" style={{ marginTop: 0, marginBottom: 8 }}>
                <span className="workspace-task-builder-label">{t("candidate_structures")}</span>
                <button
                  className="workspace-section-recommend"
                  data-tooltip="Ask Goldilocks to interpret these results (no re-search)"
                  disabled={!structureMatchResults || (structureMatchResults.groups?.length ?? 0) === 0}
                  onClick={() => {
                    // Answers the "should ✦ re-run the search or read what's
                    // already here" question: it's handed the panel's current
                    // GroupedSearchResult as-is -- the LLM interprets existing
                    // data, it doesn't call find_in_databases again.
                    const label = structureMatchResults.query_formula || dbFormulaInput || "this formula";
                    const prompt = `Here is the current Find in Databases search result for ${label} ` +
                      `(already fetched -- interpret this data, don't search again):\n\n` +
                      "```json\n" + JSON.stringify(structureMatchResults) + "\n```";
                    send(prompt, `Help me interpret the current search results for ${label}`);
                  }}
                >✦</button>
              </div>
              {(() => {
                const groups = structureMatchResults?.groups ?? [];
                const ungrouped = structureMatchResults?.ungrouped ?? [];
                const errors = structureMatchResults?.errors ?? {};
                const unavailable = structureMatchResults?.unavailable_properties ?? [];
                const truncated = structureMatchResults?.truncated_group_count ?? 0;
                const ungroupedOpen = dbGroupOpen["__ungrouped__"] !== false;

                const renderEntryRow = (r, i) => {
                  const key = `${r.source}:${r.entry_id}`;
                  const isImporting = importingEntries.has(key);
                  const isImported = importedEntries.includes(key);
                  return (
                    <div key={i} className="db-result-row">
                      <div className="db-result-left">
                        <span className="db-result-formula">{r.source}</span>
                        {r.entry_id && (
                          <span className="db-result-sg" title={r.entry_id}>
                            {r.entry_id.length > 12 ? r.entry_id.slice(0, 10) + "…" : r.entry_id}
                          </span>
                        )}
                      </div>
                      {r.entry_id && (
                        <button
                          className={`db-import-btn${isImported ? " imported" : ""}`}
                          disabled={isImporting || isImported}
                          onClick={() => handleImportStructure(r.source, r.entry_id, r.formula)}
                        >
                          {isImported ? "✓" : isImporting ? "…" : "Import"}
                        </button>
                      )}
                      <a className="db-result-link" href={r.url} target="_blank" rel="noreferrer">Visit ↗</a>
                    </div>
                  );
                };

                return (
                  <>
                    {Object.keys(errors).length > 0 && (
                      <div className="db-empty-hint" style={{ marginBottom: 6 }}>
                        <span className="db-error-hint">Failed: {Object.keys(errors).join(", ")}</span>
                      </div>
                    )}
                    {unavailable.length > 0 && (
                      <div className="db-empty-hint" style={{ marginBottom: 6 }}>
                        <span className="db-error-hint">Not available from these sources: {unavailable.join(", ")}</span>
                      </div>
                    )}
                    {structureMatchResults && groups.length === 0 && ungrouped.length === 0 && Object.keys(errors).length === 0 && (
                      <div className="db-empty-hint">No candidates found.</div>
                    )}
                    {groups.map((g, gi) => {
                      const groupKey = `${g.formula}-${g.spacegroup}-${gi}`;
                      const isOpen = dbGroupOpen[groupKey] !== false;
                      const properties = DB_PROPERTY_DISPLAY.filter(p => g[p.key] != null);
                      return (
                        <div key={groupKey} className="db-group">
                          <button className="db-group-header" onClick={() => setDbGroupOpen(prev => ({ ...prev, [groupKey]: !isOpen }))}>
                            <span className={`db-group-caret${isOpen ? " open" : ""}`}>›</span>
                            <span className="db-group-name">{g.formula} · {g.spacegroup || "unknown SG"}</span>
                            {g.energy_above_hull === 0 && <span className="db-result-match-badge">Ground state</span>}
                            <span className="db-group-count">{g.sources.length} source{g.sources.length > 1 ? "s" : ""}</span>
                          </button>
                          {isOpen && (
                            <div className="db-group-results">
                              {properties.length > 0 && (
                                <div className="db-property-grid">
                                  {properties.map(p => (
                                    <div key={p.key} className="db-property-item">
                                      <span className="db-property-key">{p.label}</span>
                                      <span className="db-property-value">
                                        {formatDbPropertyValue(g[p.key])}{p.unit ? ` ${p.unit}` : ""}
                                        {p.methodKey && g[p.methodKey] ? ` (${g[p.methodKey]})` : ""}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              )}
                              {g.entries.map(renderEntryRow)}
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {truncated > 0 && (
                      <div className="db-empty-hint">{truncated} more distinct structure{truncated > 1 ? "s" : ""} not shown.</div>
                    )}
                    {ungrouped.length > 0 && (
                      <div className="db-group">
                        <button className="db-group-header" onClick={() => setDbGroupOpen(prev => ({ ...prev, __ungrouped__: !ungroupedOpen }))}>
                          <span className={`db-group-caret${ungroupedOpen ? " open" : ""}`}>›</span>
                          <span className="db-group-name">Other entries (space group unknown)</span>
                          <span className="db-group-count">{ungrouped.length}</span>
                        </button>
                        {ungroupedOpen && (
                          <div className="db-group-results">{ungrouped.map(renderEntryRow)}</div>
                        )}
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      );
    }

    if (activeTool.id === "dft-workbench") {
      // Grouped once per render from the flat /api/dft/capabilities
      // settings[] list -- 49 items, cheap, no memoization needed.
      const allSettings = dftCapabilities?.settings ?? [];
      const settingsByGroup = [];
      for (const spec of allSettings) {
        let bucket = settingsByGroup.find((b) => b.group === spec.group);
        if (!bucket) { bucket = { group: spec.group, specs: [] }; settingsByGroup.push(bucket); }
        bucket.specs.push(spec);
      }
      const overrideCount = Object.keys(dftOverrides).length;
      const pseudoTables = dftCapabilities?.pseudopotential_tables ?? [];
      const hpcProfiles = dftCapabilities?.hpc_profiles ?? [];

      function setOverrideEnabled(spec, enabled) {
        setDftOverrides((prev) => {
          const next = { ...prev };
          if (enabled) next[spec.key] = spec.default ?? (spec.type === "number" ? 0 : "");
          else delete next[spec.key];
          return next;
        });
      }
      function setOverrideValue(spec, raw) {
        let value = raw;
        if (spec.type === "number") value = raw === "" ? "" : Number(raw);
        else if (spec.type === "boolean") value = raw === "true";
        else if (spec.type === "array") {
          try { value = JSON.parse(raw); } catch { return; } // wait for valid JSON before storing
        }
        setDftOverrides((prev) => ({ ...prev, [spec.key]: value }));
      }
      function renderOverrideInput(spec) {
        if (spec.key === "pseudo_table_id" && pseudoTables.length > 0) {
          return (
            <select className="workspace-override-input" value={dftOverrides[spec.key] ?? ""} onChange={(e) => setOverrideValue(spec, e.target.value)}>
              <option value="" disabled>Choose a pseudopotential table…</option>
              {pseudoTables.map((p) => (
                <option key={p.id} value={p.id}>{p.provider} · {p.functional} · {p.accuracy}{p.default ? " (default)" : ""}</option>
              ))}
            </select>
          );
        }
        if (spec.enum) {
          return (
            <select className="workspace-override-input" value={dftOverrides[spec.key]} onChange={(e) => setOverrideValue(spec, e.target.value)}>
              {spec.enum.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          );
        }
        if (spec.type === "boolean") {
          return (
            <select className="workspace-override-input" value={String(dftOverrides[spec.key])} onChange={(e) => setOverrideValue(spec, e.target.value)}>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          );
        }
        return (
          <input
            className="workspace-override-input"
            type={spec.type === "number" ? "number" : "text"}
            value={dftOverrides[spec.key] ?? ""}
            onChange={(e) => setOverrideValue(spec, e.target.value)}
          />
        );
      }

      // "Analysis + advisors" rendering for the Explain tab -- one row per
      // `records{}` entry (a structure fact like is_metal, or a
      // settings-group like cutoffs/k_sampling), showing who resolved it
      // (source: human/ml/llm/heuristic) and with what value/reason.
      function renderRecordValue(value) {
        if (value === null || value === undefined) return <span style={{ color: "var(--muted)" }}>—</span>;
        if (typeof value !== "object") return <span>{String(value)}</span>;
        const entries = Object.entries(value).filter(([k]) => k !== "warnings");
        if (entries.length === 0) return <span style={{ color: "var(--muted)" }}>—</span>;
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {entries.map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 6, fontSize: "0.8rem" }}>
                <span style={{ color: "var(--muted)" }}>{k}:</span>
                <span>{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</span>
              </div>
            ))}
          </div>
        );
      }
      // Cast to Record<string, any>: TS's Object.entries overload infers the
      // value type as `unknown` (rather than `any`) when given a bare `any`
      // argument, which would make every `record.*` access below an error.
      const recordEntries = dftExplainResult?.records
        ? Object.entries(dftExplainResult.records as Record<string, any>)
        : [];

      return (
        <div className="workspace-content">
          <div className="workspace-tool-header" style={{ "--tool-color": activeTool.color } as CSSPropertiesWithVars}>
            <div className="workspace-tool-header-top">
              <div className="workspace-tool-title">
                <ToolGlyph tool={activeTool} size={18} />
                <span>{activeTool.label}</span>
              </div>
              <button className="ghost-icon-btn" onClick={clearTool} title={t("dismiss_tool")}>
                <CloseIcon />
              </button>
            </div>
            <div className="workspace-powered-by">
              {t("powered_by")}{" "}
              <a href="https://github.com/stfc/goldilocks-core" target="_blank" rel="noreferrer">goldilocks-core</a>
            </div>
          </div>
          <div className="workspace-tabs">
            <button className={`workspace-tab${panelView === "setup" ? " active" : ""}`} onClick={() => setRightPanelView("setup")}>{t("tab_setup")}</button>
            <button className={`workspace-tab${panelView === "inputs" ? " active" : ""}`} onClick={() => setRightPanelView("inputs")}>{t("tab_inputs")}</button>
            <button className={`workspace-tab${panelView === "checks" ? " active" : ""}`} onClick={() => setRightPanelView("checks")}>{t("tab_checks")}</button>
          </div>
          {panelView === "setup" && (
            <div className="workspace-stack">
              <div className="workspace-section" ref={dftPickerRef}>
                <div className="workspace-form">
                  <SimpleSelect
                    label="Structure"
                    value={safeViewerIdx}
                    items={chatStructures.length === 0
                      ? [{ id: -1, label: t("no_structure_in_chat") }]
                      : chatStructures.map((s, i) => ({ id: i, label: s.name }))
                    }
                    isOpen={openDftPicker === "dft-struct"}
                    onToggle={() => setOpenDftPicker((p) => p === "dft-struct" ? null : "dft-struct")}
                    onSelect={(id) => { setViewerIdx(id); setOpenDftPicker(null); }}
                    disabled={chatStructures.length === 0}
                  />
                  <StructureUploadControl onFile={readFile} />
                  <WorkspacePicker
                    label="Code"
                    value={dftCode}
                    option={dftCodeMeta}
                    groups={dftCodeGroupsFromCapabilities}
                    isOpen={openDftPicker === "code"}
                    onToggle={() => setOpenDftPicker((current) => (current === "code" ? null : "code"))}
                    onSelect={(value) => { setDftCode(value); setOpenDftPicker(null); }}
                  />
                  <WorkspacePicker
                    label="Task"
                    value={dftTask}
                    option={dftTaskMeta}
                    groups={dftTaskGroupsFromCapabilities}
                    isOpen={openDftPicker === "task"}
                    onToggle={() => setOpenDftPicker((current) => (current === "task" ? null : "task"))}
                    onSelect={(value) => { setDftTask(value); setOpenDftPicker(null); }}
                  />
                  <WorkspacePicker
                    label="HPC profile"
                    value={dftHpc}
                    option={dftHpcMeta}
                    groups={dftHpcGroupsFromCapabilities}
                    isOpen={openDftPicker === "hpc"}
                    onToggle={() => setOpenDftPicker((current) => (current === "hpc" ? null : "hpc"))}
                    onSelect={(value) => { setDftHpc(value); setOpenDftPicker(null); }}
                  />
                  {dftCapabilitiesLoading && hpcProfiles.length === 0 && (
                    <div className="workspace-hint">Loading real HPC profiles from goldilocks-core…</div>
                  )}
                  <SimpleSelect
                    label="Advisor model"
                    value={selectedDftAdvisorModel}
                    items={DFT_ADVISOR_MODELS.length > 0
                      ? DFT_ADVISOR_MODELS.map((m) => ({ id: m.id, label: m.label }))
                      : [{ id: "__none__", label: "Provided by goldilocks-core" }]}
                    isOpen={DFT_ADVISOR_MODELS.length > 0 && openDftPicker === "dft-advisor"}
                    onToggle={DFT_ADVISOR_MODELS.length > 0 ? () => setOpenDftPicker((p) => p === "dft-advisor" ? null : "dft-advisor") : undefined}
                    onSelect={DFT_ADVISOR_MODELS.length > 0 ? (id) => { setSelectedDftAdvisorModel(id); setOpenDftPicker(null); } : undefined}
                    disabled={DFT_ADVISOR_MODELS.length === 0}
                  />
                </div>
              </div>

              <button className="mlip-run-btn" disabled={dftLoading || chatStructures.length === 0} onClick={handleDftGenerate}>
                {dftLoading ? "Generating…" : "Generate"}
              </button>
              {(dftExplainResult?.error || dftRunResult?.error) && (
                <div className="check-item error">{dftExplainResult?.error || dftRunResult?.error}</div>
              )}
              <div className="workspace-hint">
                One click runs goldilocks-core's real analysis+advisors and
                generates the recommended input, pseudopotential, and
                submission script -- see the Inputs and Explain tabs.
              </div>

              <div className="workspace-section">
                <div className="workspace-task-builder-header">
                  <span className="workspace-task-builder-label">
                    Settings overrides {overrideCount > 0 ? `(${overrideCount} set)` : ""}
                  </span>
                </div>
                <div className="workspace-hint">
                  goldilocks-core's advisors auto-resolve every setting from
                  the structure -- add an override only for the ones you
                  disagree with.
                </div>
                {dftCapabilitiesLoading && <div className="workspace-hint">Loading real settings from goldilocks-core...</div>}
                {dftCapabilitiesError && <div className="check-item error">{dftCapabilitiesError}</div>}
                {allSettings.length > 0 && (
                  <select
                    className="workspace-override-input"
                    style={{ width: "100%" }}
                    value={dftOverrideDraftKey}
                    onChange={(e) => {
                      const key = e.target.value;
                      if (!key) return;
                      const spec = allSettings.find((s) => s.key === key);
                      if (spec) setOverrideEnabled(spec, true);
                      setDftOverrideDraftKey("");
                    }}
                  >
                    <option value="">+ Add an override…</option>
                    {settingsByGroup.map(({ group, specs }) => {
                      const available = specs.filter((s) => !Object.hasOwn(dftOverrides, s.key));
                      if (available.length === 0) return null;
                      return (
                        <optgroup key={group} label={group}>
                          {available.map((s) => (
                            <option key={s.key} value={s.key}>{s.key}{s.unit ? ` (${s.unit})` : ""}</option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                )}
                {overrideCount === 0 && (
                  <div className="workspace-hint">No overrides added -- goldilocks-core's defaults will be used.</div>
                )}
                {Object.keys(dftOverrides).map((key) => {
                  const spec = allSettings.find((s) => s.key === key);
                  if (!spec) return null;
                  return (
                    <div key={key} className="workspace-settings-row" title={spec.description}>
                      <div className="workspace-settings-row-label">{spec.group} · {spec.key}{spec.unit ? ` (${spec.unit})` : ""}</div>
                      <div className="workspace-settings-row-control">
                        {renderOverrideInput(spec)}
                        <button className="ghost-icon-btn" onClick={() => setOverrideEnabled(spec, false)} title="Remove override">
                          <CloseIcon />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {panelView === "inputs" && (
            <div className="workspace-stack">
              <button
                className="mlip-run-btn"
                disabled={dftBundleLoading || chatStructures.length === 0}
                onClick={handleDftDownloadBundle}
              >
                {dftBundleLoading ? "Preparing bundle…" : "⬇ Download bundle (.zip)"}
              </button>
              {dftBundleError && <div className="check-item error">{dftBundleError}</div>}
              {dftRunResult?.error && <div className="check-item error">{dftRunResult.error}</div>}
              {dftRunResult?.files && Object.entries(dftRunResult.files as Record<string, any>).map(([name, content]) => (
                <details key={name} className="workspace-section" open={!name.startsWith("pseudo/")}>
                  <summary className="workspace-title" style={{ cursor: "pointer" }}>
                    {name}
                    {name.startsWith("pseudo/") ? " (pseudopotential)" : name === "submit.sh" ? " (submission script)" : ""}
                  </summary>
                  <pre className="workspace-code"><code>{content}</code></pre>
                </details>
              ))}
              {!dftRunResult && <div className="workspace-hint">{t("generated_preview")} -- click Generate on the Setup tab to run goldilocks-core for real.</div>}
            </div>
          )}
          {panelView === "checks" && (
            <div className="workspace-stack">
              <div className="workspace-section">
                <div className="workspace-title">Analysis &amp; advisors</div>
                {dftExplainResult?.error && <div className="check-item error">{dftExplainResult.error}</div>}
                {recordEntries.map(([key, record]) => (
                  <div key={key} className="workspace-settings-row">
                    <div className="workspace-settings-row-label" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span>{key}</span>
                      <span className={`workspace-source-badge${record.status !== "resolved" ? " error" : ""}`}>
                        {record.status}{record.source ? ` · ${record.source}` : ""}
                      </span>
                    </div>
                    {record.status === "resolved" && renderRecordValue(record.value)}
                    {record.reason && <span className="workspace-hint">{record.reason}</span>}
                    {record.blocked_by && <span className="workspace-hint">Blocked by: {record.blocked_by}</span>}
                  </div>
                ))}
                {!dftExplainResult && <div className="workspace-hint">Click Generate on the Setup tab to see goldilocks-core's real analysis and advisors here.</div>}
              </div>
              <div className="workspace-section">
                <div className="workspace-title">{t("validation")}</div>
                {dftExplainResult?.warnings?.map((w, i) => (
                  <div key={i} className={`check-item${w.level === "warning" ? " error" : ""}`}>{w.message}</div>
                ))}
                {dftExplainResult?.warnings?.length === 0 && <div className="check-item">No warnings from the last Generate run.</div>}
              </div>
            </div>
          )}
        </div>
      );
    }

    if (activeTool.id === "beyond-dft") {
      return (
        <div className="workspace-content">
          <div className="workspace-tool-header" style={{ "--tool-color": activeTool.color } as CSSPropertiesWithVars}>
            <div className="workspace-tool-header-top">
              <div className="workspace-tool-title">
                <ToolGlyph tool={activeTool} size={18} />
                <span>{activeTool.label}</span>
              </div>
              <button className="ghost-icon-btn" onClick={clearTool} title={t("dismiss_tool")}>
                <CloseIcon />
              </button>
            </div>
            <div className="workspace-no-backing" title={t("beyond_dft_no_backing_hint")}>
              <WarningIcon />
              <span>{t("beyond_dft_no_backing")}</span>
            </div>
          </div>
          <div className="workspace-tabs">
            <button className={`workspace-tab${panelView === "setup" ? " active" : ""}`} onClick={() => setRightPanelView("setup")}>{t("tab_setup")}</button>
          </div>
          {panelView === "setup" && (
            <div className="workspace-stack">
              <div className="workspace-section">
                <div className="workspace-title">{t("task_builder")}</div>
                <div className="workspace-form">
                  <WorkspacePicker
                    label="Method"
                    value={selectedBeyondDftMethod}
                    option={selectedBeyondDftMethodMeta}
                    groups={BEYOND_DFT_METHOD_GROUPS}
                    isOpen={openDftPicker === "beyond-method"}
                    onToggle={() => setOpenDftPicker((current) => (current === "beyond-method" ? null : "beyond-method"))}
                    onSelect={(value) => {
                      setSelectedBeyondDftMethod(value);
                      setOpenDftPicker(null);
                    }}
                    onAsk={(item) => {
                      setOpenDftPicker(null);
                      setInput(`What should I know about ${item.label}? When is it a good choice?`);
                      inputRef.current?.focus();
                    }}
                    onRecommend={(item) => {
                      setOpenDftPicker(null);
                      setInput(`Goldilocks auto-selected ${item.label} as the beyond-DFT method. Why is this a good starting point, and when should I use an alternative approach?`);
                      inputRef.current?.focus();
                    }}
                  />
                  <WorkspacePicker
                    label="Code"
                    value={beyondDftCode}
                    option={beyondDftCodeMeta}
                    groups={DFT_CODE_GROUPS}
                    isOpen={openDftPicker === "code"}
                    onToggle={() => setOpenDftPicker((current) => (current === "code" ? null : "code"))}
                    onSelect={(value) => {
                      setBeyondDftCode(value);
                      setOpenDftPicker(null);
                    }}
                    onAsk={(item) => {
                      setOpenDftPicker(null);
                      setInput(`What should I know about ${item.label}? When is it a good choice?`);
                      inputRef.current?.focus();
                    }}
                    onRecommend={(item) => {
                      setOpenDftPicker(null);
                      setInput(`I just picked ${item.label} — what makes it a great choice, and what's it really good at?`);
                      inputRef.current?.focus();
                    }}
                  />
                  <WorkspacePicker
                    label="Machine"
                    value={beyondDftMachine}
                    option={beyondDftMachineMeta}
                    groups={DFT_HPC_GROUPS}
                    isOpen={openDftPicker === "machine"}
                    onToggle={() => setOpenDftPicker((current) => (current === "machine" ? null : "machine"))}
                    onSelect={(value) => {
                      setBeyondDftMachine(value);
                      setOpenDftPicker(null);
                    }}
                    onAsk={(item) => {
                      setOpenDftPicker(null);
                      setInput(`What should I know about ${item.label}? When is it a good choice?`);
                      inputRef.current?.focus();
                    }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      );
    }

    if (activeTool.id === "ml-analysis") {
    return (
      <div className="workspace-content">
        <div className="workspace-tool-header" style={{ "--tool-color": activeTool.color } as CSSPropertiesWithVars}>
          <div className="workspace-tool-header-top">
            <div className="workspace-tool-title">
              <ToolGlyph tool={activeTool} size={18} />
              <span>{activeTool.label}</span>
            </div>
            <button className="ghost-icon-btn" onClick={clearTool} title={t("dismiss_tool")}>
              <CloseIcon />
            </button>
          </div>
          <div className="workspace-powered-by">
            {t("powered_by")}{" "}
            <a href="https://stfc.github.io/janus-core/" target="_blank" rel="noreferrer">janus-core</a>
          </div>
        </div>
        {/* Model selector */}
        <div className="workspace-section">
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <div className="workspace-title" style={{ marginBottom: 0 }}>{t("mlip_model")}</div>
            <button
              className="workspace-section-recommend"
              onClick={() => {
                setInput("What are MLIPs and which model should I use? Give me a quick overview of MACE-MP-0, CHGNet, and ALIGNN, and when each one is a good choice.");
                inputRef.current?.focus();
              }}
              data-tooltip="Ask about MLIP models"
            >✦</button>
          </div>
          <SimpleSelect
            label="Model"
            value={selectedMlipModel.id}
            items={MLIP_MODELS.map((m) => ({ id: m.id, label: m.label }))}
            isOpen={openMlipPicker === "model"}
            onToggle={() => setOpenMlipPicker((p) => p === "model" ? null : "model")}
            onSelect={(id) => { setSelectedMlipModel(MLIP_MODELS.find((m) => m.id === id)); setOpenMlipPicker(null); }}
            onAsk={(item) => {
              setOpenMlipPicker(null);
              setInput(`Tell me about ${item.label} — its strengths, limitations, and what materials or calculations it's best suited for.`);
              inputRef.current?.focus();
            }}
          />
        </div>

        {/* Structure card */}
        <div className="workspace-section">
          <div className="workspace-title">Structure</div>
          {mlipCalcType !== "neb" ? (
            <SimpleSelect
              label="Structure"
              value={mlipStructIdx}
              items={chatStructures.length === 0
                ? [{ id: -1, label: "No structures loaded" }]
                : chatStructures.map((s, i) => ({ id: i, label: s.name }))
              }
              isOpen={openMlipPicker === "struct"}
              onToggle={() => setOpenMlipPicker((p) => p === "struct" ? null : "struct")}
              onSelect={(id) => { setMlipStructIdx(id); setOpenMlipPicker(null); }}
              disabled={chatStructures.length === 0}
            />
          ) : (
            <>
              <SimpleSelect
                label="Initial"
                value={mlipNebInitIdx}
                items={chatStructures.map((s, i) => ({ id: i, label: s.name }))}
                isOpen={openMlipPicker === "neb-init"}
                onToggle={() => setOpenMlipPicker((p) => p === "neb-init" ? null : "neb-init")}
                onSelect={(id) => { setMlipNebInitIdx(id); setOpenMlipPicker(null); }}
                disabled={chatStructures.length === 0}
              />
              <div style={{ marginTop: 4 }}>
                <SimpleSelect
                  label="Final"
                  value={mlipNebFinalIdx}
                  items={chatStructures.map((s, i) => ({ id: i, label: s.name }))}
                  isOpen={openMlipPicker === "neb-final"}
                  onToggle={() => setOpenMlipPicker((p) => p === "neb-final" ? null : "neb-final")}
                  onSelect={(id) => { setMlipNebFinalIdx(id); setOpenMlipPicker(null); }}
                  disabled={chatStructures.length === 0}
                />
              </div>
            </>
          )}
          <StructureUploadControl onFile={readFile} />
        </div>

        {/* Calculation launcher */}
        <div className="workspace-section">
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <div className="workspace-title" style={{ marginBottom: 0 }}>Calculation type</div>
            <button
              className="workspace-section-recommend"
              onClick={() => {
                const calcLabels = { singlepoint: "Singlepoint", geomopt: "Geometry Optimisation", phonons: "Phonons", eos: "Equation of State", neb: "Nudged Elastic Band" };
                setInput(`Can you explain the ${calcLabels[mlipCalcType]} calculation — what it computes, when to use it, and how to interpret the results?`);
                inputRef.current?.focus();
              }}
              data-tooltip="Ask about this calculation type"
            >✦</button>
          </div>
          <div className="mlip-calc-chips">
            {["singlepoint", "geomopt", "phonons", "eos", "neb"].map((ct) => (
              <button
                key={ct}
                className={`mlip-calc-chip${mlipCalcType === ct ? " active" : ""}`}
                onClick={() => setMlipCalcType(ct)}
              >
                {ct === "singlepoint" ? "Singlepoint" : ct === "geomopt" ? "Geom Opt" : ct === "phonons" ? "Phonons" : ct === "eos" ? "EoS" : "NEB"}
              </button>
            ))}
          </div>

          {mlipCalcType === "geomopt" && (
            <div className="workspace-form">
              <div className="workspace-form-field">
                <div className="workspace-form-field-label">Relax mode</div>
                <div className="mlip-calc-chips">
                  {[
                    { id: "ionic", label: "Atoms only" },
                    { id: "cell",  label: "Hydrostatic" },
                    { id: "full",  label: "Full relax" },
                  ].map((m) => (
                    <button key={m.id} className={`mlip-calc-chip${mlipRelaxMode === m.id ? " active" : ""}`} onClick={() => setMlipRelaxMode(m.id)}>{m.label}</button>
                  ))}
                </div>
                <div className="relax-mode-desc">
                  {{
                    ionic: "Relaxes atomic positions only — the unit cell shape and volume stay fixed.",
                    cell: "Relaxes the cell volume under isotropic (hydrostatic) pressure while also optimising atom positions.",
                    full: "Relaxes both atomic positions and the full cell tensor (lengths and angles). The most general option.",
                  }[mlipRelaxMode]}
                </div>
              </div>
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Force threshold (eV/Å)</span>
                <input type="number" step="0.01" value={mlipFmax} onChange={(e) => setMlipFmax(Number(e.target.value))} />
              </label>
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Max steps</span>
                <input type="number" step="100" value={mlipSteps} onChange={(e) => setMlipSteps(Number(e.target.value))} />
              </label>
            </div>
          )}
          {mlipCalcType === "phonons" && (
            <div className="workspace-form">
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Supercell</span>
                <input type="number" step="1" min="1" value={mlipSupercell} onChange={(e) => setMlipSupercell(Number(e.target.value))} />
              </label>
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Displacement (Å)</span>
                <input type="number" step="0.005" value={mlipDisplacement} onChange={(e) => setMlipDisplacement(Number(e.target.value))} />
              </label>
            </div>
          )}
          {mlipCalcType === "eos" && (
            <div className="workspace-form">
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Min volume</span>
                <input type="number" step="0.01" value={mlipMinVol} onChange={(e) => setMlipMinVol(Number(e.target.value))} />
              </label>
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Max volume</span>
                <input type="number" step="0.01" value={mlipMaxVol} onChange={(e) => setMlipMaxVol(Number(e.target.value))} />
              </label>
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">N volumes</span>
                <input type="number" step="1" min="3" value={mlipNVolumes} onChange={(e) => setMlipNVolumes(Number(e.target.value))} />
              </label>
            </div>
          )}
          {mlipCalcType === "neb" && (
            <div className="workspace-form">
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">N images</span>
                <input type="number" step="1" min="3" value={mlipNImages} onChange={(e) => setMlipNImages(Number(e.target.value))} />
              </label>
              <label className="workspace-form-field">
                <span className="workspace-form-field-label">Force threshold (eV/Å)</span>
                <input type="number" step="0.01" value={mlipNebFmax} onChange={(e) => setMlipNebFmax(Number(e.target.value))} />
              </label>
            </div>
          )}

          <button
            className={`mlip-run-btn${mlipCalcLoading ? " loading" : ""}`}
            disabled={mlipCalcLoading || (mlipCalcType !== "neb" && chatStructures.length === 0) || (mlipCalcType === "neb" && chatStructures.length < 2)}
            onClick={handleRunMlipCalc}
          >
            {mlipCalcLoading ? "Running…" : "Run calculation"}
          </button>
        </div>

        {/* Results list */}
        {mlipResultsList.length > 0 && (
          <div className="workspace-section">
            <div className="workspace-title">Results</div>
            <div className="mlip-result-list">
              {mlipResultsList.map((result) => (
                <div key={result.id} className={`mlip-result-card${result.error ? " error" : ""}`}>
                  <div className="mlip-result-header">
                    <div className="mlip-result-meta">
                      <span className="mlip-result-type">
                        {result.type === "singlepoint" ? "Singlepoint" : result.type === "geomopt" ? "Geom Opt" : result.type === "phonons" ? "Phonons" : result.type === "eos" ? "EoS" : "NEB"}
                      </span>
                      <span className="mlip-result-struct">{result.structureName}</span>
                    </div>
                    <button className="mlip-dismiss-btn" onClick={() => handleDismissMlipResult(result.id)} title="Dismiss">×</button>
                  </div>

                  {result.error ? (
                    <div className="mlip-result-error">{result.error}</div>
                  ) : (
                    <>
                      {/* Singlepoint */}
                      {result.type === "singlepoint" && result.raw && (
                        <div className="mlip-sp-rows">
                          <div className="mlip-sp-row">
                            <span className="mlip-sp-label">Energy</span>
                            <span className="mlip-sp-value">{result.raw.energy != null ? `${result.raw.energy.toFixed(4)} eV` : "—"}</span>
                          </div>
                        </div>
                      )}

                      {/* Geom Opt */}
                      {result.type === "geomopt" && result.raw && (
                        <div className="mlip-sp-rows">
                          <div className="mlip-sp-row">
                            <span className="mlip-sp-label">Final energy</span>
                            <span className="mlip-sp-value">{result.raw.final_energy != null ? `${result.raw.final_energy.toFixed(4)} eV` : "—"}</span>
                          </div>
                          <div className="mlip-sp-row">
                            <span className="mlip-sp-label">Max force</span>
                            <span className="mlip-sp-value">{result.raw.max_force != null ? `${result.raw.max_force.toFixed(4)} eV/Å` : "—"}</span>
                          </div>
                        </div>
                      )}

                      {/* Phonons — dispersion + thermal */}
                      {result.type === "phonons" && result.raw && (
                        <>
                          {result.raw.band_svg && (
                            <div style={{ background: "white", borderRadius: 6, padding: 4, margin: "8px 0", overflow: "hidden" }}>
                              <img
                                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.raw.band_svg)}`}
                                style={{ width: "100%", height: "auto", display: "block" }}
                                alt="Phonon dispersion"
                              />
                            </div>
                          )}
                          {(() => {
                            const temps = result.raw.temperatures || [];
                            if (temps.length === 0) return null;
                            const idx = temps.reduce((best, t, i) => Math.abs(t - 300) < Math.abs(temps[best] - 300) ? i : best, 0);
                            const cv = result.raw.heat_capacity?.[idx];
                            const s = result.raw.entropy?.[idx];
                            return (
                              <div className="mlip-sp-rows">
                                {cv != null && <div className="mlip-sp-row"><span className="mlip-sp-label">Cv (300 K)</span><span className="mlip-sp-value">{cv.toFixed(2)} J/mol·K</span></div>}
                                {s != null && <div className="mlip-sp-row"><span className="mlip-sp-label">Entropy (300 K)</span><span className="mlip-sp-value">{s.toFixed(2)} J/mol·K</span></div>}
                              </div>
                            );
                          })()}
                        </>
                      )}

                      {/* EoS — E-V plot placeholder */}
                      {result.type === "eos" && result.raw && (
                        <>
                          <div className="mlip-sp-rows">
                            <div className="mlip-sp-row"><span className="mlip-sp-label">Bulk modulus</span><span className="mlip-sp-value">{result.raw.bulk_modulus != null ? `${result.raw.bulk_modulus.toFixed(1)} GPa` : "—"}</span></div>
                            <div className="mlip-sp-row"><span className="mlip-sp-label">V₀</span><span className="mlip-sp-value">{result.raw.v_0 != null ? `${result.raw.v_0.toFixed(3)} Å³` : "—"}</span></div>
                            <div className="mlip-sp-row"><span className="mlip-sp-label">E₀</span><span className="mlip-sp-value">{result.raw.e_0 != null ? `${result.raw.e_0.toFixed(4)} eV` : "—"}</span></div>
                          </div>
                          {result.raw.eos_svg && (
                            <div style={{ background: "white", borderRadius: 6, padding: 4, margin: "8px 0", overflow: "hidden" }}>
                              <img
                                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.raw.eos_svg)}`}
                                style={{ width: "100%", height: "auto", display: "block" }}
                                alt="E-V curve"
                              />
                            </div>
                          )}
                        </>
                      )}

                      {/* NEB — energy profile + WEAS trajectory placeholder */}
                      {result.type === "neb" && result.raw && (
                        <>
                          <div className="mlip-sp-rows">
                            <div className="mlip-sp-row"><span className="mlip-sp-label">Barrier</span><span className="mlip-sp-value">{result.raw.barrier != null ? `${result.raw.barrier.toFixed(3)} eV` : "—"}</span></div>
                            <div className="mlip-sp-row"><span className="mlip-sp-label">ΔE</span><span className="mlip-sp-value">{result.raw.delta_e != null ? `${result.raw.delta_e.toFixed(3)} eV` : "—"}</span></div>
                          </div>
                          {result.raw.neb_svg && (
                            <div style={{ background: "white", borderRadius: 6, padding: 4, margin: "8px 0", overflow: "hidden" }}>
                              <img
                                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.raw.neb_svg)}`}
                                style={{ width: "100%", height: "auto", display: "block" }}
                                alt="NEB energy profile"
                              />
                            </div>
                          )}
                        </>
                      )}

                      <div className="mlip-result-actions" style={{ justifyContent: "space-between" }}>
                        {result.type === "geomopt" && result.raw?.optimised_structure
                          ? (
                            <span style={{ fontSize: 11, color: "var(--subtle, #64748b)", alignSelf: "center" }}>
                              {`geo-opt-${(result.structureName ?? "structure").replace(/\.[^.]+$/, "")}.cif`}
                            </span>
                          )
                          : <span />
                        }
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          {result.type === "geomopt" && result.raw?.optimised_structure && (
                            <button className="mlip-import-btn" onClick={() => handleImportMlipStructure(result.raw, result.structureName)}>
                              Import
                            </button>
                          )}
                          {result.type === "eos" && result.raw?.volumes?.length > 0 && (
                            <button className="mlip-import-btn" onClick={() => {
                              const stem = (result.structureName ?? "structure").replace(/\.[^.]+$/, "");
                              const rows = ["volume_A3,energy_eV",
                                ...result.raw.volumes.map((v, i) => `${v.toFixed(6)},${result.raw.energies[i].toFixed(6)}`)
                              ].join("\n");
                              const url = URL.createObjectURL(new Blob([rows], { type: "text/csv" }));
                              const a = document.createElement("a");
                              a.href = url; a.download = `${stem}-eos.csv`; a.click();
                              URL.revokeObjectURL(url);
                            }}>
                              ↓ Download
                            </button>
                          )}
                          {result.type === "neb" && result.raw?.neb_traj && (
                            <button className="mlip-import-btn" onClick={() => {
                              const stem = (result.structureName ?? "neb").replace(/\.[^.]+$/, "");
                              const url = URL.createObjectURL(new Blob([result.raw.neb_traj], { type: "text/plain" }));
                              const a = document.createElement("a");
                              a.href = url; a.download = `${stem}-neb.extxyz`; a.click();
                              URL.revokeObjectURL(url);
                            }}>
                              ↓ Download
                            </button>
                          )}
                          {result.type === "phonons" && result.raw && (
                            <span style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
                              {(result.raw.band_svg || result.raw.band_yaml) && (
                                <button className="mlip-import-btn" onClick={() => {
                                  const stem = (result.structureName ?? "structure").replace(/\.[^.]+$/, "");
                                  const dl = (content, type, name) => {
                                    const url = URL.createObjectURL(new Blob([content], { type }));
                                    const a = document.createElement("a");
                                    a.href = url; a.download = name; a.click();
                                    URL.revokeObjectURL(url);
                                  };
                                  if (result.raw.band_svg) dl(result.raw.band_svg, "image/svg+xml", `${stem}-bands.svg`);
                                  if (result.raw.band_yaml) dl(result.raw.band_yaml, "application/x-yaml", `${stem}-bands.yaml`);
                                }}>
                                  ↓ Download
                                </button>
                              )}
                              {result.raw.band_yaml && (
                                <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                                  <button className="mlip-import-btn" onClick={() => {
                                    // Vendored locally (app/public/phonon/, same tool as
                                    // henriquemiranda.github.io/phononwebsite) so this works
                                    // offline and doesn't depend on a third-party site staying up.
                                    // phonon.html already has a load listener (added when it was
                                    // vendored) that auto-loads this sessionStorage key into its
                                    // own file input and fires the same "change" handler a real
                                    // "Choose File" pick would -- window.open()'s new tab is a
                                    // same-origin auxiliary browsing context, so it inherits
                                    // whatever's in sessionStorage at the moment it's opened.
                                    sessionStorage.setItem("phonon_band_yaml", result.raw.band_yaml);
                                    window.open("/phonon/phonon.html", "_blank");
                                  }}>
                                    Phonon visualizer ↗
                                  </button>
                                  <span style={{ fontSize: "0.68rem", color: "var(--muted)", lineHeight: 1.3 }}>
                                    Opens with this band structure already loaded.
                                  </span>
                                </span>
                              )}
                            </span>
                          )}
                          {result.summary && (
                            <button className="mlip-discuss-btn" data-tooltip="Discuss with Goldilocks" onClick={() => handleDiscussMlipResult(result)}>
                              ✦
                            </button>
                          )}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
    }

    if (activeTool.id === "post-analysis") {
      const phononResultsInChat = mlipResultsList.filter((r) => r.type === "phonons" && r.raw?.band_yaml);
      async function handlePostAnalysisPhononFile(file) {
        try {
          const text = await file.text();
          sessionStorage.setItem("phonon_band_yaml", text);
          window.open("/phonon/phonon.html", "_blank");
        } catch (err) {
          console.error("Failed to read phonon band file.", err);
          window.alert("Couldn't read that file. Please try another one.");
        }
      }
      function openPhononYaml(bandYaml) {
        sessionStorage.setItem("phonon_band_yaml", bandYaml);
        window.open("/phonon/phonon.html", "_blank");
      }
      return (
        <div className="workspace-content">
          <div className="workspace-tool-header" style={{ "--tool-color": activeTool.color } as CSSPropertiesWithVars}>
            <div className="workspace-tool-header-top">
              <div className="workspace-tool-title">
                <ToolGlyph tool={activeTool} size={18} />
                <span>{activeTool.label}</span>
              </div>
              <button className="ghost-icon-btn" onClick={clearTool} title={t("dismiss_tool")}>
                <CloseIcon />
              </button>
            </div>
            <div className="workspace-no-backing" title="Parsing/plotting general DFT and MLIP output files (pw.out, OUTCAR, ...) isn't built yet -- only the phonon visualizer below is real.">
              <WarningIcon />
              <span>Only phonon visualization is real so far</span>
            </div>
          </div>
          <div className="workspace-stack">
            <div className="workspace-section">
              <div className="workspace-title">Phonon visualizer</div>
              <div className="workspace-hint">
                Opens the same band-structure viewer MLIP Playground's Phonons
                calculation links to, pre-loaded with a phonopy{" "}
                <code>band.yaml</code> -- from this chat, or from anywhere
                else (it doesn't have to come from a calculation run here).
              </div>
              {phononResultsInChat.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
                  {phononResultsInChat.map((r, i) => (
                    <button key={i} className="structure-upload-btn" style={{ width: "100%" }} onClick={() => openPhononYaml(r.raw.band_yaml)}>
                      ↗ {r.structureName ?? "structure"}'s phonon result
                    </button>
                  ))}
                </div>
              )}
              <StructureUploadControl
                onFile={handlePostAnalysisPhononFile}
                accept=".yaml,.yml"
                label="Upload band.yaml"
                hint="or drag a phonopy band.yaml here"
              />
            </div>
          </div>
        </div>
      );
    }

    // Tools with an entry in the picker but no dedicated panel built yet
    // (aiida — 2026-09-15: added as a future-required tool, see
    // docs/goldilocks-agent-implementation-plan.md §五).
    return (
      <div className="workspace-content">
        <div className="workspace-tool-header" style={{ "--tool-color": activeTool.color } as CSSPropertiesWithVars}>
          <div className="workspace-tool-header-top">
            <div className="workspace-tool-title">
              <ToolGlyph tool={activeTool} size={18} />
              <span>{activeTool.label}</span>
            </div>
            <button className="ghost-icon-btn" onClick={clearTool} title={t("dismiss_tool")}>
              <CloseIcon />
            </button>
          </div>
        </div>
        <div className="workspace-section">
          <div className="workspace-title">Coming soon</div>
          <p style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>{activeTool.desc}</p>
        </div>
      </div>
    );
  }

  // Full-page "all tools" grid -- reachable from the header's new
  // expand-all-tools button (openToolsOverview). Each card's own expand
  // affordance (ExpandIcon) drills into that Tool's full detail page via
  // openToolFullPage, which is activateTool() plus flipping fullPageView to
  // "detail" -- so this is purely a second entry point onto the same Tool
  // state renderToolPicker()/activateTool() already manage for the inline
  // side panel, not a parallel copy of it.
  function renderToolsOverview() {
    return (
      <div className="tools-overview-grid">
        {TOOLS.map((tool) => (
          <button
            key={tool.id}
            type="button"
            className="tools-overview-card"
            style={{ "--tool-color": tool.color } as CSSPropertiesWithVars}
            onClick={() => openToolFullPage(tool)}
          >
            <div className="tools-overview-card-icon">
              <ToolGlyph tool={tool} size={26} />
            </div>
            <div className="tools-overview-card-copy">
              <strong>{tool.label}</strong>
              <span>{t("tool_" + tool.id.replace(/-/g, "_") + "_launcher") || tool.launcherDesc}</span>
            </div>
            <span className="tools-overview-expand-btn" title={`Expand ${tool.label}`}>
              <ExpandIcon />
            </span>
          </button>
        ))}
      </div>
    );
  }

  // Full-page detail body for whichever Tool is active. DFT Workbench is
  // special-cased to the real embedded goldilocks-core Workbench (the exact
  // MantineProvider/CoreWorkspaceProvider/CoreWorkbenchContent block that
  // used to live under the old top-level "Workbench" viewMode branch) --
  // every other Tool reuses `workspaceContent` (the same renderWorkspace()
  // output already rendered beside chat by the inline side panel) at full
  // width instead of designing a second copy of that content.
  function renderToolDetailPage() {
    if (!activeTool) return renderToolsOverview();
    if (activeTool.id === "dft-workbench") {
      return (
        <MantineProvider
          theme={workbenchTheme}
          colorSchemeManager={coreColorSchemeManager}
          defaultColorScheme="light"
        >
          <CoreWorkspaceProvider workspace={coreWorkspace}>
            <CoreWorkbenchContent />
          </CoreWorkspaceProvider>
        </MantineProvider>
      );
    }
    return <div className="tool-detail-content">{workspaceContent}</div>;
  }

  // The full-page takeover itself -- same "replace the whole app-row"
  // pattern the old viewMode === "workbench" branch used, just triggered by
  // fullPageView instead of a removed top-level Chat/Workbench toggle. Always
  // offers a breadcrumb back to chat (and, one level down, back to the
  // overview) so there's an obvious way out from anywhere in this drill-down.
  function renderToolsFullPage() {
    const isWorkbenchDetail = fullPageView === "detail" && activeTool?.id === "dft-workbench";
    return (
      <div className="app-row tools-fullpage">
        <div className="tools-fullpage-header">
          <button type="button" className="tools-fullpage-crumb-btn" onClick={closeFullPage}>
            ← Chat
          </button>
          <span className="tools-fullpage-crumb-sep">/</span>
          {fullPageView === "detail" ? (
            <button type="button" className="tools-fullpage-crumb-btn" onClick={backToToolsOverview}>
              All Tools
            </button>
          ) : (
            <span className="tools-fullpage-crumb-current">All Tools</span>
          )}
          {fullPageView === "detail" && activeTool && (
            <>
              <span className="tools-fullpage-crumb-sep">/</span>
              <span className="tools-fullpage-crumb-current">
                <ToolGlyph tool={activeTool} size={15} />
                {activeTool.label}
              </span>
            </>
          )}
        </div>
        <div className={`tools-fullpage-body${isWorkbenchDetail ? " workbench-embed" : ""}`}>
          {fullPageView === "overview" ? renderToolsOverview() : renderToolDetailPage()}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`app ${resolvedTheme}${resizingPane ? " pane-resizing" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body, #root { height: 100%; }
        body { font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
        /* Browsers don't inherit the page font into form controls by default.
           This codebase previously patched that per-button (a handful of
           spots already have their own font-family/font: inherit) instead of
           resetting it once -- that meant it was easy to miss on a new
           button (as happened with the new header controls) and silently
           fall back to the OS UI font. One global reset covers every button/
           input/textarea/select, present and future; class-level overrides
           (e.g. explicit monospace inputs) still win via specificity. */
        button, input, textarea, select { font-family: inherit; }

        .app {
          --bg: #0b0b0f;
          --bg-elev: #11111a;
          --bg-soft: #151523;
          --bg-wash: rgba(17, 17, 26, 0.8);
          --panel: rgba(15, 15, 24, 0.88);
          --sidebar: #09090d;
          --border: rgba(109, 98, 145, 0.18);
          --text: #eceaf7;
          --muted: #9b97b3;
          --subtle: #5f5a74;
          --accent: #2b7de0;
          --accent-soft: rgba(43, 125, 224, 0.16);
          --success: #22c55e;
          --shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
          /* Fixed brand header color -- matches goldilocks-web's nav bar exactly,
             deliberately NOT theme-dependent (unlike everything else here), so the
             three panel headers read as one consistent brand surface regardless of
             light/dark mode. Not redefined in .app.light -- CSS vars inherit. */
          --brand-header: #2e2d62;
          --brand-header-text: #ffffff;
          --brand-header-text-dim: rgba(255, 255, 255, 0.68);
          background:
            radial-gradient(circle at top right, rgba(43, 125, 224, 0.09), transparent 28%),
            radial-gradient(circle at bottom left, rgba(20, 184, 166, 0.07), transparent 24%),
            radial-gradient(circle at top left, rgba(239, 68, 68, 0.05), transparent 20%),
            var(--bg);
          color: var(--text);
          display: flex;
          flex-direction: column;
          height: 100vh;
          position: relative;
          overflow: hidden;
        }

        .app-row {
          flex: 1;
          display: flex;
          min-height: 0;
          overflow: hidden;
        }

        .top-header {
          position: relative;
          /* .top-header-center's own children (the pill, the theme button)
             are both position: absolute now, so it has no normal-flow
             content left to size the row by -- pin a height explicitly
             instead of leaving it to collapse to whatever .top-header-left/
             -right's content happens to be. */
          min-height: 64px;
          flex-shrink: 0;
          display: flex;
          align-items: stretch;
          background: var(--brand-header);
          color: var(--brand-header-text);
          border-bottom: 1px solid rgba(255, 255, 255, 0.12);
          z-index: 6;
        }

        .top-header-left,
        .top-header-right {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 0 14px;
          overflow: hidden;
          flex-shrink: 0;
          transition: width 0.2s ease;
        }

        .top-header-left {
          border-right: 1px solid rgba(255, 255, 255, 0.12);
        }

        .top-header-right {
          justify-content: space-between;
          border-left: 1px solid rgba(255, 255, 255, 0.12);
        }

        /* Groups the tools-panel toggle and the new expand-all-tools button
           together as a pair, so they read as two related controls instead
           of being pushed to opposite ends by .top-header-right's own
           space-between (which still applies between the "Tools" label and
           this whole group). */
        .top-header-right-actions {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .top-header-center {
          flex: 1;
          min-width: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 10px 14px;
          position: relative;
        }

        .top-header-theme-btn {
          position: absolute;
          right: 14px;
          top: 50%;
          transform: translateY(-50%);
          border: none;
          cursor: pointer;
          border-radius: 999px;
          padding: 6px 12px;
          font-family: inherit;
          font-size: 12px;
          white-space: nowrap;
          background: rgba(255, 255, 255, 0.12);
          color: var(--brand-header-text);
          transition: background 0.14s ease;
        }

        .top-header-theme-btn:hover {
          background: rgba(255, 255, 255, 0.22);
        }

        .top-header .brand-name,
        .top-header .brand-ink {
          color: var(--brand-header-text);
        }

        .top-header .brand-slogan {
          color: var(--brand-header-text-dim);
        }

        .top-header .icon-btn {
          color: var(--brand-header-text-dim);
        }

        .top-header .icon-btn:hover {
          background: rgba(255, 255, 255, 0.14);
          color: var(--brand-header-text);
        }

        .app.light {
          --bg: #f4f6f8;
          --bg-elev: #ffffff;
          --bg-soft: #eef1f5;
          --bg-wash: rgba(255, 255, 255, 0.92);
          --panel: rgba(255, 255, 255, 0.92);
          --sidebar: #edf0f4;
          --border: rgba(71, 85, 105, 0.16);
          --text: #1e293b;
          --muted: #55606e;
          --subtle: #8994a3;
          --accent: #1e40af;
          --accent-soft: rgba(30, 64, 175, 0.14);
          --success: #15803d;
          --shadow: 0 20px 60px rgba(30, 41, 59, 0.12);
          background:
            radial-gradient(circle at top right, rgba(30, 64, 175, 0.08), transparent 26%),
            radial-gradient(circle at bottom left, rgba(13, 148, 136, 0.06), transparent 22%),
            radial-gradient(circle at top left, rgba(220, 38, 38, 0.04), transparent 18%),
            var(--bg);
        }

        .drag-overlay {
          position: absolute;
          inset: 16px;
          border: 2px dashed var(--accent);
          border-radius: 24px;
          background: rgba(43, 125, 224, 0.08);
          display: flex;
          align-items: center;
          justify-content: center;
          pointer-events: none;
          z-index: 30;
        }

        .drag-overlay-inner {
          text-align: center;
          color: var(--accent);
        }

        .drag-overlay-emoji {
          font-size: 36px;
          margin-bottom: 8px;
        }

        .sidebar {
          background: linear-gradient(180deg, var(--sidebar), color-mix(in srgb, var(--sidebar), var(--bg-elev) 28%));
          border-right: 1px solid var(--border);
          display: flex;
          flex-direction: column;
          flex-shrink: 0;
          overflow: hidden;
          transition: width 0.2s ease, min-width 0.2s ease;
          position: relative;
          z-index: 5;
        }

        .sidebar.closed {
          border-right: none;
        }

        .sidebar-inner {
          display: flex;
          flex-direction: column;
          min-height: 100%;
        }


        .brand {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: nowrap;
          /* Without min-width: 0, a flex item's minimum size defaults to its
             content's intrinsic width -- with the nowrap text below, that's
             the full, untruncated label, which at a small dragged-down
             sidebar width pushed the collapse button in .top-header-left
             outside the visible box entirely instead of the text truncating. */
          min-width: 0;
          flex: 1 1 auto;
        }

        .brand-icon {
          width: 32px;
          height: 32px;
          min-width: 32px;
          display: grid;
          place-items: center;
          border-radius: 8px;
          overflow: hidden;
          flex-shrink: 0;
        }

        .brand-copy {
          display: flex;
          flex-direction: column;
          gap: 1px;
          min-width: 0;
        }

        .brand-name {
          font-weight: 700;
          font-size: 16px;
          line-height: 1.2;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .brand-ink {
          color: var(--accent);
        }

        .brand-slogan {
          font-size: 10px;
          color: var(--subtle);
          font-style: italic;
          letter-spacing: 0.01em;
          line-height: 1.2;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .logo-image {
          display: block;
          width: 100%;
          height: 100%;
          object-fit: contain;
        }

        .icon-btn,
        .ghost-icon-btn {
          border: none;
          background: transparent;
          color: var(--subtle);
          width: 30px;
          height: 30px;
          min-width: 30px;
          border-radius: 9px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          flex-shrink: 0;
          transition: all 0.14s ease;
        }

        .icon-btn:hover,
        .ghost-icon-btn:hover {
          background: var(--bg-soft);
          color: var(--text);
        }

        .sidebar-nav {
          padding: 6px 10px;
        }

        .sidebar-nav + .sidebar-nav {
          padding-top: 0;
        }

        .nav-row {
          width: 100%;
          border: none;
          background: transparent;
          color: var(--muted);
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 10px 12px;
          border-radius: 12px;
          cursor: pointer;
          font-size: 14px;
          text-align: left;
          transition: all 0.14s ease;
        }

        .nav-row:hover,
        .nav-row.active {
          background: var(--bg-soft);
          color: var(--text);
        }

        .sidebar-divider {
          height: 1px;
          background: var(--border);
          margin: 8px 12px;
        }

        .session-list {
          flex: 1;
          overflow-y: auto;
          padding: 2px 10px 14px;
        }

        .sidebar-project-create {
          margin: 0 4px 8px;
        }

        .sidebar-project-list {
          display: grid;
          gap: 4px;
          margin-bottom: 10px;
        }

        .project-group {
          display: grid;
          gap: 6px;
        }

        .project-sidebar-row {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .project-sidebar-main {
          width: 100%;
          text-align: left;
        }

        .project-sidebar-delete {
          flex-shrink: 0;
        }

        .see-more-btn {
          border: none;
          background: transparent;
          color: var(--muted);
          font-size: 13px;
          text-align: left;
          padding: 8px 12px;
          cursor: pointer;
          border-radius: 10px;
          margin: 2px 4px 0;
        }

        .see-more-btn:hover {
          background: var(--bg-soft);
          color: var(--text);
        }

        .project-scope-block {
          border: 1px solid var(--border);
          background: var(--panel);
          border-radius: 16px;
          padding: 12px;
          margin: 0 4px;
        }

        .project-children {
          margin: 0 0 8px 14px;
          padding-left: 10px;
          border-left: 1px solid var(--border);
          display: grid;
          gap: 8px;
        }

        .project-scope-head {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 10px;
        }

        .project-scope-name {
          font-size: 13px;
          font-weight: 700;
          color: var(--text);
          margin-bottom: 4px;
        }

        .project-scope-desc {
          font-size: 11px;
          line-height: 1.5;
          color: var(--muted);
        }

        .project-inline-sources,
        .project-welcome-sources {
          display: flex;
          flex-wrap: wrap;
          justify-content: center;
          gap: 8px;
          margin-top: 12px;
        }

        .project-inline-source {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          border-radius: 999px;
          padding: 7px 10px;
          font-size: 11px;
          color: var(--text);
        }

        .project-new-chat-btn {
          margin-top: 12px;
          width: 100%;
        }

        .sidebar-project-chats {
          display: grid;
          gap: 4px;
          margin: 0 0 10px;
        }

        .group-label {
          font-size: 10px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--subtle);
          padding: 12px 10px 6px;
          font-weight: 700;
        }

        .session-item {
          display: flex;
          gap: 8px;
          align-items: center;
          border-radius: 12px;
          padding: 8px 10px;
          cursor: pointer;
          margin-bottom: 4px;
          transition: all 0.14s ease;
          border: 1px solid var(--border);
          background: color-mix(in srgb, var(--bg-elev) 55%, transparent);
        }

        .session-item:hover,
        .session-item.active {
          background: var(--bg-soft);
          border-color: color-mix(in srgb, var(--border) 180%, transparent);
        }

        .session-copy {
          flex: 1;
          min-width: 0;
          width: 0;
          overflow: hidden;
        }

        .session-title {
          font-size: 12.5px;
          color: var(--text);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .session-meta {
          font-size: 10px;
          color: var(--subtle);
          margin-top: 2px;
        }

        .tool-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
          margin-top: 2px;
        }

        .sidebar-footer {
          border-top: 1px solid var(--border);
          padding: 12px;
          position: relative;
        }

        .settings-row {
          width: 100%;
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 14px;
          padding: 10px 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          font-size: 13px;
          transition: all 0.14s ease;
        }

        .settings-row:hover {
          background: var(--bg-soft);
        }

        .settings-copy strong {
          display: block;
          font-size: 13px;
        }

        .settings-copy span {
          font-size: 11px;
          color: var(--muted);
        }

        .footer-note {
          margin-top: 10px;
          font-size: 10px;
          line-height: 1.5;
          color: var(--subtle);
        }

        .footer-note a {
          color: var(--muted);
          font-weight: 600;
          text-decoration: none;
          cursor: pointer;
        }

        .footer-note a:hover {
          color: var(--text);
          text-decoration: underline;
        }

        .main {
          flex: 1;
          display: flex;
          min-width: 0;
          min-height: 0;
          overflow: hidden;
        }

        .content {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-width: 0;
          min-height: 0;
          overflow: hidden;
          position: relative;
        }

        .tool-chip,
        .theme-chip,
        .status-chip {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          border-radius: 999px;
          padding: 5px 10px;
          font-size: 11px;
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--muted);
        }

        .theme-chip {
          cursor: pointer;
          transition: background 0.14s ease, color 0.14s ease;
        }

        .theme-chip:hover {
          background: var(--bg-soft);
          color: var(--text);
        }

        .tool-chip {
          border-color: color-mix(in srgb, var(--tool-color, var(--accent)) 24%, var(--border));
          background: color-mix(in srgb, var(--tool-color, var(--accent)) 10%, var(--bg-elev));
          color: var(--text);
        }

        .lang-chip-row {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 4px;
        }
        .lang-chip {
          padding: 6px 14px;
          border-radius: 999px;
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--muted);
          font-size: 13px;
          cursor: pointer;
          transition: background 0.14s, color 0.14s, border-color 0.14s;
        }
        .lang-chip:hover { background: var(--bg-soft); color: var(--text); }
        .lang-chip.active {
          border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
          background: color-mix(in srgb, var(--accent) 12%, var(--bg-elev));
          color: var(--text);
          font-weight: 600;
        }

        .body {
          flex: 1;
          display: flex;
          min-height: 0;
          overflow: hidden;
        }

        .chat-shell {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-width: 0;
          min-height: 0;
        }

        /* Full-page tools takeover (renderToolsFullPage): the "all tools"
           overview grid and each Tool's own full-page detail page, both
           replacing the whole sidebar+main+Tools-panel row the same way
           the old top-level Workbench viewMode used to. */
        .tools-fullpage {
          flex-direction: column;
          overflow: hidden;
          background: var(--bg);
        }

        .tools-fullpage-header {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 14px 24px;
          border-bottom: 1px solid var(--border);
          flex-shrink: 0;
        }

        .tools-fullpage-crumb-btn {
          border: none;
          background: transparent;
          color: var(--muted);
          font-family: inherit;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          padding: 5px 9px;
          border-radius: 8px;
          display: inline-flex;
          align-items: center;
          gap: 4px;
          transition: all 0.14s ease;
        }

        .tools-fullpage-crumb-btn:hover {
          background: var(--bg-soft);
          color: var(--text);
        }

        .tools-fullpage-crumb-sep {
          color: var(--subtle);
          font-size: 13px;
        }

        .tools-fullpage-crumb-current {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-size: 13px;
          font-weight: 700;
          color: var(--text);
          padding: 5px 9px;
        }

        .tools-fullpage-body {
          flex: 1;
          min-height: 0;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
        }

        .tools-overview-grid {
          width: min(1080px, calc(100% - 64px));
          margin: 0 auto;
          padding: 32px 0 48px;
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
          gap: 16px;
        }

        .tools-overview-card {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          border-radius: 16px;
          padding: 20px;
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 12px;
          text-align: left;
          cursor: pointer;
          font-family: inherit;
          color: inherit;
          position: relative;
          transition: all 0.14s ease;
        }

        .tools-overview-card:hover {
          background: var(--bg-soft);
          border-color: color-mix(in srgb, var(--tool-color, var(--accent)) 40%, var(--border));
          transform: translateY(-1px);
        }

        .tools-overview-card-icon {
          width: 44px;
          height: 44px;
          border-radius: 14px;
          display: grid;
          place-items: center;
          background: color-mix(in srgb, var(--tool-color, var(--accent)) 14%, var(--bg-elev));
          color: var(--tool-color, var(--accent));
        }

        .tools-overview-card-copy strong {
          display: block;
          font-size: 15px;
          margin-bottom: 4px;
        }

        .tools-overview-card-copy span {
          display: block;
          font-size: 12px;
          color: var(--muted);
          line-height: 1.5;
        }

        .tools-overview-expand-btn {
          position: absolute;
          top: 16px;
          right: 16px;
          width: 26px;
          height: 26px;
          border-radius: 8px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: var(--subtle);
          background: var(--bg-soft);
          transition: color 0.14s ease;
        }

        .tools-overview-card:hover .tools-overview-expand-btn {
          color: var(--tool-color, var(--accent));
        }

        /* Full-page detail body for the five Tools that reuse their inline
           side-panel content (renderToolDetailPage) -- same workspace-body
           padding as the side panel (see .workspace-body below) so the
           sticky .workspace-tool-header inside stays correctly positioned,
           just centered and wider instead of pinned to the aside's width.
           DFT Workbench's detail (the real embedded goldilocks-core
           Workbench) does NOT use this -- it renders full-bleed via
           .workbench-embed instead, see the JSX. */
        .tool-detail-content {
          width: 100%;
          max-width: 900px;
          margin: 0 auto;
          padding: 0 14px 14px;
          box-sizing: border-box;
        }

        .workbench-embed {
          /* .app-row is a row flex container for the chat layout's sidebar
             + main + tools; WorkbenchContent expects normal top-to-bottom
             document flow (status banner, then the grid), so override to a
             column here rather than letting each child stretch full-height
             as a row item. */
          flex-direction: column;
          overflow: auto;
          background: var(--bg);
        }

        /* Same @layer problem as the font-family fix below, but for the
           global universal-selector margin/padding reset at the top of this
           stylesheet: it's unlayered, so it beats every layered Mantine rule
           that sets spacing for its own components (Accordion rows, form
           field gaps, etc.), flattening them all to 0 and making Calculation's
           list rows look cramped compared to Structure/Generation, which
           don't lean on that spacing as much. revert (not unset -- margin/
           padding aren't inherited properties, so unset would just reapply
           the initial 0 value) hands the property back to the normal
           cascade, i.e. Mantine's own layered rules or the UA default. */
        .workbench-embed * {
          margin: revert;
          padding: revert;
        }

        /* goldilocks-workbench ships all of its CSS inside @layer mantine
           (see @mantine/core/styles.layer.css) so host apps can layer their
           own overrides on top -- but a *layered* rule always loses to an
           *unlayered* one of any specificity, so the plain reset above
           (also unlayered) was winning here regardless of its low
           specificity. This rule is unlayered too, just more specific, so
           it wins over that reset inside the embed and lets Mantine's own
           layered font-family rules apply as designed. */
        .workbench-embed button,
        .workbench-embed input,
        .workbench-embed textarea,
        .workbench-embed select {
          font-family: unset;
        }

        /* core/web's own .workbench-grid sizes itself as
           calc(100dvh - var(--app-header-height)), assuming its own
           sticky AppHeader sits above it (see goldilocks-core/web/src/App.css).
           WorkbenchContent renders without that header here, so that calc
           budgets space for a header that doesn't exist and the grid ends up
           taller than the actual remaining row height, forcing a scroll.
           .workbench-embed is already a flex column (see above) and
           .workbench-grid is its direct child, so sizing it via flex instead
           lets it fill exactly what's left after the status/failure banners
           -- fixed here rather than in core/web's own CSS since that file is
           shared with core's real standalone deployment, which does have the
           header the calc assumes. */
        .workbench-embed .workbench-grid {
          height: auto;
          flex: 1;
          min-height: 0;
          /* .workbench-grid's own max-width + margin-inline: auto is meant
             to center it once the row is wider than 100rem, but auto-margin
             centering on a flex item only kicks in when the item isn't
             being stretched to fill the cross axis -- align-items: stretch
             is the default here and it was winning, so the grid just filled
             the full row width up to max-width from the left instead of
             centering. align-self: center opts this item out of stretch
             explicitly instead of relying on margin: auto to imply it. */
          align-self: center;
          width: 100%;
        }

        .chat-area {
          flex: 1;
          overflow-y: auto;
        }

        .welcome,
        .projects-view {
          width: min(900px, calc(100% - 32px));
          margin: 0 auto;
        }

        .welcome {
          min-height: 100%;
          display: flex;
          flex-direction: column;
          justify-content: center;
          align-items: center;
          text-align: center;
          padding: 40px 0;
        }

        .welcome-brand {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 22px;
          margin-bottom: 40px;
          text-align: center;
        }

        .welcome-brand-copy {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .welcome-icon {
          width: 100px;
          height: 100px;
          min-width: 100px;
          display: grid;
          place-items: center;
          border-radius: 24px;
          overflow: hidden;
          flex-shrink: 0;
        }

        .welcome h1 {
          font-size: 44px;
          font-weight: 700;
          line-height: 1.15;
          margin-bottom: 0;
        }

        .welcome h1.brand-ink {
          font-size: 20px;
        }

        .welcome p {
          max-width: 700px;
          color: var(--muted);
          font-size: 15px;
          line-height: 1.8;
          margin-bottom: 24px;
        }

        .ghost-btn {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 16px;
          padding: 14px 16px;
          text-align: left;
          cursor: pointer;
          transition: all 0.14s ease;
          line-height: 1.5;
        }

        .ghost-btn:hover {
          background: var(--bg-soft);
          transform: translateY(-1px);
        }

        .messages {
          width: min(860px, calc(100% - 32px));
          margin: 0 auto;
          padding: 20px 0 24px;
        }

        .message-row {
          display: flex;
          gap: 12px;
          padding: 10px 0;
        }

        .avatar {
          width: 30px;
          height: 30px;
          border-radius: 10px;
          display: grid;
          place-items: center;
          flex-shrink: 0;
          color: white;
          margin-top: 2px;
        }

        .avatar.user {
          background: color-mix(in srgb, var(--bg-soft), black 5%);
          color: var(--muted);
        }

        .avatar.assistant {
          overflow: hidden;
        }

        .message-content {
          flex: 1;
          min-width: 0;
          color: var(--text);
          font-size: 14px;
          line-height: 1.8;
        }

        .user-bubble {
          display: inline-block;
          background: var(--bg-elev);
          border: 1px solid var(--border);
          border-radius: 16px 16px 6px 16px;
          padding: 10px 14px;
          max-width: 100%;
          color: var(--text);
        }

        .user-bubble-images {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 8px;
        }

        .user-bubble-image {
          width: 120px;
          height: 120px;
          object-fit: cover;
          border-radius: 10px;
          border: 1px solid var(--border);
        }

        .message-paragraph {
          margin-bottom: 6px;
          color: var(--text);
        }

        .md { line-height: 1.65; }
        .md p { margin: 0 0 8px; }
        .md p:last-child { margin-bottom: 0; }
        .md h1, .md h2, .md h3, .md h4 { font-weight: 700; margin: 16px 0 6px; line-height: 1.3; }
        .md h1 { font-size: 20px; }
        .md h2 { font-size: 17px; }
        .md h3 { font-size: 15px; }
        .md h4 { font-size: 14px; }
        .md ul, .md ol { padding-left: 20px; margin: 6px 0 10px; }
        .md li { margin-bottom: 4px; }
        .md li > ul, .md li > ol { margin: 4px 0; }
        .md code { background: color-mix(in srgb, var(--bg-soft), black 8%); border-radius: 5px; padding: 1px 5px; font-size: 13px; font-family: monospace; }
        .md pre { margin: 8px 0; border-radius: 14px; padding: 12px 14px; background: color-mix(in srgb, var(--bg-soft), black 8%); border: 1px solid var(--border); overflow-x: auto; }
        .md pre code { background: none; padding: 0; font-size: 13px; }
        .md hr { border: none; border-top: 1px solid var(--border); margin: 14px 0; }
        .md strong { font-weight: 700; }
        .md em { font-style: italic; }
        .md a { color: var(--accent); text-decoration: underline; }
        .md table { border-collapse: collapse; margin: 8px 0 10px; font-size: 13px; width: 100%; }
        .md th, .md td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
        .md th { background: color-mix(in srgb, var(--bg-soft), black 8%); font-weight: 700; }
        .md tr:nth-child(even) td { background: color-mix(in srgb, var(--bg-soft), transparent 60%); }

        .code-block {
          margin: 8px 0;
          border-radius: 14px;
          padding: 12px 14px;
          background: color-mix(in srgb, var(--bg-soft), black 8%);
          border: 1px solid var(--border);
          overflow-x: auto;
        }

        .code-lang {
          display: inline-block;
          font-size: 10px;
          letter-spacing: 0.12em;
          color: var(--accent);
          margin-bottom: 8px;
          font-weight: 700;
        }

        .code-block code,
        .workspace-code code {
          font-family: "JetBrains Mono", "Fira Code", monospace;
          font-size: 12px;
          color: color-mix(in srgb, var(--text), #70b8ff 14%);
          white-space: pre;
        }

        .typing {
          display: inline-flex;
          gap: 5px;
          align-items: center;
          padding: 8px 0;
        }

        .typing span {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--accent);
          animation: bounce 1.2s infinite ease-in-out;
        }

        .typing span:nth-child(2) { animation-delay: 0.16s; }
        .typing span:nth-child(3) { animation-delay: 0.32s; }

        @keyframes bounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
          40% { transform: translateY(-4px); opacity: 1; }
        }

        .tool-status {
          font-size: 0.85rem;
          color: var(--text-secondary);
          padding: 8px 0;
          font-style: italic;
        }

        .composer-wrap {
          padding: 10px 16px 16px;
          flex-shrink: 0;
        }

        .composer-outer {
          width: min(860px, 100%);
          margin: 0 auto;
        }

        .attachment-pill,
        .tool-banner,
        .element-chip {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 999px;
          padding: 8px 12px;
        }

        .tool-banner {
          width: 100%;
          justify-content: space-between;
          margin-bottom: 8px;
          border-radius: 18px;
          background: color-mix(in srgb, var(--tool-color, var(--accent)) 10%, var(--bg-elev));
        }

        .tool-banner-left {
          display: flex;
          gap: 10px;
          align-items: center;
          min-width: 0;
        }

        .tool-banner-copy strong {
          display: block;
          font-size: 13px;
        }

        .tool-banner-copy span {
          display: block;
          font-size: 11px;
          color: var(--muted);
        }

        .attachment-pills {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 8px;
        }

        .attachment-pill {
          margin-bottom: 0;
        }

        .attachment-thumb {
          width: 20px;
          height: 20px;
          object-fit: cover;
          border-radius: 5px;
        }

        .composer {
          border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
          background: var(--bg-wash);
          backdrop-filter: blur(18px);
          border-radius: 24px;
          padding: 10px;
          box-shadow: var(--shadow);
          position: relative;
        }

        .composer:focus-within {
          border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
          box-shadow: var(--shadow), 0 0 0 3px color-mix(in srgb, var(--accent) 35%, transparent);
        }

        .composer-input-row {
          display: flex;
          gap: 8px;
          align-items: flex-end;
        }

        .composer-controls {
          display: flex;
          gap: 8px;
          align-items: center;
          flex-shrink: 0;
        }

        .composer-btn {
          width: 40px;
          height: 40px;
          border-radius: 14px;
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--muted);
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          transition: all 0.14s ease;
        }

        .composer-btn:hover,
        .composer-btn.open {
          background: var(--bg-soft);
          color: var(--text);
        }

        .composer-text {
          flex: 1;
          min-width: 0;
          padding: 3px 0;
          display: flex;
          flex-direction: column;
        }

        .composer-text textarea {
          width: 100%;
          background: transparent;
          border: none;
          outline: none;
          resize: none;
          color: var(--text);
          font-size: 15px;
          line-height: 1.55;
          min-height: 40px;
          max-height: 140px;
          font-family: inherit;
        }

        .composer-text textarea::placeholder {
          color: var(--subtle);
        }

        .inline-widget-row {
          position: relative;
          display: flex;
          gap: 6px;
          margin-bottom: 6px;
          flex-wrap: wrap;
          align-items: center;
        }

        .inline-widget-btn {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--muted);
          border-radius: 10px;
          height: 28px;
          padding: 0 10px;
          font-size: 12px;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          white-space: nowrap;
        }

        .inline-widget-btn:hover,
        .inline-widget-btn.active {
          background: color-mix(in srgb, #8b5cf6 12%, var(--bg-elev));
          color: var(--text);
        }

        .plus-menu,
        .model-menu {
          position: absolute;
          bottom: calc(100% + 10px);
          background: var(--panel);
          backdrop-filter: blur(18px);
          border: 1px solid var(--border);
          border-radius: 18px;
          box-shadow: var(--shadow);
          z-index: 20;
        }

        .plus-menu {
          left: 0;
          width: 300px;
          padding: 8px;
        }

        .model-menu {
          right: 0;
          width: 260px;
          padding: 8px;
        }

        .menu-label {
          font-size: 10px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--subtle);
          padding: 8px 10px 6px;
          font-weight: 700;
        }

        .menu-item {
          border-radius: 14px;
          padding: 10px 12px;
          display: flex;
          gap: 10px;
          align-items: flex-start;
          cursor: pointer;
          transition: background 0.14s ease;
        }

        .menu-item:hover {
          background: var(--bg-soft);
        }

        .menu-item-icon {
          width: 32px;
          height: 32px;
          border-radius: 12px;
          display: grid;
          place-items: center;
          background: var(--bg-elev);
          flex-shrink: 0;
        }

        .tool-glyph {
          display: block;
          object-fit: contain;
          flex-shrink: 0;
        }

        .tool-glyph-fallback {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          line-height: 1;
          flex-shrink: 0;
        }

        .menu-item-copy strong {
          display: block;
          font-size: 13px;
        }

        .menu-item-copy span {
          display: block;
          margin-top: 2px;
          font-size: 11px;
          color: var(--muted);
          line-height: 1.45;
        }

        .menu-item-copy .model-ready {
          color: #10b981;
          font-weight: 600;
        }

        .menu-item-copy .model-coming-soon {
          color: var(--muted);
          font-style: italic;
        }

        .menu-item-disabled {
          cursor: default;
          opacity: 0.55;
        }

        .menu-item-disabled:hover {
          background: transparent;
        }

        .menu-item-ask {
          flex-shrink: 0;
          align-self: center;
          width: 24px;
          height: 24px;
          border-radius: 50%;
          border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
          background: color-mix(in srgb, var(--accent) 8%, var(--bg-elev));
          color: var(--accent);
          font-size: 11px;
          font-weight: 700;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: all 0.14s ease;
        }

        .menu-item-ask:hover {
          background: color-mix(in srgb, var(--accent) 18%, var(--bg-elev));
          border-color: var(--accent);
          transform: scale(1.1);
        }

        .menu-divider {
          height: 1px;
          background: var(--border);
          margin: 6px 8px;
        }

        .project-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          flex-shrink: 0;
          display: block;
        }

        .element-picker {
          position: absolute;
          left: 0;
          bottom: calc(100% + 10px);
          width: min(680px, calc(100vw - 72px));
          border: 1px solid var(--border);
          background: var(--panel);
          backdrop-filter: blur(18px);
          border-radius: 18px;
          padding: 12px;
          box-shadow: var(--shadow);
          z-index: 25;
        }

        .element-picker-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 6px;
        }

        .element-picker-grid {
          display: grid;
          grid-template-columns: repeat(18, minmax(0, 1fr));
          gap: 4px;
          min-width: 560px;
        }

        .element-cell,
        .element-empty {
          height: 32px;
          border-radius: 9px;
        }

        .element-empty {
          visibility: hidden;
        }

        .element-cell {
          border: 1px solid var(--border);
          background: transparent;
          color: var(--muted);
          font-size: 10.5px;
          cursor: pointer;
          transition: all 0.14s ease;
        }

        .element-cell.category-alkali { background: rgba(239, 68, 68, 0.10); }
        .element-cell.category-alkaline { background: rgba(249, 115, 22, 0.10); }
        .element-cell.category-transition { background: rgba(59, 130, 246, 0.10); }
        .element-cell.category-post { background: rgba(234, 179, 8, 0.10); }
        .element-cell.category-metalloid { background: rgba(20, 184, 166, 0.10); }
        .element-cell.category-nonmetal { background: rgba(34, 197, 94, 0.10); }
        .element-cell.category-halogen { background: rgba(168, 85, 247, 0.10); }
        .element-cell.category-noble { background: rgba(6, 182, 212, 0.10); }
        .element-cell.category-lanthanide { background: rgba(236, 72, 153, 0.10); }
        .element-cell.category-actinide { background: rgba(244, 114, 182, 0.12); }

        .element-cell {
          position: relative;
        }

        .element-cell:hover,
        .element-cell.selected {
          background: color-mix(in srgb, #8b5cf6 14%, var(--bg-elev));
          color: var(--text);
          border-color: rgba(139, 92, 246, 0.35);
        }

        .element-count {
          position: absolute;
          top: 1px;
          right: 2px;
          font-size: 8px;
          font-weight: 700;
          color: #8b5cf6;
          line-height: 1;
        }

        .element-formula-inset {
          display: flex;
          align-items: center;
          padding: 0 12px;
        }

        .element-formula-row {
          display: flex;
          align-items: center;
          gap: 5px;
          width: 100%;
        }

        .element-formula-block {
          flex: 1;
          min-width: 0;
          background: var(--bg);
          border: 1px solid var(--border);
          border-radius: 7px;
          padding: 3px 8px;
          font-family: ui-monospace, monospace;
          font-size: 12px;
          color: var(--text);
          margin: 0;
          height: 26px;
          display: flex;
          align-items: center;
          overflow: hidden;
        }

        .element-formula-insert {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 7px;
          padding: 0 9px;
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          flex-shrink: 0;
          height: 26px;
          display: flex;
          align-items: center;
        }

        .element-formula-insert:hover {
          background: var(--bg-soft);
        }

        .element-formula-search {
          border: 1px solid var(--accent);
          background: var(--accent-soft);
          color: var(--accent);
          border-radius: 7px;
          padding: 0 10px;
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          flex-shrink: 0;
          height: 26px;
          display: flex;
          align-items: center;
        }

        .element-formula-search:hover {
          background: color-mix(in srgb, var(--accent) 20%, transparent);
        }

        .element-picker-clear {
          background: none;
          border: none;
          color: var(--subtle);
          font-size: 12px;
          cursor: pointer;
          padding: 0 3px;
          flex-shrink: 0;
          height: 26px;
          display: flex;
          align-items: center;
        }

        .element-picker-clear:hover {
          color: var(--muted);
        }

        .element-picker-hint-inline {
          color: var(--subtle);
          font-size: 11px;
          text-align: center;
          width: 100%;
        }

        .inline-widget-btn.disabled {
          opacity: 0.45;
          cursor: default;
          pointer-events: none;
        }

        .structure-viewer {
          position: absolute;
          left: 0;
          bottom: calc(100% + 10px);
          width: min(560px, calc(100vw - 72px));
          border: 1px solid var(--border);
          background: var(--panel);
          backdrop-filter: blur(18px);
          border-radius: 18px;
          padding: 14px;
          box-shadow: var(--shadow);
          z-index: 25;
        }

        /* Make the dat.GUI controls panel scrollable when it overflows */
        .dg.main {
          overflow-y: auto !important;
          max-height: min(420px, 60vh) !important;
        }

        .structure-viewer-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 12px;
        }

        .structure-viewer-head-main {
          display: flex;
          align-items: center;
          gap: 10px;
          min-width: 0;
          flex-wrap: wrap;
        }

        .structure-viewer-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
        }

        .structure-viewer-body {
          display: grid;
          gap: 14px;
        }

        .structure-viewer-select {
          max-width: 260px;
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 8px;
          padding: 4px 8px;
          font-size: 12px;
        }

        .structure-viewer-row-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 4px;
        }

        .files-panel {
          position: absolute;
          left: 0;
          bottom: calc(100% + 10px);
          width: min(420px, calc(100vw - 72px));
          max-height: min(420px, 60vh);
          overflow: auto;
          border: 1px solid var(--border);
          background: var(--panel);
          backdrop-filter: blur(18px);
          border-radius: 18px;
          padding: 14px;
          box-shadow: var(--shadow);
          z-index: 25;
        }

        .files-panel-head {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 10px;
        }

        .files-panel-tabs {
          display: flex;
          gap: 6px;
          margin-bottom: 12px;
        }

        .files-tab-btn {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--muted);
          border-radius: 999px;
          padding: 6px 12px;
          font-size: 12px;
          font-weight: 600;
        }

        .files-tab-btn.active {
          color: var(--accent);
          border-color: var(--accent);
          background: color-mix(in srgb, var(--accent) 8%, transparent);
        }

        .files-list {
          display: grid;
          gap: 10px;
        }

        .files-row {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 12px;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: var(--bg-elev);
        }

        .files-row-thumb {
          width: 28px;
          height: 28px;
          object-fit: cover;
          border-radius: 6px;
          flex-shrink: 0;
        }

        .files-row-copy {
          min-width: 0;
          flex: 1;
        }

        .files-row-title {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
          min-width: 0;
        }

        .files-row-name {
          min-width: 0;
          font-size: 13px;
          font-weight: 600;
          color: var(--text);
          word-break: break-word;
        }

        .files-row-format {
          border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
          border-radius: 999px;
          padding: 2px 7px;
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--accent);
          background: color-mix(in srgb, var(--accent) 8%, var(--bg-soft));
        }

        .files-row-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
        }

        .secondary-btn.compact.active-insert {
          color: var(--accent);
          border-color: var(--accent);
          background: color-mix(in srgb, var(--accent) 8%, transparent);
        }

        .chat-files-note {
          margin-top: 10px;
          font-size: 11px;
          line-height: 1.6;
          color: var(--subtle);
        }

        .structure-svg-wrap {
          flex-shrink: 0;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-elev);
          overflow: hidden;
        }

        .structure-info {
          min-width: 0;
          display: grid;
          gap: 10px;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: var(--bg-elev);
        }

        .structure-info-section {
          background: var(--bg-elev);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 10px 12px;
        }

        .structure-legend {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }

        .structure-legend-item {
          display: flex;
          align-items: center;
          gap: 7px;
          font-size: 12px;
        }

        .structure-legend-dot {
          width: 12px;
          height: 12px;
          border-radius: 50%;
          border: 1px solid rgba(0,0,0,0.15);
          flex-shrink: 0;
        }

        .structure-legend-sym {
          font-weight: 600;
          min-width: 24px;
        }

        .structure-legend-cnt {
          color: var(--muted);
          font-size: 11px;
        }

        .structure-meta-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 12px;
          color: var(--muted);
          gap: 12px;
          padding: 3px 0;
        }

        .structure-meta-row span:last-child {
          color: var(--text);
          font-weight: 500;
          text-align: right;
          word-break: break-word;
        }

        .structure-empty {
          width: 100%;
          text-align: center;
          padding: 32px 16px;
          color: var(--muted);
          font-size: 13px;
        }

        .structure-empty-icon {
          font-size: 26px;
          margin-bottom: 10px;
          color: var(--subtle);
        }

        .viewer-attribution a {
          color: var(--muted);
          text-decoration: none;
          font-weight: 600;
        }

        .viewer-attribution a:hover {
          color: var(--text);
          text-decoration: underline;
        }

        .viewer-empty-state {
          border: 1px dashed var(--border);
          border-radius: 16px;
          padding: 28px 20px;
          text-align: center;
          margin-bottom: 12px;
        }

        .viewer-empty-icon {
          font-size: 22px;
          color: var(--subtle);
          margin-bottom: 8px;
        }

        .viewer-empty-label {
          font-size: 13px;
          font-weight: 600;
          color: var(--text);
          margin-bottom: 6px;
        }

        .viewer-empty-hint {
          font-size: 12px;
          color: var(--muted);
          line-height: 1.6;
          margin-bottom: 10px;
        }


        .viewer-attribution {
          font-size: 11px;
          color: var(--subtle);
          margin-top: 8px;
        }

        .structure-select {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 8px;
          padding: 3px 8px;
          font-size: 12px;
          cursor: pointer;
          outline: none;
        }

        .structure-select:focus-visible {
          border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
          box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 35%, transparent);
        }

        .model-selector {
          position: relative;
          flex-shrink: 0;
        }

        .model-btn {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--text);
          border-radius: 14px;
          padding: 0 12px;
          height: 40px;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          cursor: pointer;
          white-space: nowrap;
        }

        .model-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
        }

        .send-btn {
          width: 40px;
          height: 40px;
          border-radius: 14px;
          border: none;
          background: linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent), white 18%));
          color: white;
          cursor: pointer;
          transition: opacity 0.14s ease;
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }

        .send-btn:disabled {
          opacity: 0.3;
          cursor: not-allowed;
        }

        .stop-btn {
          background: color-mix(in srgb, var(--text) 12%, transparent);
          color: var(--text);
        }

        .stop-btn:hover {
          background: color-mix(in srgb, var(--text) 20%, transparent);
        }

        .composer-hint {
          text-align: center;
          color: var(--subtle);
          font-size: 10px;
          margin-top: 8px;
        }

        .inline-grid-hint {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 20px;
          height: 20px;
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
          background: color-mix(in srgb, var(--accent) 8%, var(--bg-elev));
          color: var(--accent);
          vertical-align: middle;
        }

        .inline-grid-hint svg {
          width: 11px;
          height: 11px;
        }

        .workspace {
          border-left: 1px solid var(--border);
          background: color-mix(in srgb, var(--bg), var(--bg-elev) 38%);
          overflow: hidden;
          flex-shrink: 0;
          transition: width 0.2s ease, min-width 0.2s ease;
          position: relative;
        }

        .workspace.closed {
          border-left: none;
        }

        .workspace-inner {
          display: flex;
          flex-direction: column;
          height: 100%;
          box-sizing: border-box;
        }


        .workspace-body {
          flex: 1;
          min-height: 0;
          overflow-y: auto;
          padding: 0 14px 14px;
          box-sizing: border-box;
        }

        .resize-handle {
          width: 6px;
          flex-shrink: 0;
          cursor: col-resize;
          background: transparent;
          position: relative;
          z-index: 6;
        }

        .resize-handle.handle-active {
          background: var(--accent-soft);
        }

        .sidebar.resizing,
        .workspace.resizing {
          transition: none;
        }

        .app.pane-resizing .top-header-left,
        .app.pane-resizing .top-header-right {
          transition: none;
        }

        .top-header .resize-handle.handle-active {
          background: rgba(255, 255, 255, 0.25);
        }

        .app.pane-resizing {
          cursor: col-resize;
          user-select: none;
        }

        .app.pane-resizing * {
          user-select: none;
        }


        .workspace-tool-header {
          margin-bottom: 10px;
          padding-bottom: 10px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.14);
          position: sticky;
          top: -14px;
          padding-top: 14px;
          margin-top: -14px;
          background: var(--brand-header);
          color: var(--brand-header-text);
          z-index: 3;
        }

        .workspace-tool-header-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 4px;
        }

        .workspace-tool-header .ghost-icon-btn {
          color: rgba(255, 255, 255, 0.7);
        }

        .workspace-tool-header .ghost-icon-btn:hover {
          background: rgba(255, 255, 255, 0.16);
          color: #ffffff;
        }

        .workspace-tool-title {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 13px;
          font-weight: 700;
          /* Mixed toward white (not var(--text)) so each Tool's own accent color
             still reads as a distinct hue against the now-fixed dark brand-header
             background, regardless of the app's light/dark theme. */
          color: color-mix(in srgb, var(--tool-color, var(--accent)) 55%, #ffffff);
        }

        .workspace-tabs {
          display: flex;
          gap: 8px;
          margin-bottom: 12px;
          flex-wrap: wrap;
        }

        .workspace-tab {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          color: var(--muted);
          border-radius: 999px;
          padding: 5px 10px;
          cursor: pointer;
          font-size: 13px;
        }

        .workspace-tab.active {
          background: var(--bg-soft);
          color: var(--text);
        }

        .workspace-stack {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }

        .workspace-section,
        .metric-card,
        .check-item {
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-elev);
          padding: 8px 10px;
        }

        .workspace-title {
          font-size: 12px;
          font-weight: 700;
          margin-bottom: 4px;
        }

        .workspace-copy,
        .project-desc {
          color: var(--muted);
          font-size: 13px;
          line-height: 1.7;
        }

        /* .workspace-hint had no rule at all before this -- it rendered as
           unstyled system-default text, clashing with the styled
           WorkspacePicker/SimpleSelect controls next to it in every Tool
           panel that uses it (not just DFT Workbench). */
        .workspace-hint {
          font-size: 12px;
          line-height: 1.6;
          color: var(--muted);
        }

        .workspace-settings-group-label {
          font-size: 11px;
          font-weight: 700;
          color: var(--subtle);
          text-transform: uppercase;
          letter-spacing: 0.03em;
          margin: 10px 0 4px;
        }

        .workspace-settings-row {
          display: flex;
          flex-direction: column;
          gap: 4px;
          padding: 8px 0;
          border-bottom: 1px solid var(--border);
        }
        .workspace-settings-row:last-child { border-bottom: none; }

        .workspace-settings-row-label {
          font-size: 12px;
          font-weight: 600;
          color: var(--text);
        }

        .workspace-settings-row-control {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .workspace-settings-row-control > .workspace-override-input {
          flex: 1;
          min-width: 0;
        }

        /* Same look as .struct-select (custom chevron, rounded, themed) --
           a distinct class rather than reusing .struct-select directly
           since these live in tighter rows, not a full-width form field. */
        .workspace-override-input {
          border: 1px solid var(--border);
          border-radius: 10px;
          background: var(--bg-soft);
          color: var(--text);
          padding: 7px 10px;
          font: inherit;
          font-size: 12px;
        }
        select.workspace-override-input {
          appearance: none;
          -webkit-appearance: none;
          cursor: pointer;
          padding-right: 26px;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2 4l4 4 4-4' stroke='%239b97b3' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
          background-repeat: no-repeat;
          background-position: right 8px center;
          background-size: 10px;
        }
        select.workspace-override-input:hover { border-color: color-mix(in srgb, var(--accent) 24%, var(--border)); }

        .workspace-source-badge {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 1px 8px;
          font-size: 11px;
          font-weight: 600;
          background: color-mix(in srgb, var(--accent) 14%, var(--bg-soft));
          color: var(--accent);
        }
        .workspace-source-badge.error {
          background: color-mix(in srgb, #ef4444 16%, var(--bg-soft));
          color: #ef4444;
        }

        /* db-query */
        .db-query-toggle {
          display: flex;
          gap: 4px;
          margin-bottom: 2px;
        }

        .db-query-toggle-btn {
          flex: 1;
          padding: 4px 0;
          border-radius: 8px;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--muted);
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.14s ease;
        }

        .db-query-toggle-btn.active {
          background: color-mix(in srgb, var(--accent) 12%, var(--bg-soft));
          border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
          color: var(--accent);
        }
        .db-formula-input {
          flex: 1;
          background: none;
          border: none;
          outline: none;
          color: var(--text);
          font-size: 13px;
          font-weight: 500;
          min-width: 0;
          padding: 0;
        }
        .db-formula-input::placeholder { color: var(--subtle); font-weight: 400; }
        .db-struct-nav {
          display: flex;
          align-items: center;
          gap: 2px;
          flex-shrink: 0;
        }
        .db-struct-nav-label {
          font-size: 11px;
          color: var(--muted);
          min-width: 28px;
          text-align: center;
        }

        .db-property-section {
          margin-top: 12px;
          margin-bottom: 10px;
        }

        .db-property-label {
          font-size: 12px;
          font-weight: 700;
          color: var(--text);
          margin-bottom: 8px;
        }

        .db-property-chips {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .db-property-chip {
          padding: 5px 12px;
          border-radius: 999px;
          border: 1px solid var(--border);
          background: var(--bg-soft);
          color: var(--muted);
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.14s ease;
        }

        .db-property-chip:hover {
          border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
          color: var(--text);
        }

        .db-property-chip.active {
          border-color: color-mix(in srgb, var(--accent) 50%, var(--border));
          background: color-mix(in srgb, var(--accent) 12%, var(--bg-soft));
          color: var(--accent);
          font-weight: 600;
        }

        /* db-group */
        .db-group { margin-bottom: 4px; }
        .db-group-header {
          width: 100%;
          display: flex;
          align-items: center;
          gap: 6px;
          background: none;
          border: none;
          padding: 5px 2px;
          cursor: pointer;
          color: var(--text);
          border-radius: 6px;
          transition: background 0.12s;
        }
        .db-group-header:hover { background: var(--bg-soft); }
        .db-group-caret {
          font-size: 13px;
          color: var(--muted);
          display: inline-block;
          transition: transform 0.15s;
          transform: rotate(0deg);
          line-height: 1;
          flex-shrink: 0;
        }
        .db-group-caret.open { transform: rotate(90deg); }
        .db-group-name {
          font-size: 12px;
          font-weight: 700;
          flex: 1;
          text-align: left;
        }
        .db-group-count {
          font-size: 11px;
          color: var(--muted);
          background: var(--bg-soft);
          border: 1px solid var(--border);
          border-radius: 999px;
          padding: 1px 7px;
          flex-shrink: 0;
        }
        .db-group-results {
          border-left: 1px solid var(--border);
          margin-left: 8px;
          padding-left: 10px;
          margin-bottom: 4px;
        }
        .db-result-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 5px 0;
          border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
        }
        .db-result-row:last-child { border-bottom: none; }
        .db-result-left {
          flex: 1;
          min-width: 0;
          display: flex;
          align-items: baseline;
          gap: 6px;
          overflow: hidden;
        }
        .db-result-formula {
          font-size: 13px;
          font-weight: 700;
          white-space: nowrap;
        }
        .db-result-sg {
          font-size: 11px;
          color: var(--muted);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .db-result-link {
          font-size: 11px;
          font-weight: 600;
          color: var(--accent);
          text-decoration: none;
          white-space: nowrap;
          flex-shrink: 0;
          opacity: 0.8;
          transition: opacity 0.12s;
        }
        .db-result-link:hover { opacity: 1; text-decoration: underline; }
        .db-result-row.matched { background: color-mix(in srgb, var(--accent) 5%, transparent); border-radius: 4px; padding: 5px 4px; }
        .db-result-match-badge {
          font-size: 10px;
          font-weight: 600;
          color: var(--accent);
          white-space: nowrap;
          flex-shrink: 0;
        }
        .db-import-btn {
          font-size: 11px;
          font-weight: 600;
          color: var(--accent);
          background: none;
          border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
          border-radius: 4px;
          padding: 1px 6px;
          cursor: pointer;
          white-space: nowrap;
          flex-shrink: 0;
          line-height: 1.4;
          transition: background 0.12s;
        }
        .db-import-btn:hover:not(:disabled) {
          background: color-mix(in srgb, var(--accent) 10%, transparent);
        }
        .db-import-btn.imported {
          color: var(--muted);
          border-color: color-mix(in srgb, var(--border) 60%, transparent);
          cursor: default;
        }
        .db-import-btn:disabled:not(.imported) { opacity: 0.6; }
        .db-empty-hint {
          font-size: 12px;
          color: var(--muted);
          padding: 8px 0;
          text-align: center;
        }
        .db-error-hint { color: var(--error, #e55); }

        .db-property-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 4px 10px;
          padding: 6px 0 8px;
          margin-bottom: 4px;
          border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
        }
        .db-property-item {
          display: flex;
          justify-content: space-between;
          gap: 6px;
          font-size: 11px;
        }
        .db-property-key { color: var(--muted); white-space: nowrap; }
        .db-property-value { font-weight: 600; text-align: right; }

        .search-databases-btn {
          width: 100%;
          padding: 7px 12px;
          border-radius: 10px;
          border: 1px solid var(--accent);
          background: var(--accent-soft);
          color: var(--accent);
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.14s ease;
        }

        .search-databases-btn:hover:not(:disabled) {
          background: color-mix(in srgb, var(--accent) 20%, transparent);
        }

        .search-databases-btn:disabled {
          border-color: var(--border);
          background: var(--bg-elev);
          color: var(--subtle);
          cursor: not-allowed;
        }

        .search-databases-btn.loading {
          opacity: 0.7;
          cursor: wait;
        }

        .mlip-model-list {
          display: grid;
          gap: 6px;
          margin-top: 8px;
        }

        .mlip-model-item {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          border-radius: 12px;
          padding: 7px 10px;
          text-align: left;
          cursor: pointer;
          transition: all 0.14s ease;
          width: 100%;
        }

        .mlip-model-item:hover {
          background: var(--bg-soft);
          border-color: color-mix(in srgb, #10b981 25%, var(--border));
        }

        .mlip-model-item.active {
          background: color-mix(in srgb, #10b981 10%, var(--bg-elev));
          border-color: color-mix(in srgb, #10b981 35%, var(--border));
        }

        .mlip-model-label {
          font-size: 12px;
          font-weight: 600;
          color: var(--text);
          margin-bottom: 2px;
        }

        .mlip-model-item.active .mlip-model-label {
          color: #10b981;
        }

        .mlip-model-desc {
          font-size: 11px;
          color: var(--muted);
        }

        .mlip-calc-chips {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 4px;
        }
        .mlip-calc-chip {
          padding: 5px 12px;
          border-radius: 999px;
          border: 1px solid var(--border);
          background: var(--bg-soft);
          color: var(--muted);
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.14s ease;
        }
        .mlip-calc-chip:hover {
          border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
          color: var(--text);
        }
        .mlip-calc-chip.active {
          border-color: color-mix(in srgb, var(--accent) 50%, var(--border));
          background: color-mix(in srgb, var(--accent) 12%, var(--bg-soft));
          color: var(--accent);
          font-weight: 600;
        }

        .mlip-run-btn {
          width: 100%;
          padding: 9px;
          background: var(--accent);
          color: #fff;
          border: none;
          border-radius: 8px;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          transition: opacity 0.15s;
          margin-top: 4px;
        }
        .mlip-run-btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .mlip-run-btn.loading { opacity: 0.7; }

        .mlip-result-list { display: grid; gap: 6px; }

        .mlip-result-card {
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 8px 10px;
          background: var(--bg-elev);
          display: grid;
          gap: 6px;
        }
        .mlip-result-card.error {
          border-color: color-mix(in srgb, #ef4444 30%, var(--border));
        }

        .mlip-result-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 6px;
        }
        .mlip-result-meta {
          display: flex;
          flex-direction: column;
          gap: 1px;
          min-width: 0;
        }
        .mlip-result-type {
          font-size: 12px;
          font-weight: 700;
          color: var(--text);
        }
        .mlip-result-struct {
          font-size: 11px;
          color: var(--muted);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .mlip-dismiss-btn {
          flex-shrink: 0;
          background: none;
          border: none;
          color: var(--subtle);
          font-size: 14px;
          cursor: pointer;
          padding: 0 2px;
          line-height: 1;
        }
        .mlip-dismiss-btn:hover { color: var(--text); }

        .mlip-result-error {
          font-size: 12px;
          color: #ef4444;
          background: color-mix(in srgb, #ef4444 8%, transparent);
          border-radius: 6px;
          padding: 6px 8px;
        }

        .mlip-viz-placeholder {
          font-size: 11px;
          color: var(--subtle);
          border: 1px dashed var(--border);
          border-radius: 6px;
          padding: 12px;
          text-align: center;
        }

        .mlip-result-actions {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          padding-top: 2px;
        }
        .mlip-import-btn {
          font-size: 13px;
          font-weight: 600;
          color: var(--accent);
          background: none;
          border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
          border-radius: 8px;
          padding: 5px 12px;
          cursor: pointer;
          line-height: 1.4;
          transition: background 0.12s;
        }
        .mlip-import-btn:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }
        .mlip-discuss-btn {
          font-size: 11px;
          font-weight: 600;
          color: var(--accent);
          background: none;
          border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
          border-radius: 4px;
          padding: 1px 6px;
          cursor: pointer;
          line-height: 1.4;
          transition: background 0.12s;
        }
        .mlip-discuss-btn:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }

        .workspace-powered-by {
          font-size: 11px;
          color: var(--subtle);
        }

        .workspace-no-backing {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 11px;
          font-weight: 600;
          color: #d97706;
          background: color-mix(in srgb, #d97706 14%, transparent);
          border: 1px solid color-mix(in srgb, #d97706 30%, transparent);
          border-radius: 999px;
          padding: 3px 9px;
          width: fit-content;
          cursor: help;
        }

        .workspace-powered-by a {
          color: var(--muted);
          text-decoration: none;
          font-weight: 600;
        }

        .workspace-powered-by a:hover {
          color: var(--text);
          text-decoration: underline;
        }

        .viewer-placeholder {
          height: 220px;
          border-radius: 18px;
          background:
            radial-gradient(circle at center, rgba(245, 158, 11, 0.12), transparent 28%),
            linear-gradient(180deg, color-mix(in srgb, var(--bg-soft), transparent 0%), color-mix(in srgb, var(--bg-elev), transparent 0%));
          border: 1px solid var(--border);
          position: relative;
          overflow: hidden;
        }

        .viewer-orbit {
          position: absolute;
          inset: 24px;
          border: 1px dashed rgba(245, 158, 11, 0.35);
          border-radius: 999px;
        }

        .viewer-core {
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          width: 70px;
          height: 70px;
          border-radius: 50%;
          background: linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent), white 20%));
          color: white;
          display: grid;
          place-items: center;
          font-weight: 700;
        }

        .struct-select {
          width: 100%;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: linear-gradient(180deg, color-mix(in srgb, var(--bg-elev), white 14%), var(--bg-soft));
          background-image:
            linear-gradient(180deg, color-mix(in srgb, var(--bg-elev), white 14%), var(--bg-soft)),
            none;
          color: var(--text);
          padding: 8px 32px 8px 10px;
          font: inherit;
          font-size: 13px;
          cursor: pointer;
          appearance: none;
          -webkit-appearance: none;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2 4l4 4 4-4' stroke='%239b97b3' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E"),
            linear-gradient(180deg, color-mix(in srgb, var(--bg-elev), white 14%), var(--bg-soft));
          background-repeat: no-repeat, no-repeat;
          background-position: right 10px center, 0 0;
          background-size: 12px, 100%;
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.45);
          transition: border-color 0.18s ease;
          margin-top: 6px;
        }
        .struct-select:hover { border-color: color-mix(in srgb, var(--accent) 24%, var(--border)); }
        .struct-select:disabled { opacity: 0.5; cursor: not-allowed; }
        .struct-select option { background: var(--bg-elev); color: var(--text); }

        .structure-upload-zone {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 8px;
          margin-top: 6px;
          padding: 8px 10px;
          border: 1px dashed var(--border);
          border-radius: 10px;
          transition: border-color 0.15s ease, background 0.15s ease;
        }
        .structure-upload-zone.drag-over {
          border-color: var(--accent);
          background: color-mix(in srgb, var(--accent) 8%, transparent);
        }

        .structure-upload-btn {
          border: 1px solid var(--border);
          border-radius: 999px;
          background: var(--bg-soft);
          color: var(--text);
          padding: 5px 12px;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          white-space: nowrap;
        }
        .structure-upload-btn:hover { border-color: color-mix(in srgb, var(--accent) 24%, var(--border)); }

        .structure-upload-hint {
          font-size: 11px;
          color: var(--muted);
          line-height: 1.4;
        }

        .workspace-form {
          display: grid;
          gap: 8px;
        }

        .workspace-form-field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .workspace-form-field-label {
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.04em;
          color: var(--muted);
          text-transform: uppercase;
        }

        .relax-mode-desc {
          font-size: 11px;
          line-height: 1.55;
          color: var(--subtle);
          padding: 1px 2px;
        }

        .workspace-form input {
          width: 100%;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-soft);
          color: var(--text);
          padding: 10px 12px;
          font-size: 13px;
        }

        .workspace-form textarea {
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-soft);
          color: var(--text);
          padding: 10px 12px;
          resize: vertical;
          min-height: 96px;
          font: inherit;
        }

        .workspace-picker {
          position: relative;
        }

        .workspace-picker-trigger {
          width: 100%;
          border: 1px solid var(--border);
          border-radius: 12px;
          background:
            linear-gradient(180deg, color-mix(in srgb, var(--bg-elev), white 14%), var(--bg-soft));
          color: var(--text);
          padding: 8px 10px;
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 14px;
          text-align: left;
          cursor: pointer;
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45);
          transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
        }

        .workspace-picker-trigger:hover,
        .workspace-picker.open .workspace-picker-trigger {
          border-color: color-mix(in srgb, var(--accent) 24%, var(--border));
          box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.55),
            0 10px 24px rgba(148, 163, 184, 0.12);
          transform: translateY(-1px);
        }

        .workspace-picker-trigger:focus-within {
          border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
          box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.55),
            0 0 0 3px color-mix(in srgb, var(--accent) 35%, transparent);
        }

        .workspace-picker.disabled .workspace-picker-trigger {
          opacity: 0.45;
          cursor: not-allowed;
          pointer-events: none;
        }

        .workspace-picker-copy {
          display: grid;
          gap: 2px;
          min-width: 0;
        }

        .workspace-picker-label {
          font-size: 10px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--subtle);
          font-weight: 700;
        }

        .workspace-picker-value {
          font-size: 12px;
          line-height: 1.3;
          font-weight: 700;
          color: var(--text);
        }

        .workspace-picker-actions {
          display: flex;
          align-items: center;
          gap: 4px;
          flex-shrink: 0;
          margin-top: 1px;
        }

        .workspace-picker-recommend {
          display: inline-flex;
          align-items: center;
          gap: 3px;
          padding: 2px 6px;
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
          background: color-mix(in srgb, var(--accent) 10%, var(--bg-elev));
          color: var(--accent);
          font-size: 10px;
          font-weight: 600;
          cursor: pointer;
          opacity: 0.55;
          white-space: nowrap;
          transition: opacity 0.15s ease, transform 0.15s ease, background 0.15s ease;
        }

        .workspace-picker-trigger:hover .workspace-picker-recommend {
          opacity: 0.9;
        }

        .workspace-picker-recommend:hover {
          opacity: 1 !important;
          background: color-mix(in srgb, var(--accent) 18%, var(--bg-elev));
          transform: scale(1.04);
        }

        .workspace-task-builder-header {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 10px;
          margin-bottom: 4px;
        }

        .workspace-task-builder-label {
          font-size: 12px;
          font-weight: 700;
          color: var(--text);
        }

        .workspace-section-recommend {
          width: 26px;
          height: 26px;
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
          background: color-mix(in srgb, var(--accent) 9%, var(--bg-elev));
          display: grid;
          place-items: center;
          color: var(--accent);
          font-size: 11px;
          cursor: pointer;
          opacity: 0.6;
          flex-shrink: 0;
          transition: opacity 0.15s ease, transform 0.15s ease;
        }

        .workspace-section-recommend:hover {
          opacity: 1;
          transform: scale(1.12);
        }
        .workspace-section-recommend:disabled {
          opacity: 0.25;
          cursor: default;
        }
        .workspace-section-recommend:disabled:hover { transform: none; }

        .ask-goldilocks-btn {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 3px 8px;
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border));
          background: color-mix(in srgb, var(--accent) 10%, var(--bg-elev));
          color: var(--accent);
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.15s, border-color 0.15s, transform 0.15s;
          white-space: nowrap;
        }
        .ask-goldilocks-btn:hover {
          background: color-mix(in srgb, var(--accent) 18%, var(--bg-elev));
          border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
          transform: scale(1.04);
        }
        .ask-goldilocks-btn span { font-size: 10px; }

        [data-tooltip] { position: relative; }
        [data-tooltip]::after {
          content: attr(data-tooltip);
          position: absolute;
          bottom: calc(100% + 5px);
          left: 50%;
          transform: translateX(-50%);
          background: var(--text);
          color: var(--bg);
          font-size: 11px;
          font-weight: 500;
          line-height: 1.3;
          padding: 3px 8px;
          border-radius: 6px;
          white-space: nowrap;
          pointer-events: none;
          opacity: 0;
          transition: opacity 0s;
          z-index: 200;
        }
        [data-tooltip]:hover::after { opacity: 1; }

        .workspace-picker-caret {
          width: 24px;
          height: 24px;
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--border));
          background: color-mix(in srgb, var(--accent) 9%, var(--bg-elev));
          display: grid;
          place-items: center;
          color: var(--accent);
          transition: transform 0.18s ease;
        }

        .workspace-picker.open .workspace-picker-caret {
          transform: rotate(180deg);
        }

        .workspace-picker-menu {
          position: absolute;
          top: calc(100% + 6px);
          left: 0;
          right: 0;
          z-index: 20;
          border: 1px solid var(--border);
          border-radius: 16px;
          background: color-mix(in srgb, var(--bg-elev), white 8%);
          box-shadow: 0 20px 48px rgba(15, 23, 42, 0.16);
          padding: 10px;
          max-height: min(360px, 58vh);
          overflow-y: auto;
          display: grid;
          gap: 10px;
        }

        .workspace-picker-group {
          display: grid;
          gap: 4px;
        }

        .workspace-picker-group:not(:last-child) {
          padding-bottom: 2px;
          border-bottom: 1px solid rgba(148, 163, 184, 0.16);
        }

        .workspace-picker-group-label {
          font-size: 10px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--subtle);
          font-weight: 700;
          padding: 2px 4px 0;
        }

        .workspace-picker-options {
          display: grid;
          gap: 2px;
        }

        .workspace-picker-option {
          border: 1px solid transparent;
          border-radius: 10px;
          background: transparent;
          color: var(--text);
          padding: 7px 10px;
          display: flex;
          align-items: center;
          gap: 8px;
          text-align: left;
          cursor: pointer;
          transition: background 0.16s ease, border-color 0.16s ease, transform 0.16s ease;
        }

        .workspace-picker-option-ask {
          flex-shrink: 0;
          margin-left: auto;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
          background: color-mix(in srgb, var(--accent) 8%, var(--bg));
          color: var(--accent);
          font-size: 10px;
          font-weight: 700;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          transition: all 0.14s ease;
          opacity: 0;
        }

        .workspace-picker-option:hover .workspace-picker-option-ask {
          opacity: 0.75;
        }

        .workspace-picker-option-ask:hover {
          opacity: 1 !important;
          border-color: var(--accent);
          background: color-mix(in srgb, var(--accent) 18%, var(--bg));
          transform: scale(1.1);
        }

        .workspace-picker-option:hover {
          background: var(--bg-soft);
          border-color: rgba(148, 163, 184, 0.16);
          transform: translateX(1px);
        }

        .workspace-picker-option.active {
          background: color-mix(in srgb, var(--accent) 10%, var(--bg-soft));
          border-color: color-mix(in srgb, var(--accent) 24%, var(--border));
        }

        .workspace-picker-option-title {
          font-size: 12px;
          font-weight: 700;
          color: var(--text);
        }

        .toggle-row {
          grid-template-columns: 1fr auto;
          align-items: center;
        }

        .toggle-row input {
          width: 18px;
          height: 18px;
          accent-color: var(--accent);
        }

        .workspace-code {
          border: 1px solid var(--border);
          background: color-mix(in srgb, var(--bg-soft), black 8%);
          border-radius: 16px;
          padding: 14px;
          overflow-x: auto;
        }

        .metric-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
        }

        .mlip-sp-rows {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .mlip-sp-row {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 8px;
          padding: 5px 0;
          border-bottom: 1px solid var(--border);
        }
        .mlip-sp-rows .mlip-sp-row:last-child { border-bottom: none; }
        .mlip-sp-label {
          font-size: 11px;
          color: var(--muted);
          white-space: nowrap;
        }
        .mlip-sp-value {
          font-size: 15px;
          font-weight: 600;
          color: var(--text);
          white-space: nowrap;
        }

        .metric-card span {
          display: block;
          color: var(--muted);
          font-size: 11px;
          margin-bottom: 4px;
        }

        .metric-card strong {
          font-size: 17px;
        }

        .chart-placeholder {
          height: 170px;
          border-radius: 18px;
          background: var(--bg-elev);
          border: 1px solid var(--border);
          display: flex;
          align-items: flex-end;
          justify-content: space-evenly;
          padding: 18px;
          gap: 12px;
        }

        .chart-bar {
          width: 18%;
          border-radius: 999px 999px 10px 10px;
          background: linear-gradient(180deg, color-mix(in srgb, var(--accent), white 18%), var(--accent));
        }

        .chart-bar.h1 { height: 38%; }
        .chart-bar.h2 { height: 62%; }
        .chart-bar.h3 { height: 80%; }
        .chart-bar.h4 { height: 52%; }
        .chart-bar.h5 { height: 70%; }

        .projects-view {
          padding: 32px 0;
        }

        .project-home {
          padding: 40px 0 32px;
          width: min(640px, calc(100% - 32px));
          margin: 0 auto;
          display: flex;
          flex-direction: column;
          gap: 0;
        }

        .project-home-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 20px;
        }

        .project-home-title {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .project-home-title h1 {
          font-size: 26px;
          font-weight: 700;
        }

        .project-dot-lg {
          width: 14px;
          height: 14px;
          border-radius: 50%;
          flex-shrink: 0;
        }

        .project-home-tabs {
          display: flex;
          gap: 2px;
          border-bottom: 1px solid var(--border);
          margin-bottom: 20px;
        }

        .project-tab-btn {
          background: none;
          border: none;
          padding: 8px 14px;
          font-size: 14px;
          color: var(--muted);
          cursor: pointer;
          border-bottom: 2px solid transparent;
          margin-bottom: -1px;
          transition: color 0.15s, border-color 0.15s;
        }

        .project-tab-btn.active {
          color: var(--text);
          font-weight: 600;
          border-bottom-color: var(--text);
        }

        .project-home-body {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .project-home-empty {
          color: var(--muted);
          font-size: 13px;
          padding: 16px 0;
        }

        .project-chat-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 12px 14px;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: var(--bg-elev);
          cursor: pointer;
          transition: background 0.14s;
        }

        .project-chat-row:hover {
          background: color-mix(in srgb, var(--accent) 6%, var(--bg-elev));
          border-color: color-mix(in srgb, var(--accent) 25%, var(--border));
        }

        .project-chat-title {
          font-size: 13px;
          font-weight: 600;
          margin-bottom: 2px;
        }

        .project-chat-meta {
          font-size: 11px;
          color: var(--muted);
        }

        .project-source-row {
          padding: 10px 14px;
          border: 1px solid var(--border);
          border-radius: 10px;
          background: var(--bg-elev);
          font-size: 13px;
          color: var(--text);
        }

        .projects-head h1 {
          font-size: 34px;
          margin-bottom: 10px;
        }

        .projects-head p {
          color: var(--muted);
          margin-bottom: 22px;
        }

        .projects-head {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 18px;
        }

        .projects-head-actions {
          display: flex;
          align-items: center;
          gap: 10px;
        }

        .projects-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 14px;
        }

        .project-card {
          border: 1px solid var(--border);
          background: var(--panel);
          border-radius: 20px;
          padding: 18px;
        }

        .project-card-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          margin-bottom: 10px;
        }

        .project-card-title {
          display: flex;
          align-items: center;
          gap: 10px;
        }

        .project-color {
          width: 12px;
          height: 12px;
          border-radius: 50%;
        }

        .project-name {
          font-weight: 700;
        }

        .project-meta {
          font-size: 11px;
          color: var(--subtle);
          margin-top: 2px;
        }

        .back-link {
          border: none;
          background: transparent;
          color: var(--muted);
          cursor: pointer;
          margin-bottom: 12px;
          font-size: 13px;
        }

        .back-link:hover {
          color: var(--text);
        }

        .project-detail-panel {
          border: 1px solid var(--border);
          background: var(--panel);
          border-radius: 20px;
          padding: 18px;
        }

        .project-detail-panel + .project-detail-panel {
          margin-top: 14px;
        }

        .project-source-list {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin-top: 10px;
        }

        .project-source-chip {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          border-radius: 999px;
          padding: 8px 12px;
          font-size: 12px;
          color: var(--text);
        }

        .project-session-list {
          display: grid;
          gap: 10px;
          margin-top: 10px;
        }

        .project-session-row {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          border-radius: 16px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 10px;
        }

        .project-session-row:hover {
          background: var(--bg-soft);
        }

        .project-session-main {
          border: none;
          background: transparent;
          color: var(--text);
          display: flex;
          align-items: center;
          gap: 10px;
          text-align: left;
          cursor: pointer;
          flex: 1;
          padding: 4px 6px;
        }

        .project-session-delete {
          border: 1px solid var(--border);
          background: transparent;
          color: var(--muted);
          border-radius: 12px;
          padding: 8px 12px;
          cursor: pointer;
          flex-shrink: 0;
        }

        .project-session-delete:hover {
          color: #ef4444;
          border-color: color-mix(in srgb, #ef4444, var(--border) 40%);
          background: color-mix(in srgb, #ef4444, transparent 92%);
        }

        .project-session-copy {
          display: grid;
          gap: 3px;
        }

        .project-session-copy strong {
          font-size: 14px;
        }

        .project-session-copy span {
          font-size: 12px;
          color: var(--muted);
        }

        .project-empty p {
          color: var(--muted);
          margin-bottom: 14px;
        }

        .project-empty.compact p {
          margin-bottom: 0;
        }

        .ghost-btn {
          margin-top: 14px;
          width: 100%;
        }

        .modal-scrim {
          position: absolute;
          inset: 0;
          background: rgba(3, 3, 8, 0.45);
          backdrop-filter: blur(10px);
          z-index: 40;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px;
        }

        .modal-card {
          width: min(620px, 100%);
          max-height: calc(100dvh - 48px);
          border-radius: 28px;
          border: 1px solid var(--border);
          background: var(--panel);
          box-shadow: var(--shadow);
          padding: 24px;
          position: relative;
          overflow-y: auto;
        }

        .settings-overlay {
          position: absolute;
          inset: 0;
          border-radius: 28px;
          background: var(--panel);
          padding: 24px;
          z-index: 4;
          box-shadow: var(--shadow);
          overflow-y: auto;
        }

        .settings-overlay-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 4px;
          position: sticky;
          top: -24px;
          padding-top: 24px;
          margin-top: -24px;
          background: var(--panel);
          z-index: 3;
        }

        .settings-overlay-head h3 {
          font-size: 18px;
          font-weight: 700;
        }

        .modal-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 18px;
          position: sticky;
          top: -24px;
          padding-top: 24px;
          margin-top: -24px;
          background: var(--panel);
          z-index: 3;
        }

        .modal-head h2 {
          font-size: 28px;
          margin-bottom: 6px;
        }

        .modal-head p {
          color: var(--muted);
          line-height: 1.7;
          font-size: 13px;
        }

        .option-grid {
          display: grid;
          gap: 12px;
          margin: 22px 0;
        }

        .option-card {
          border: 1px solid var(--border);
          background: var(--bg-elev);
          border-radius: 18px;
          padding: 18px;
          cursor: pointer;
          transition: all 0.14s ease;
        }

        .option-card.selected {
          border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
          background: color-mix(in srgb, var(--accent) 10%, var(--bg-elev));
        }

        .option-card strong {
          display: block;
          font-size: 15px;
          margin-bottom: 6px;
        }

        .option-card span {
          color: var(--muted);
          line-height: 1.7;
          font-size: 13px;
        }

        .modal-actions {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
        }

        .primary-btn {
          border: none;
          background: linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent), white 18%));
          color: white;
          border-radius: 14px;
          padding: 12px 16px;
          cursor: pointer;
          font-weight: 700;
        }

        .settings-sections {
          display: grid;
          gap: 10px;
        }

        .settings-section {
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 14px;
          background: var(--bg-elev);
        }

        .settings-section h3 {
          font-size: 15px;
          font-weight: 700;
          margin-bottom: 4px;
        }

        .settings-section p {
          color: var(--muted);
          margin-bottom: 10px;
          line-height: 1.6;
          font-size: 12px;
        }

        .credential-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 8px 0;
        }

        .credential-row + .credential-row {
          border-top: 1px solid var(--border);
        }

        .credential-label {
          font-size: 13px;
          font-weight: 600;
          flex-shrink: 0;
          width: 90px;
        }

        .credential-input {
          flex: 1;
          border: 1px solid var(--border);
          background: var(--bg);
          color: var(--text);
          border-radius: 10px;
          padding: 8px 10px;
          font-size: 12px;
          min-width: 0;
        }

        .credential-status {
          flex-shrink: 0;
          white-space: nowrap;
          font-size: 11px;
          color: var(--subtle);
        }

        .credential-status-ok { color: #10b981; }
        .credential-status-error { color: #ef4444; }

        .credential-input::placeholder {
          color: var(--subtle);
        }

        .settings-field-hint {
          display: block;
          color: var(--muted);
          font-size: 11px;
          line-height: 1.5;
          margin-top: 8px;
        }

        .profile-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 14px;
        }

        .settings-row-control {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          flex-shrink: 0;
        }

        .profile-row strong {
          display: block;
          font-size: 13px;
          margin-bottom: 2px;
        }

        .profile-row span {
          color: var(--muted);
          font-size: 11px;
        }

        .toggle-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 14px;
        }

        .toggle-row strong {
          display: block;
          font-size: 13px;
          margin-bottom: 2px;
        }

        .toggle-row span {
          color: var(--muted);
          font-size: 11px;
        }


        .contribute-word {
          font-style: italic;
          font-weight: 800;
          background: linear-gradient(90deg, var(--accent), color-mix(in srgb, var(--accent), #10b981 60%));
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }

        .contact-name {
          display: block;
          font-weight: 600;
          font-size: 13px;
          text-align: center;
        }

        .contact-role {
          font-weight: 400;
          font-size: 11px;
          color: var(--muted);
        }

        .contact-email {
          font-size: 12px;
          color: var(--muted);
          flex-shrink: 0;
        }

        .contact-person-row {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .contact-right {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .contact-team-box {
          display: flex;
          justify-content: space-around;
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 10px 8px;
          margin-top: 6px;
          text-decoration: none;
          color: inherit;
          transition: background 0.14s ease, border-color 0.14s ease;
        }

        .contact-team-box:hover {
          background: var(--bg-soft);
          border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
        }

        .contact-team-entry {
          display: flex;
          flex-direction: row;
          align-items: baseline;
          gap: 5px;
        }

        .ack-list {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .ack-chip {
          font-size: 12px;
          padding: 4px 10px;
          border-radius: 999px;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--text);
          text-decoration: none;
          transition: border-color 0.15s, background 0.15s;
        }

        .ack-note {
          margin-top: 8px;
          font-size: 11px;
          color: var(--subtle);
        }

        .ack-chip:hover {
          border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
          background: color-mix(in srgb, var(--accent) 8%, var(--bg-soft));
        }

        .contact-list {
          display: grid;
          gap: 8px;
        }

        .contact-link {
          display: block;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--text);
          border-radius: 12px;
          padding: 10px 12px;
          text-decoration: none;
          font-size: 13px;
        }

        .contact-primary-link {
          text-align: center;
        }

        .contact-link:hover {
          background: var(--bg-soft);
        }

        .secondary-btn {
          border: 1px solid var(--border);
          background: var(--bg-soft);
          color: var(--text);
          border-radius: 10px;
          padding: 6px 11px;
          font-size: 12px;
          cursor: pointer;
          white-space: nowrap;
        }

        .secondary-btn.compact {
          padding: 7px 10px;
          font-size: 12px;
        }

        .sidebar-empty {
          margin: 10px 4px 0;
          padding: 12px;
          border: 1px dashed var(--border);
          border-radius: 14px;
          background: var(--panel);
        }

        @media (max-width: 980px) {
          .workspace {
            display: none;
          }

          .projects-grid,
          .metric-grid {
            grid-template-columns: 1fr;
          }
        }

        @media (max-width: 720px) {
          .sidebar {
            position: absolute;
            inset: 0 auto 0 0;
            height: 100%;
          }

          .composer-wrap {
            padding: 10px 12px 12px;
          }

          .composer-controls {
            gap: 6px;
          }

          .composer-btn,
          .send-btn {
            width: 38px;
            height: 38px;
          }

          .element-picker-grid {
            grid-template-columns: repeat(9, minmax(0, 1fr));
          }
        }
      `}</style>

      <header className="top-header">
        <div
          className="top-header-left"
          style={{ width: sbOpen ? sidebarWidth : 100 }}
        >
          <div className="brand">
            <div className="brand-icon">
              <LogoImage alt="Goldilocks logo" />
            </div>
            {sbOpen && (
              <div className="brand-copy">
                <span className="brand-name brand-ink">Goldilocks</span>
                <span className="brand-slogan">Towards Greener Computation</span>
              </div>
            )}
          </div>
          <button
            className="icon-btn"
            onClick={() => setSbOpen((open) => !open)}
            title={sbOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            <MenuIcon />
          </button>
        </div>

        {sbOpen && (
          <div
            className={`resize-handle${hoveredHandle === "sidebar" || resizingPane === "sidebar" ? " handle-active" : ""}`}
            onMouseEnter={() => setHoveredHandle("sidebar")}
            onMouseLeave={() => setHoveredHandle(null)}
            onMouseDown={(e) =>
              startPaneResize(e, {
                startWidth: sidebarWidth,
                min: 200,
                max: 480,
                direction: "right",
                onChange: updateSidebarWidth,
                onStart: () => setResizingPane("sidebar"),
                onEnd: () => setResizingPane(null),
              })
            }
          />
        )}

        <div className="top-header-center">
          <button
            className="top-header-theme-btn"
            onClick={() => updateTheme(resolvedTheme === "light" ? "dark" : "light")}
          >
            {resolvedTheme === "light" ? "☀ Day" : "☽ Night"}
          </button>
        </div>

        {toolsOpen && (
          <div
            className={`resize-handle${hoveredHandle === "tools" || resizingPane === "tools" ? " handle-active" : ""}`}
            onMouseEnter={() => setHoveredHandle("tools")}
            onMouseLeave={() => setHoveredHandle(null)}
            onMouseDown={(e) =>
              startPaneResize(e, {
                startWidth: toolsWidth,
                min: 320,
                max: 700,
                direction: "left",
                onChange: updateToolsWidth,
                onStart: () => setResizingPane("tools"),
                onEnd: () => setResizingPane(null),
              })
            }
          />
        )}

        <div className="top-header-right" style={{ width: toolsOpen ? toolsWidth : 100 }}>
          {toolsOpen && <span className="brand-ink">Tools</span>}
          <div className="top-header-right-actions">
            <button
              className="icon-btn"
              onClick={() => setToolsOpen((open) => !open)}
              title={toolsOpen ? "Collapse tools" : "Expand tools"}
            >
              <MenuIcon />
            </button>
            {/* New, additive: takes over the whole main content area with a
                full-page grid of all six Tools (renderToolsOverview) -- kept
                as its own button right next to the one above rather than
                repurposing it, since that one's "show/hide the side panel"
                job is unchanged and still needs to work on its own. */}
            <button
              className="icon-btn"
              onClick={openToolsOverview}
              title={fullPageView === "none" ? "Expand all tools" : "Close tools overview"}
            >
              <ExpandIcon />
            </button>
          </div>
        </div>
      </header>

      {fullPageView !== "none" ? (
        renderToolsFullPage()
      ) : (
      <div className="app-row">
        {dragOver && (
          <div className="drag-overlay">
            <div className="drag-overlay-inner">
              <div className="drag-overlay-emoji">🔬</div>
              <div>Drop a structure file to attach it to the current chat.</div>
            </div>
          </div>
        )}

        <aside
          className={`sidebar${sbOpen ? "" : " closed"}${resizingPane === "sidebar" ? " resizing" : ""}`}
          style={{ width: sbOpen ? sidebarWidth : 0, minWidth: sbOpen ? sidebarWidth : 0 }}
        >
          <div className="sidebar-inner" style={{ width: sidebarWidth }}>
            <div className="sidebar-nav">
            <button className="nav-row" onClick={() => newChat(null)}>
              <PlusIcon />
              <span>{t("new_chat")}</span>
            </button>
            <button className="nav-row">
              <SearchIcon />
              <span>{t("search")}</span>
            </button>
          </div>

          <div className="sidebar-divider" />

          <div className="session-list">
            <div className="group-label">{t("projects")}</div>
            <button className="nav-row sidebar-project-create" onClick={() => setCreateProjectOpen(true)}>
              <FolderIcon />
              <span>{t("new_project")}</span>
            </button>

            <div className="sidebar-project-list">
              {visibleProjects.map((project) => {
                const isActiveProject = activeProjectId === project.id;
                return (
                  <div
                    key={project.id}
                    className={`session-item project-sidebar-main${isActiveProject ? " active" : ""}`}
                    onClick={() => openProject(project.id)}
                    style={{ cursor: "pointer" }}
                  >
                    <span className="project-dot" style={{ background: project.color }} />
                    <div className="session-copy">
                      <div className="session-title">{project.name}</div>
                    </div>
                    <button
                      className="ghost-icon-btn"
                      onClick={(e) => { e.stopPropagation(); deleteProject(project.id); }}
                      aria-label={`Delete ${project.name}`}
                    >
                      <TrashIcon />
                    </button>
                  </div>
                );
              })}
            </div>

            {hiddenProjectCount > 0 ? (
              <button className="see-more-btn" onClick={() => setShowAllProjects((prev) => !prev)}>
                {showAllProjects ? t("see_less") : `... See more (${hiddenProjectCount})`}
              </button>
            ) : null}

            <div className="group-label">{t("chats")}</div>
            <div className="sidebar-project-chats">
              {recentSessions.map((item) => {
                const tool = getToolById(item.tool);
                return (
                  <div
                    key={item.id}
                    className={`session-item${item.id === currentActiveId ? " active" : ""}`}
                    onClick={() => {
                      setActiveId(item.id);
                      setActiveProjectId(null);
                      setView("chats");
                      setShowElementPicker(false);
                    }}
                  >
                    <div className="tool-dot" style={{ background: tool?.color ?? "transparent" }} />
                    <div className="session-copy">
                      <div className="session-title">{item.title}</div>
                    </div>
                    <button
                      className="ghost-icon-btn"
                      onClick={(event) => {
                        event.stopPropagation();
                        deleteSession(item.id);
                      }}
                    >
                      <TrashIcon />
                    </button>
                  </div>
                );
              })}
            </div>

          </div>

          <div className="sidebar-footer">
            <button className="settings-row" onClick={() => setSettingsOpen(true)}>
              <strong>{t("settings")}</strong>
            </button>

            <div className="footer-note">
              Developed by{" "}
              <a href="https://www.sc.stfc.ac.uk/" target="_blank" rel="noreferrer">
                Scientific Computing, UKRI-STFC
              </a>
              <br />
              Funded by{" "}
              <a href="https://www.sc.stfc.ac.uk/programmes/ada-lovelace-centre-alc/" target="_blank" rel="noreferrer">
                Ada Lovelace Centre
              </a>
              {" & "}
              <a href="https://gtr.ukri.org/projects?ref=EP%2FZ530657%2F1" target="_blank" rel="noreferrer">
                EPSRC
              </a>
            </div>
          </div>
        </div>
      </aside>

      {sbOpen && (
        <div
          className={`resize-handle${hoveredHandle === "sidebar" || resizingPane === "sidebar" ? " handle-active" : ""}`}
          onMouseEnter={() => setHoveredHandle("sidebar")}
          onMouseLeave={() => setHoveredHandle(null)}
          onMouseDown={(e) =>
            startPaneResize(e, {
              startWidth: sidebarWidth,
              min: 200,
              max: 480,
              direction: "right",
              onChange: updateSidebarWidth,
              onStart: () => setResizingPane("sidebar"),
              onEnd: () => setResizingPane(null),
            })
          }
        />
      )}

      <main className="main">
        <div className="content">
          <div className="body">
            <div className="chat-shell">
              <div className="chat-area" ref={chatAreaRef}>
                {view === "projects" ? (
                  renderProjectsView()
                ) : view === "project" ? (
                  renderProjectHome()
                ) : !hasMessages ? (
                  <div className="welcome">
                    <div className="welcome-brand">
                      <div className="welcome-icon">
                        <LogoImage alt="Goldilocks logo" />
                      </div>
                      <div className="welcome-brand-copy">
                        <h1 className={activeProject ? undefined : "brand-ink"}>{activeProject ? activeProject.name : "Goldilocks"}</h1>
                      </div>
                    </div>
                    {activeProject && <p>{activeProject.desc}</p>}
                    {activeProject?.sources?.length ? (
                      <div className="project-welcome-sources">
                        {activeProject.sources.map((source) => (
                          <span key={source} className="project-inline-source">
                            {source}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="messages">
                    {messages.map((message, index) => {
                      if (message.role === "confirmation") {
                        return (
                          <div key={`confirmation-${index}`} className="message-row">
                            <div className="avatar assistant">
                              <LogoImage alt="Goldilocks logo" />
                            </div>
                            <div className="message-content">
                              <div className="confirmation-card">
                                <div className="confirmation-card-label">{message.label}</div>
                                {message.resolved === null ? (
                                  <div className="confirmation-card-actions">
                                    <button
                                      className="primary-btn compact"
                                      onClick={() => respondToConfirmation(session.id, index, true)}
                                      disabled={loading}
                                    >
                                      Run it
                                    </button>
                                    <button
                                      className="secondary-btn compact"
                                      onClick={() => respondToConfirmation(session.id, index, false)}
                                      disabled={loading}
                                    >
                                      Not now
                                    </button>
                                  </div>
                                ) : (
                                  <div className="confirmation-card-resolved">
                                    {message.resolved ? "✓ Approved" : "✗ Declined"}
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      }
                      const parts = getMessageDisplayParts(message);
                      return (
                        <div key={`${message.role}-${index}`} className="message-row">
                          <div className={`avatar ${message.role}`}>
                            {message.role === "user" ? <UserIcon /> : <LogoImage alt="Goldilocks logo" />}
                          </div>
                          <div className="message-content">
                            {message.role === "user" ? (
                              <div className="user-bubble">
                                {parts.images.length > 0 && (
                                  <div className="user-bubble-images">
                                    {parts.images.map((img, imgIndex) => (
                                      <img key={img.name ?? imgIndex} src={img.dataUrl} alt={img.name ?? "Attached image"} className="user-bubble-image" />
                                    ))}
                                  </div>
                                )}
                                {parts.text}
                              </div>
                            ) : (
                              <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{parts.text}</ReactMarkdown></div>
                            )}
                          </div>
                        </div>
                      );
                    })}

                    {loading && (
                      <div className="message-row">
                        <div className="avatar assistant">
                          <LogoImage alt="Goldilocks logo" />
                        </div>
                        <div className="message-content">
                          {toolStatus ? (
                            <div className="tool-status">{toolStatus}</div>
                          ) : (
                            <div className="typing">
                              <span />
                              <span />
                              <span />
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    <div ref={bottomRef} />
                  </div>
                )}
              </div>

              {(view === "chats" || view === "project") && (
                <div className="composer-wrap">
                  <div className="composer-outer">

                    {attachedFiles.length > 0 && (
                      <div className="attachment-pills">
                        {attachedFiles.map(af => (
                          <div key={af.name} className="attachment-pill">
                            <CrystalIcon />
                            <span>{af.name}</span>
                            <span style={{ color: "var(--muted)", fontSize: 11 }}>
                              {formatStructureLabel(af.ext || af.rawExt)}
                            </span>
                            <button className="ghost-icon-btn" onClick={() => setAttachedFiles(prev => prev.filter(f => f.name !== af.name))}>
                              <CloseIcon />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    {attachedImages.length > 0 && (
                      <div className="attachment-pills">
                        {attachedImages.map(img => (
                          <div key={img.name} className="attachment-pill">
                            <img src={img.dataUrl} alt="" className="attachment-thumb" />
                            <span>{img.name}</span>
                            <button className="ghost-icon-btn" onClick={() => setAttachedImages(prev => prev.filter(i => i.name !== img.name))}>
                              <CloseIcon />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="composer">
                      <div className="inline-widget-row">
                        <div
                          ref={widgetsAreaRef}
                          style={{ position: "relative", display: "flex", alignItems: "center", gap: 8 }}
                        >
                          <button
                            className={`inline-widget-btn${showElementPicker ? " active" : ""}`}
                            onClick={() => {
                              setShowElementPicker((v) => !v);
                              setShowStructureViewer(false);
                              setShowFilesPanel(false);
                            }}
                          >
                            Periodic Table
                          </button>

                          <button
                            className={`inline-widget-btn${showStructureViewer ? " active" : ""}`}
                            onClick={() => {
                              setShowStructureViewer((v) => !v);
                              setShowElementPicker(false);
                              setShowFilesPanel(false);
                            }}
                          >
                            Structure Viewer
                            {chatStructures.length > 0 && (
                              <span style={{ background: "var(--accent)", color: "#fff", borderRadius: 8, padding: "1px 6px", fontSize: 10 }}>
                                {chatStructures.length}
                              </span>
                            )}
                          </button>

                          <button
                            className={`inline-widget-btn${showFilesPanel ? " active" : ""}`}
                            onClick={() => {
                              setShowFilesPanel((v) => !v);
                              setShowElementPicker(false);
                              setShowStructureViewer(false);
                            }}
                          >
                            Files
                            {(sessionFiles.length + sessionImages.length) > 0 && (
                              <span style={{ background: "var(--accent)", color: "#fff", borderRadius: 8, padding: "1px 6px", fontSize: 10 }}>
                                {sessionFiles.length + sessionImages.length}
                              </span>
                            )}
                          </button>

                          {showElementPicker && (
                            <div className="element-picker">
                              <div className="element-picker-head">
                                <div className="menu-label">Build a formula</div>
                                <button className="ghost-icon-btn" onClick={() => setShowElementPicker(false)}>
                                  <CloseIcon />
                                </button>
                              </div>
                              <div className="element-picker-grid">
                                {/* Row 0: H | formula inset (cols 2–17) | He */}
                                <button
                                  className={`element-cell category-nonmetal${(pickerElements["H"] || 0) > 0 ? " selected" : ""}`}
                                  onClick={() => clickPickerElement("H")}
                                >
                                  H
                                  {(pickerElements["H"] || 0) > 1 && <span className="element-count">{pickerElements["H"]}</span>}
                                </button>
                                <div className="element-formula-inset" style={{ gridColumn: "2 / 18", gridRow: "1" }}>
                                  {selectedElements.length > 0 ? (
                                    <div className="element-formula-row">
                                      <pre className="element-formula-block"><code>{pickerFormulaStr(pickerElements)}</code></pre>
                                      <button className="element-formula-insert" title="Insert into chat" onClick={insertFormulaIntoInput}>Insert</button>
                                      <button className="element-formula-search" onClick={handleFormulaSearch}>Search</button>
                                      <button className="element-picker-clear" title="Clear" onClick={clearPickerElements}>✕</button>
                                    </div>
                                  ) : (
                                    <div className="element-picker-hint-inline">Click elements to build a formula</div>
                                  )}
                                </div>
                                <button
                                  className={`element-cell category-noble${(pickerElements["He"] || 0) > 0 ? " selected" : ""}`}
                                  onClick={() => clickPickerElement("He")}
                                >
                                  He
                                  {(pickerElements["He"] || 0) > 1 && <span className="element-count">{pickerElements["He"]}</span>}
                                </button>
                                {/* Rows 1–8 */}
                                {PERIODIC_TABLE_ROWS.slice(1).flatMap((row, rowIndex) =>
                                  row.map((symbol, columnIndex) =>
                                    symbol ? (
                                      <button
                                        key={`${rowIndex + 1}-${columnIndex}-${symbol}`}
                                        className={`element-cell category-${getElementCategory(symbol)}${(pickerElements[symbol] || 0) > 0 ? " selected" : ""}`}
                                        onClick={() => clickPickerElement(symbol)}
                                      >
                                        {symbol}
                                        {(pickerElements[symbol] || 0) > 1 && <span className="element-count">{pickerElements[symbol]}</span>}
                                      </button>
                                    ) : (
                                      <div key={`${rowIndex + 1}-${columnIndex}-empty`} className="element-empty" />
                                    ),
                                  ),
                                )}
                              </div>
                            </div>
                          )}

                          {showStructureViewer && (
                            <div className="structure-viewer">
                              <div className="structure-viewer-head">
                                <div className="structure-viewer-head-main">
                                  <span className="menu-label" style={{ padding: 0 }}>Structure Viewer</span>
                                  {chatStructures.length > 0 && (
                                    <select
                                      className="structure-viewer-select"
                                      value={safeViewerIdx}
                                      onChange={(event) => setViewerIdx(Number(event.target.value))}
                                    >
                                      {chatStructures.map((structure, index) => (
                                        <option key={`${structure.name}-${index}`} value={index}>
                                          {structure.name} · {formatStructureLabel(structure.ext || structure.rawExt)}
                                        </option>
                                      ))}
                                    </select>
                                  )}
                                </div>
                                <div className="structure-viewer-actions">
                                  <button className="ghost-icon-btn" onClick={() => setShowStructureViewer(false)}>
                                    <CloseIcon />
                                  </button>
                                </div>
                              </div>

                              <div className="structure-viewer-body">
                                <WeasStructureViewport source={activeViewerSource} />
                                {activeViewerSource && (
                                  <div
                                    className="structure-info"
                                    style={{
                                      display: "grid",
                                      gap: 8,
                                      padding: "14px 16px",
                                      borderTop: "1px solid rgba(148, 163, 184, 0.18)",
                                    }}
                                  >
                                    <div className="structure-meta-row">
                                      <span>File</span><span>{activeViewerSource.name}</span>
                                    </div>
                                    <div className="structure-meta-row">
                                      <span>Format</span><span>{formatStructureLabel(activeViewerSource.ext || activeViewerSource.rawExt)}</span>
                                    </div>
                                    <div className="structure-meta-row">
                                      <span>Renderer</span><span>WEAS</span>
                                    </div>
                                    <div className="structure-viewer-row-actions">
                                      {!messages.some(
                                        msg => msg.role === "user" && extractStructureFromMessageContent(msg.content)?.name === activeViewerSource.name
                                      ) && (
                                        <button
                                          className="ghost-icon-btn"
                                          title="Remove from session"
                                          onClick={() => {
                                            setSessionFiles(prev => prev.filter(f => f.name !== activeViewerSource.name));
                                            setAttachedFiles(prev => prev.filter(f => f.name !== activeViewerSource.name));
                                          }}
                                        >
                                          <TrashIcon />
                                        </button>
                                      )}
                                      <button
                                        className={`secondary-btn compact${attachedFiles.some(f => f.name === activeViewerSource.name) ? " active-insert" : ""}`}
                                        onClick={() => {
                                          const isInserted = attachedFiles.some(f => f.name === activeViewerSource.name);
                                          const content = structureFileContents[activeViewerSource.name] ?? activeViewerSource.content;
                                          setStructureFileContents(prev => ({ ...prev, [activeViewerSource.name]: content }));
                                          if (isInserted) {
                                            setAttachedFiles(prev => prev.filter(f => f.name !== activeViewerSource.name));
                                          } else {
                                            setAttachedFiles(prev => {
                                              const filtered = prev.filter(f => f.name !== activeViewerSource.name);
                                              return [...filtered, {
                                                name: activeViewerSource.name,
                                                content,
                                                rawExt: activeViewerSource.rawExt,
                                                ext: activeViewerSource.ext,
                                                isStructure: true,
                                              }];
                                            });
                                          }
                                        }}
                                        title={attachedFiles.some(f => f.name === activeViewerSource.name) ? "Remove from next message" : "Insert into next message"}
                                      >
                                        {attachedFiles.some(f => f.name === activeViewerSource.name) ? "✓ Inserted" : "Insert into next message"}
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}

                          {showFilesPanel && (
                            <div className="files-panel">
                              <div className="files-panel-head">
                                <div className="menu-label" style={{ padding: 0 }}>Files</div>
                                <button className="ghost-icon-btn" onClick={() => setShowFilesPanel(false)}>
                                  <CloseIcon />
                                </button>
                              </div>

                              <div className="files-panel-tabs">
                                <button
                                  className={`files-tab-btn${filesTab === "uploaded" ? " active" : ""}`}
                                  onClick={() => setFilesTab("uploaded")}
                                >
                                  Uploaded Files
                                </button>
                                <button
                                  className={`files-tab-btn${filesTab === "generated" ? " active" : ""}`}
                                  onClick={() => setFilesTab("generated")}
                                >
                                  Generated Files
                                </button>
                              </div>

                              {filesTab === "uploaded" ? (
                                uploadedFiles.length > 0 ? (
                                  <div className="files-list">
                                    {uploadedFiles.map((item, index) => {
                                      const isImage = item.kind === "image";
                                      const isSent = isFileSentInHistory(messages, item.name);
                                      return (
                                        <div key={`${item.name}-${index}`} className="files-row">
                                          {isImage ? (
                                            <img src={item.dataUrl} alt="" className="files-row-thumb" />
                                          ) : (
                                            <CrystalIcon />
                                          )}
                                          <div className="files-row-copy">
                                            <div className="files-row-title">
                                              <span className="files-row-name">{item.name}</span>
                                              <span className="files-row-format">
                                                {isImage ? "IMAGE" : formatStructureLabel(item.ext || item.rawExt)}
                                              </span>
                                            </div>
                                          </div>
                                          <div className="files-row-actions">
                                            {item.isStructure && (
                                              <button
                                                className="secondary-btn compact"
                                                onClick={() => {
                                                  const idx = chatStructures.findIndex((s) => s.name === item.name);
                                                  if (idx >= 0) setViewerIdx(idx);
                                                  setShowStructureViewer(true);
                                                  setShowFilesPanel(false);
                                                }}
                                              >
                                                View
                                              </button>
                                            )}
                                            {!isSent && (
                                              <button
                                                className="ghost-icon-btn"
                                                title="Remove from session"
                                                onClick={() => {
                                                  if (isImage) {
                                                    setSessionImages(prev => prev.filter(i => i.name !== item.name));
                                                    setAttachedImages(prev => prev.filter(i => i.name !== item.name));
                                                  } else {
                                                    setSessionFiles(prev => prev.filter(f => f.name !== item.name));
                                                    setAttachedFiles(prev => prev.filter(f => f.name !== item.name));
                                                  }
                                                }}
                                              >
                                                <TrashIcon />
                                              </button>
                                            )}
                                            <button
                                              className={`secondary-btn compact${item.inserted ? " active-insert" : ""}`}
                                              onClick={() => {
                                                if (isImage) {
                                                  setAttachedImages(prev =>
                                                    item.inserted
                                                      ? prev.filter(i => i.name !== item.name)
                                                      : [...prev.filter(i => i.name !== item.name), { name: item.name, dataUrl: item.dataUrl }]
                                                  );
                                                } else {
                                                  const content = structureFileContents[item.name] ?? item.content;
                                                  setStructureFileContents(prev => ({ ...prev, [item.name]: content }));
                                                  setAttachedFiles(prev =>
                                                    item.inserted
                                                      ? prev.filter(f => f.name !== item.name)
                                                      : [...prev.filter(f => f.name !== item.name), {
                                                          name: item.name,
                                                          content,
                                                          rawExt: item.rawExt,
                                                          ext: item.ext,
                                                          isStructure: item.isStructure,
                                                        }]
                                                  );
                                                }
                                              }}
                                              title={item.inserted ? "Remove from next message" : "Insert into next message"}
                                            >
                                              {item.inserted ? "✓ Inserted" : "Insert"}
                                            </button>
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                ) : (
                                  <div className="viewer-empty-state" style={{ marginBottom: 0 }}>
                                    <div className="viewer-empty-icon">📁</div>
                                    <div className="viewer-empty-label">No files in this chat yet</div>
                                    <div className="viewer-empty-hint">
                                      Attach a structure, an image, or a plain-text file (like a calculation output log) using the 📎 button, or drag and drop one.
                                    </div>
                                  </div>
                                )
                              ) : (
                                <div className="viewer-empty-state" style={{ marginBottom: 0 }}>
                                  <div className="viewer-empty-icon">⚙️</div>
                                  <div className="viewer-empty-label">No generated files yet</div>
                                  <div className="viewer-empty-hint">
                                    Once Goldilocks can generate files for you — like DFT input decks — they will show up here.
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="composer-input-row">
                        <input
                          ref={fileRef}
                          type="file"
                          style={{ display: "none" }}
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (file) readFile(file);
                            event.target.value = "";
                          }}
                        />

                        <div className="composer-controls">
                          <div style={{ position: "relative" }} ref={plusRef}>
                            <button
                              className={`composer-btn${plusOpen ? " open" : ""}`}
                              onClick={() => setPlusOpen((open) => !open)}
                              title="Open actions"
                              aria-label="Open actions"
                            >
                              <GridIcon />
                            </button>
                            {plusOpen && (
                              <div className="plus-menu">
                                <div className="menu-label">Actions</div>
                                <div
                                  className="menu-item"
                                  onClick={() => {
                                    setPlusOpen(false);
                                    setPendingProjectId(projects[0]?.id ?? null);
                                    setAddToProjectOpen(true);
                                  }}
                                >
                                  <div className="menu-item-icon">📁</div>
                                  <div className="menu-item-copy">
                                    <strong>Add to Project</strong>
                                    <span>Save this chat to a research project</span>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>

                          <button className="composer-btn" onClick={() => fileRef.current?.click()}>
                            <ClipIcon />
                          </button>
                        </div>

                        <div className="composer-text">
                          <textarea
                            ref={inputRef}
                            rows={1}
                            disabled={hasPendingConfirmation}
                            placeholder={
                              hasPendingConfirmation
                                ? "Respond to the confirmation above before sending another message."
                                : activeTool
                                  ? activeTool.placeholder
                                  : attachedFiles.length > 0 || attachedImages.length > 0
                                    ? `Ask about ${[...attachedFiles, ...attachedImages].map(f => f.name).join(", ")} or send to analyse.`
                                    : "Ask anything, or drag and drop a structure file or image."
                            }
                            value={input}
                            onChange={(event) => {
                              setInput(event.target.value);
                              event.target.style.height = "auto";
                              event.target.style.height = `${Math.min(event.target.scrollHeight, 140)}px`;
                            }}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" && !event.shiftKey) {
                                event.preventDefault();
                                send();
                              }
                            }}
                          />
                        </div>

                        <div className="model-selector" ref={modelRef}>
                          <button className="model-btn" onClick={() => setModelOpen((open) => !open)}>
                            <span className="model-dot" style={{ background: MODEL_TAG_COLORS[selectedModel.tag] }} />
                            {selectedModel.label}
                            <ChevDown />
                          </button>

                          {modelOpen && (
                            <div className="model-menu">
                              {MODEL_GROUPS.map((group, groupIndex) => (
                                <div key={group.group}>
                                  {groupIndex > 0 && <div className="sidebar-divider" style={{ margin: "6px 6px 8px" }} />}
                                  <div className="menu-label">{group.group}</div>
                                  {group.items.map((model) => (
                                    <div
                                      key={model.id}
                                      className={`menu-item${model.disabled ? " menu-item-disabled" : ""}`}
                                      aria-disabled={model.disabled || undefined}
                                      onClick={() => {
                                        if (model.disabled) return;
                                        setSelectedModel(model);
                                        setModelOpen(false);
                                      }}
                                    >
                                      <div className="menu-item-icon">
                                        <span className="model-dot" style={{ background: MODEL_TAG_COLORS[model.tag] }} />
                                      </div>
                                      <div className="menu-item-copy">
                                        <strong>{model.label}</strong>
                                        {model.disabled ? (
                                          <span className="model-coming-soon">{model.desc}</span>
                                        ) : model.tag !== "default" && configuredProviders[model.tag] ? (
                                          <span className="model-ready">✓ API key saved — ready to use</span>
                                        ) : (
                                          <span>{model.desc}</span>
                                        )}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>

                        {loading ? (
                          <button className="send-btn stop-btn" onClick={() => abortRef.current?.abort()}>
                            <StopIcon />
                          </button>
                        ) : (
                          <button className="send-btn" onClick={() => send()} disabled={!canSend}>
                            <SendIcon />
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="composer-hint">
                      Goldilocks prototype · Enter to send · Shift+Enter for new line ·{" "}
                      <span className="inline-grid-hint" aria-label="grid actions button">
                        <GridIcon />
                      </span>{" "}
                      for actions · pick a tool on the right
                    </div>
                  </div>
                </div>
              )}
            </div>

          </div>
        </div>

        {toolsOpen && (
          <div
            className={`resize-handle${hoveredHandle === "tools" || resizingPane === "tools" ? " handle-active" : ""}`}
            onMouseEnter={() => setHoveredHandle("tools")}
            onMouseLeave={() => setHoveredHandle(null)}
            onMouseDown={(e) =>
              startPaneResize(e, {
                startWidth: toolsWidth,
                min: 320,
                max: 700,
                direction: "left",
                onChange: updateToolsWidth,
                onStart: () => setResizingPane("tools"),
                onEnd: () => setResizingPane(null),
              })
            }
          />
        )}

        <aside
          className={`workspace${toolsOpen ? "" : " closed"}${resizingPane === "tools" ? " resizing" : ""}`}
          style={{ width: toolsOpen ? toolsWidth : 0, minWidth: toolsOpen ? toolsWidth : 0 }}
        >
          <div className="workspace-inner" style={{ width: toolsWidth }}>
            <div className="workspace-body">{workspaceContent}</div>
          </div>
        </aside>
      </main>
      </div>
      )}

      {settingsOpen && (
        <div className="modal-scrim">
          <div className="modal-card">
            <div className="modal-head">
              <div>
                <h2>{t("settings")}</h2>
              </div>
              <button className="icon-btn" onClick={() => setSettingsOpen(false)}>
                <CloseIcon />
              </button>
            </div>

            <div className="settings-sections">
              <section className="settings-section">
                <h3>{t("profile")}</h3>
                <div className="profile-row">
                  <div>
                    <strong>{t("experience_level")}</strong>
                    <span>{currentExperience?.title ?? t("not_selected")}</span>
                  </div>
                  <div className="settings-row-control">
                    <button
                      className="secondary-btn"
                      onClick={() => {
                        setPendingExperience(experienceLevel || "new");
                        setShowExperiencePicker(true);
                      }}
                    >
                      {t("change")}
                    </button>
                  </div>
                </div>
              </section>

              <section className="settings-section">
                <h3>{t("settings_model_heading")}</h3>
                <p>{t("settings_model_desc")}</p>
                {renderCredentialRow("openai", "OpenAI")}
                {renderCredentialRow("anthropic", "Claude")}
                {renderCredentialRow("google", "Gemini")}
                <span className="settings-field-hint">
                  {t("settings_model_key_hint")}
                </span>
              </section>

              <section className="settings-section">
                <h3>{t("settings_databases_heading")}</h3>
                <p>{t("settings_databases_desc")}</p>
                {renderCredentialRow("materials_project", "Materials Project")}
              </section>

              <section className="settings-section">
                <h3>{t("settings_compute_heading")}</h3>
                <div className="profile-row">
                  <div>
                    <strong>{t("settings_compute_label")}</strong>
                    <span>
                      {computeConnected
                        ? t("settings_compute_connected")
                        : t("settings_compute_disconnected")}
                    </span>
                  </div>
                  <div className="settings-row-control">
                    <button className="secondary-btn" onClick={() => setComputeConnected((v) => !v)}>
                      {computeConnected ? t("settings_compute_disconnect") : t("settings_compute_connect")}
                    </button>
                  </div>
                </div>
              </section>

              <section className="settings-section">
                <h3>{t("language")}</h3>
                <div className="lang-chip-row">
                  {[
                    { code: "en", label: "English" },
                    { code: "fr", label: "Français" },
                    { code: "de", label: "Deutsch" },
                    { code: "it", label: "Italiano" },
                    { code: "zh", label: "中文" },
                  ].map(({ code, label }) => (
                    <button
                      key={code}
                      className={`lang-chip${language === code ? " active" : ""}`}
                      onClick={() => updateLanguage(code)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </section>

              <section className="settings-section">
                <h3>{t("contribute_heading")}</h3>
                <p>{t("contribute_desc")}</p>
                <div className="contact-list">
                  <a className="contact-link contact-primary-link" href="https://github.com/junwen94/goldilocks-agent/issues" target="_blank" rel="noreferrer">
                    {t("github_issues")}
                  </a>
                  <a
                    className="contact-team-box"
                    href="mailto:alin-marin.elena@stfc.ac.uk,susmita.basak@stfc.ac.uk,junwen.yin@stfc.ac.uk"
                  >
                    {[
                      { name: "Alin-Marin Elena", role: "Lead" },
                      { name: "Susmita Basak",    role: "Co-Lead" },
                      { name: "Junwen Yin",        role: "Core Developer" },
                    ].map(({ name, role }) => (
                      <span key={name} className="contact-team-entry">
                        <span className="contact-role">{role}:</span>
                        <span className="contact-name">{name}</span>
                      </span>
                    ))}
                  </a>
                </div>
              </section>

              <section className="settings-section">
                <h3>{t("acknowledgements")}</h3>
                <p>{t("ack_desc")}</p>
                <div className="ack-list">
                  {[
                    { name: "Alin-Marin Elena",     email: "alin-marin.elena@stfc.ac.uk" },
                    { name: "Elena Patyukova",      email: "patyukova@gmail.com" },
                    { name: "Gilberto Teobaldi",    email: "gilberto.teobaldi@stfc.ac.uk" },
                    { name: "Jaehoon Cha",          email: "jaehoon.cha@stfc.ac.uk" },
                    { name: "Jeyan Thiyagalingam",  email: "jeyan.thiyagalingam@stfc.ac.uk" },
                    { name: "Junwen Yin",           email: "junwen.yin@stfc.ac.uk" },
                    { name: "Samuel Pinilla",       email: "samuel.pinilla@diamond.ac.uk" },
                    { name: "Susmita Basak",        email: "susmita.basak@stfc.ac.uk" },
                    { name: "Willow Sparks",        email: "willow.sparks@stfc.ac.uk" },
                  ].map(({ name, email }) => (
                    <a key={name} className="ack-chip" href={`mailto:${email}`}>{name}</a>
                  ))}
                </div>
                <div className="ack-note">{t("ack_note")}</div>
              </section>
            </div>

            {showExperiencePicker && (
              <div className="settings-overlay">
                <div className="settings-overlay-head">
                  <h3>{t("experience_level")}</h3>
                  <button className="icon-btn" onClick={() => setShowExperiencePicker(false)}>
                    <CloseIcon />
                  </button>
                </div>
                <div className="option-grid" style={{ margin: "12px 0" }}>
                  {EXPERIENCE_OPTIONS.map((option) => (
                    <button
                      key={option.id}
                      className={`option-card${pendingExperience === option.id ? " selected" : ""}`}
                      onClick={() => setPendingExperience(option.id)}
                    >
                      <strong>{t("exp_" + option.id + "_title")}</strong>
                      <span>{t("exp_" + option.id + "_desc")}</span>
                    </button>
                  ))}
                </div>
                <div className="modal-actions">
                  <button className="primary-btn" onClick={() => { saveExperience(pendingExperience); setShowExperiencePicker(false); }}>
                    {t("save")}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {showOnboarding && (
        <div className="modal-scrim">
          <div className="modal-card">
            <div className="modal-head">
              <div>
                <h2>{t("welcome")}</h2>
                <p>{t("welcome_desc")}</p>
              </div>
            </div>

            <div className="option-grid">
              {EXPERIENCE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  className={`option-card${pendingExperience === option.id ? " selected" : ""}`}
                  onClick={() => setPendingExperience(option.id)}
                >
                  <strong>{t("exp_" + option.id + "_title")}</strong>
                  <span>{t("exp_" + option.id + "_desc")}</span>
                </button>
              ))}
            </div>

            <div className="modal-actions">
              <button className="primary-btn" onClick={() => saveExperience(pendingExperience)}>
                {t("continue")}
              </button>
            </div>
          </div>
        </div>
      )}

      {addToProjectOpen && (
        <div className="modal-scrim">
          <div className="modal-card">
            <div className="modal-head">
              <div>
                <h2>{t("add_to_project")}</h2>
                <p>{t("choose_project")}</p>
              </div>
              <button className="icon-btn" onClick={() => setAddToProjectOpen(false)}>
                <CloseIcon />
              </button>
            </div>

            <div className="option-grid">
              {projects.length ? (
                projects.map((project) => (
                  <button
                    key={project.id}
                    className={`option-card${pendingProjectId === project.id ? " selected" : ""}`}
                    onClick={() => setPendingProjectId(project.id)}
                  >
                    <span className="project-dot" style={{ background: project.color }} />
                    <strong>{project.name}</strong>
                    <span>{project.desc}</span>
                  </button>
                ))
              ) : (
                <div className="project-empty">
                  <p>{t("no_projects_yet")} {t("create_project_first")}</p>
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button
                className="secondary-btn"
                onClick={() => {
                  setAddToProjectOpen(false);
                  setCreateProjectOpen(true);
                }}
              >
                {t("new_project")}
              </button>
              <button className="secondary-btn" onClick={() => setAddToProjectOpen(false)}>
                {t("cancel")}
              </button>
              <button
                className="primary-btn"
                onClick={() => addCurrentChatToProject(pendingProjectId)}
                disabled={!pendingProjectId}
              >
                {t("add_to_project")}
              </button>
            </div>
          </div>
        </div>
      )}

      {createProjectOpen && (
        <div className="modal-scrim">
          <div className="modal-card">
            <div className="modal-head">
              <div>
                <h2>New Project</h2>
                <p>Create a research space for related chats and files.</p>
              </div>
              <button className="icon-btn" onClick={() => setCreateProjectOpen(false)}>
                <CloseIcon />
              </button>
            </div>

            <div className="workspace-form">
              <label>
                Project name
                <input value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="e.g. Oxide Screening" />
              </label>
              <label>
                Description
                <input value={newProjectDesc} onChange={(event) => setNewProjectDesc(event.target.value)} placeholder="Short project summary" />
              </label>
              <label>
                Color
                <div className="theme-options">
                  {PROJECT_COLORS.map((color) => (
                    <button
                      key={color}
                      className={`theme-option${newProjectColor === color ? " active" : ""}`}
                      onClick={() => setNewProjectColor(color)}
                      style={{ minWidth: 44 }}
                    >
                      <span className="project-dot" style={{ background: color, margin: "0 auto" }} />
                    </button>
                  ))}
                </div>
              </label>
              <label className="toggle-row">
                <span>Create starter chat</span>
                <input type="checkbox" checked={newProjectWithChat} onChange={(event) => setNewProjectWithChat(event.target.checked)} />
              </label>
              {newProjectWithChat && (
                <label>
                  Starter chat title
                  <input
                    value={newProjectChatTitle}
                    onChange={(event) => setNewProjectChatTitle(event.target.value)}
                    placeholder="e.g. Initial screening chat"
                  />
                </label>
              )}
              <label className="toggle-row">
                <span>Add starter sources</span>
                <input type="checkbox" checked={newProjectWithSources} onChange={(event) => setNewProjectWithSources(event.target.checked)} />
              </label>
              {newProjectWithSources && (
                <label>
                  Sources
                  <textarea
                    rows={4}
                    value={newProjectSourcesText}
                    onChange={(event) => setNewProjectSourcesText(event.target.value)}
                    placeholder={"One source per line\nMaterials Project shortlist\nUploaded CIF batch"}
                  />
                </label>
              )}
            </div>

            <div className="modal-actions">
              <button className="secondary-btn" onClick={() => setCreateProjectOpen(false)}>
                Cancel
              </button>
              <button className="primary-btn" onClick={createProject} disabled={!newProjectName.trim()}>
                Create Project
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
