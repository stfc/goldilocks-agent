// DFT Workbench's chat-tool executor and reload restore: everything that
// drives the embedded goldilocks-workbench `Workspace` on the model's or the
// page's behalf (graph.py's CLIENT_EXECUTED_TOOLS, #18's restore). Kept out
// of App.tsx so its use of the `Workspace` and snapshot types is
// type-checked, and so it is tested against a real `createWorkspace` with
// recorded core responses (dftWorkbench.test.ts). The request payload types
// (structure input, compute request) aren't checked: the vendored tarball
// ships without the generated schema types they come from.

import {
  currentArchive,
  currentExplain,
  currentPreview,
  findingsOf,
  saveArchiveToBrowser,
} from "goldilocks-workbench";
import type {
  ArchiveDownload,
  BlockingFinding,
  CalculationDraft,
  PipelineOutcome,
  Workspace,
  WorkspaceFailure,
  WorkspaceSnapshot,
} from "goldilocks-workbench";
import type { ChatStructure } from "./utils/structureFiles";

// The goldilocks-workbench types its public surface uses without exporting.
type CoreFailure = WorkspaceFailure["failure"];
type ExplainResult = NonNullable<WorkspaceSnapshot["reviewed"]>;
type StructureInspection = NonNullable<WorkspaceSnapshot["inspection"]>;

// `{result}` or `{error}` -- the exact shape graph.py's second `interrupt()`
// expects back as its resume value.
export type DftToolOutcome = { result: unknown } | { error: string };

// Settles with `promise`, or rejects with the abort reason as soon as
// `signal` aborts -- for awaits that can't take a signal themselves
// (goldilocks-workbench's `Workspace.dispatch`). The abandoned work keeps
// running in the background; only the await is cut short.
export function untilAborted<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", onAbort));
  });
}

// Resolves once `workspace` has no operation in flight. Its actions
// silently return while one is (the panel's own auto-compute, or a request
// a Stop click abandoned), which would hand the model a stale snapshot.
export function waitForWorkspaceIdle(workspace: Workspace, signal?: AbortSignal): Promise<void> {
  if (workspace.getSnapshot().operation === null) return Promise.resolve();
  return untilAborted(new Promise<void>((resolve) => {
    const unsubscribe = workspace.subscribe(() => {
      if (workspace.getSnapshot().operation !== null) return;
      unsubscribe();
      resolve();
    });
  }), signal);
}

// `source.open` (and everything downstream) silently no-ops if
// `workspace.start` hasn't completed yet (`capabilities === null`) --
// normally dispatched by goldilocks-workbench's own `WorkspaceProvider`
// mount effect, which a chat-only flow may never have triggered.
export async function ensureWorkspaceStarted(workspace: Workspace): Promise<void> {
  if (workspace.getSnapshot().capabilities !== null) return;
  await workspace.dispatch({ type: "workspace.start" });
}

// One sentence per finding: the decision core needs, then the override
// patches that would resolve it.
function findingsText(findings: readonly BlockingFinding[]): string {
  return findings
    .map((finding) => {
      const remedies = finding.remedies.map((remedy) => JSON.stringify(remedy)).join(" or ");
      return remedies ? `${finding.summary} (resolved by setting ${remedies})` : finding.summary;
    })
    .join(" ");
}

// Why `failure` happened, for the model: the findings it carries, with their
// remedies, else core's own kind and message. Core answers 500 when it fails
// internally (it no longer falls back), and the same request then fails the
// same way; 424 when its models or pseudopotentials are missing or corrupt.
export function failureText(failure: CoreFailure): string {
  const findings = findingsOf(failure);
  if (findings.length > 0) return `${failure.kind}: ${findingsText(findings)}`;
  const hint =
    failure.status === 500
      ? " (goldilocks-core failed internally; retrying the same request won't help, and the agent's server log has core's traceback)"
      : failure.status === 424
        ? " (goldilocks-core's models or pseudopotentials are missing or corrupt; `goldilocks assets install workbench` reinstalls them, and goldilocks-agent runs it whenever it starts core)"
        : "";
  return `${failure.kind}: ${failure.message}${hint}`;
}

// Whether sending the same request again can turn out differently: core was
// unreachable, failed on its side, or lacked an asset. A 4xx refusal is the
// draft's own -- a whole preview /run repeated for nothing.
function worthRetrying(failure: CoreFailure): boolean {
  return (
    failure.kind === "network_error" ||
    (failure.status !== null && (failure.status >= 500 || failure.status === 424))
  );
}

const NO_STRUCTURE =
  "No structure has been opened in DFT Workbench yet -- call dft_open_structure first.";
const UNSETTLED =
  "DFT Workbench's settings kept changing while this ran -- call the tool again.";

// The structure input core's /inspect takes for one of the chat's structure
// files. Core reads CIF and POSCAR; any other format goes as POSCAR and
// core's own inspect failure says what is wrong with it.
export function structureInputFor(file: Pick<ChatStructure, "name" | "content" | "ext">) {
  return {
    structure_content: file.content,
    structure_name: file.name,
    structure_format: file.ext === ".cif" ? "cif" : "poscar",
  };
}

// Why dft_open_structure found no file of that name, for the model: the
// names it can use instead, or that the chat has none yet.
function missingStructureText(name: unknown, structures: readonly ChatStructure[]): string {
  const asked = `No structure file named ${JSON.stringify(name)} in this chat`;
  if (structures.length === 0) {
    return `${asked}, and the chat has no structure files yet. Ask the user to attach one, or find one with find_in_databases and get_structure.`;
  }
  const names = structures.map((s) => JSON.stringify(s.name)).join(", ");
  return `${asked}. Structure files in this chat: ${names}. Call dft_open_structure again with one of these names, or ask the user to attach the file.`;
}

function calculationOf(snapshot: WorkspaceSnapshot) {
  const draft = snapshot.draft;
  return draft && { code: draft.code, task: draft.task, hpc: draft.hpc, overrides: draft.overrides };
}

// What the model needs of an inspection: the composition and cell, and how
// many sites hold partial occupancies -- core refuses to recommend for a
// structure with any (#229). Leaves out the file content and per-site
// coordinates the Workbench's own viewer uses.
function structureSummary(inspection: StructureInspection) {
  const { structure } = inspection;
  const { lengths_angstrom, angles_degrees, volume_angstrom3 } = structure.lattice;
  return {
    formula: structure.formula,
    reduced_formula: structure.reduced_formula,
    site_count: structure.site_count,
    periodicity: structure.periodicity,
    lattice: { lengths_angstrom, angles_degrees, volume_angstrom3 },
    partially_occupied_sites: structure.sites.filter(
      (site) => site.species.length > 1 || site.species.some((species) => species.occupancy < 1),
    ).length,
  };
}

// A review for the model: each record's value, the tier that decided it and
// the advisor's note on it where there is one (e.g. why mMACE wasn't run,
// goldilocks-core#259) -- or, unresolved, why not -- with the warnings and
// the findings that block generating input files. Leaves out the provenance
// the Workbench's cards show (model evidence, citations, per-field sources,
// layout), several kB per review the model's context doesn't need.
function reviewForModel(snapshot: WorkspaceSnapshot, explain: ExplainResult) {
  return {
    calculation: calculationOf(snapshot),
    records: Object.fromEntries(
      Object.entries(explain.records).map(([name, record]) => [
        name,
        record.status === "resolved"
          ? {
              value: record.value,
              source: record.source,
              ...(record.source_note ? { source_note: record.source_note } : {}),
            }
          : {
              status: record.status,
              ...(record.reason ? { reason: record.reason } : {}),
              ...(record.blocked_by ? { blocked_by: record.blocked_by } : {}),
            },
      ]),
    ),
    warnings: explain.warnings.map(({ record, level, message }) => ({ record, level, message })),
    blocking: explain.blocking.map(({ summary, fields, remedies }) => ({ summary, fields, remedies })),
  };
}

// Sends `type`'s request for the current draft until it has an outcome:
// dispatches it, waits out whatever else is in flight (the panel's own
// auto-compute may have sent it first), and reads it back. A few rounds, as a
// settings change meanwhile makes the outcome another draft's. Null if it
// still has none.
async function settle<T>(
  workspace: Workspace,
  type: "review.compute" | "review.refreshArchive",
  outcomeOf: (snapshot: WorkspaceSnapshot) => PipelineOutcome<T> | null,
  signal?: AbortSignal,
): Promise<PipelineOutcome<T> | null> {
  for (let round = 0; round < 3; round++) {
    const outcome = outcomeOf(workspace.getSnapshot());
    if (outcome !== null) return outcome;
    await untilAborted(workspace.dispatch({ type }), signal);
    await waitForWorkspaceIdle(workspace, signal);
  }
  return outcomeOf(workspace.getSnapshot());
}

// A failure stays its draft's outcome and is never sent again on its own, so
// a tool the model calls again would only read the old failure back.
// `failure.retry` clears it and sends the request once more -- for a failure
// that might go differently (`worthRetrying`).
async function retryIfFailed<T>(
  workspace: Workspace,
  operation: "explain" | "preview",
  outcomeOf: (snapshot: WorkspaceSnapshot) => PipelineOutcome<T> | null,
  signal?: AbortSignal,
): Promise<void> {
  const outcome = outcomeOf(workspace.getSnapshot());
  if (outcome?.status !== "failure" || !worthRetrying(outcome.failure)) return;
  await untilAborted(workspace.dispatch({ type: "failure.retry", operation }), signal);
  await waitForWorkspaceIdle(workspace, signal);
}

async function explainCurrentDraft(workspace: Workspace, signal?: AbortSignal) {
  await retryIfFailed(workspace, "explain", currentExplain, signal);
  return settle(workspace, "review.compute", currentExplain, signal);
}

// Executes a client-executed DFT Workbench tool call by dispatching into the
// exact same `workspace` the embedded panel's own buttons use -- one shared
// state object, not a second implementation calling goldilocks-core's HTTP
// API from Python. Pre-checks preconditions itself: `workspace.ts`'s actions
// silently no-op on an unmet precondition rather than throwing, which would
// otherwise be indistinguishable from "nothing to report."
// `ensureCoreServerReady` resolves to null once goldilocks-core is up, or to
// why it isn't. `structures` are the structure files of the chat the call
// belongs to, which dft_open_structure picks from by name.
export async function dispatchDftTool(
  workspace: Workspace,
  ensureCoreServerReady: (signal?: AbortSignal) => Promise<string | null>,
  tool: string,
  args: Record<string, any>,
  structures: readonly ChatStructure[],
  signal?: AbortSignal,
): Promise<DftToolOutcome> {
  const readyError = await ensureCoreServerReady(signal);
  if (readyError) return { error: readyError };
  await waitForWorkspaceIdle(workspace, signal);
  await untilAborted(ensureWorkspaceStarted(workspace), signal);
  const started = workspace.getSnapshot();
  if (started.capabilities === null) {
    return {
      error: started.startupFailure
        ? failureText(started.startupFailure)
        : "DFT Workbench couldn't load goldilocks-core's capabilities.",
    };
  }

  if (tool === "dft_open_structure") {
    // The model names a file; the text comes from the chat itself (#73).
    const file = structures.find((structure) => structure.name === args.file_name);
    if (!file) return { error: missingStructureText(args.file_name, structures) };
    const input = structureInputFor(file);
    await untilAborted(workspace.dispatch({ type: "source.open", input }), signal);
    const snapshot = workspace.getSnapshot();
    if (snapshot.structureInput !== input || snapshot.inspection === null) {
      const failed = snapshot.inspectFailure;
      return {
        error: failed?.input === input
          ? failureText(failed.failure)
          : "Another structure was opened in DFT Workbench meanwhile -- open this one again.",
      };
    }
    return {
      result: {
        structure: structureSummary(snapshot.inspection),
        calculation: calculationOf(snapshot),
        // Structure-specific overrides the previous structure had set,
        // which opening this one cleared; code, task, HPC profile and the
        // method settings carry over.
        cleared_overrides: snapshot.clearedOverrides,
        tasks_available: started.capabilities.tasks.map((task) => task.id),
      },
    };
  }

  if (tool === "dft_review") {
    if (workspace.getSnapshot().structureInput === null) return { error: NO_STRUCTURE };
    const explain = await explainCurrentDraft(workspace, signal);
    if (explain === null) return { error: UNSETTLED };
    if (explain.status === "failure") return { error: failureText(explain.failure) };
    return { result: reviewForModel(workspace.getSnapshot(), explain.value) };
  }

  if (tool === "dft_download_bundle") {
    if (workspace.getSnapshot().structureInput === null) return { error: NO_STRUCTURE };
    const explain = await explainCurrentDraft(workspace, signal);
    if (explain === null) return { error: UNSETTLED };
    if (explain.status === "failure") return { error: failureText(explain.failure) };
    if (explain.value.blocking.length > 0) {
      return {
        error: `Input files can't be generated for the current settings: ${findingsText(explain.value.blocking)} Ask the user before changing any setting.`,
      };
    }
    // `review.refreshArchive` fetches the archive without saving it: a save
    // is an `<a>` click, which browsers drop outside a direct user gesture,
    // and this runs several awaits past whatever click sent the chat
    // message. The chat's Download card saves it on the user's own click
    // (`keepBundle`/`saveBundle`).
    await retryIfFailed(workspace, "preview", currentPreview, signal);
    const preview = await settle(workspace, "review.refreshArchive", currentPreview, signal);
    if (preview === null) return { error: UNSETTLED };
    if (preview.status === "failure") return { error: failureText(preview.failure) };
    return {
      result: {
        filename: preview.value.filename,
        note: "Ready -- a Download button for it appears in the chat right here, and the Bundle card on the right has the same file too (browsers won't let a chat action save it automatically, a real click is needed either way).",
      },
    };
  }

  return { error: `Unknown DFT Workbench tool: ${tool}` };
}

// The archives chat Download cards save, by card id, for this page's
// lifetime: each card saves the archive its own dft_download_bundle call
// produced, not whatever the workspace holds by the time it's clicked (a
// later settings change replaces that). A card restored after a reload finds
// nothing here.
const bundles = new Map<string, ArchiveDownload>();

// Keeps the current draft's archive for a Download card, right after a
// successful dft_download_bundle. Returns the card's id, or null if there is
// no archive to keep.
export function keepBundle(workspace: Workspace): string | null {
  const archive = currentArchive(workspace.getSnapshot());
  if (archive === null) return null;
  const id = crypto.randomUUID();
  bundles.set(id, archive);
  return id;
}

// A Download card's click: saves the archive kept under `id`, or returns
// why there's none for the card to show.
export function saveBundle(
  id: string | undefined,
  save: (archive: ArchiveDownload) => void = saveArchiveToBrowser,
): string | null {
  const archive = id === undefined ? undefined : bundles.get(id);
  if (archive === undefined) {
    return "This bundle was generated before the page was reloaded, so it isn't held here any more. Ask for it again, or use the Bundle card in DFT Workbench.";
  }
  save(archive);
  return null;
}

// Rebuilds a persisted DFT Workbench draft as a `draft.patch` for the
// capabilities core reports now (#18): code, task and HPC profile only if
// core still offers them, and the overrides only if they were saved under the
// same vocabulary -- override keys and values change between vocabularies,
// and an override core rejects sticks to the draft. Fields are omitted rather
// than set to `undefined`: `draft.patch` applies any key that is present.
// States saved before `vocabulary` was recorded all came from vocabulary "1"
// builds.
export function restorableDraftPatch(
  saved: Pick<SavedDftState, "vocabulary" | "draft">,
  capabilities,
) {
  const draft = saved.draft;
  if (!draft) return null;
  const offered = (items: { id: string }[], id: string | null) =>
    items.some((item) => item.id === id);
  return {
    type: "draft.patch" as const,
    ...(offered(capabilities.codes, draft.code) ? { code: draft.code } : {}),
    ...(offered(capabilities.tasks, draft.task) ? { task: draft.task } : {}),
    ...(draft.hpc === null || offered(capabilities.hpc_profiles, draft.hpc)
      ? { hpc: draft.hpc }
      : {}),
    ...((saved.vocabulary ?? "1") === capabilities.vocabulary_version
      ? { overrides: draft.overrides }
      : {}),
  };
}

// What the page persists for the restore (App.tsx's save listener).
export type SavedDftState = {
  vocabulary?: string;
  structureInput: unknown;
  draft?: Pick<CalculationDraft, "code" | "task" | "hpc" | "overrides"> | null;
};

export type RestoreOutcome = "restored" | "skipped" | "kept" | "forgotten";

// The /inspect refusals of the input itself: a structure core can't read, or
// a request document it rejects. Anything else (core unreachable or failing,
// a frontend/backend mismatch) says nothing about the saved structure.
const PERMANENT_INSPECT_FAILURES = new Set(["invalid_structure", "invalid_request"]);

// Replays a persisted DFT Workbench state into `workspace` after a reload
// (#18) -- a real re-run of inspect/review/preview against goldilocks-core,
// since the Workspace has no "just set the state" action. Starts core first
// (`ensureCoreServerReady`, null once it's up): after a reload nothing else
// may have asked for it yet. Only replays into an untouched workspace, so a
// structure a chat DFT tool or the panel opened while this waited for core
// is kept ("skipped"). If core refuses the saved input itself
// (`PERMANENT_INSPECT_FAILURES`) it is forgotten (`forget`); any other
// failure keeps it for the next reload, and is dismissed rather than left as
// a Retry banner -- Retry would reopen the structure with default settings,
// which the save listener would then write over the saved ones ("kept").
export async function restoreDftWorkspace(
  workspace: Workspace,
  saved: SavedDftState,
  ensureCoreServerReady: () => Promise<string | null>,
  forget: () => void,
): Promise<RestoreOutcome> {
  if (await ensureCoreServerReady()) return "skipped";
  await ensureWorkspaceStarted(workspace);
  const before = workspace.getSnapshot();
  if (
    before.capabilities === null ||
    before.structureInput !== null ||
    before.inspectFailure !== null ||
    before.operation !== null
  ) return "skipped";
  await workspace.dispatch({ type: "source.open", input: saved.structureInput });
  const opened = workspace.getSnapshot();
  if (opened.structureInput !== saved.structureInput) {
    const failed = opened.inspectFailure;
    if (failed === null || failed.input !== saved.structureInput) return "skipped";
    if (PERMANENT_INSPECT_FAILURES.has(failed.failure.kind)) {
      forget();
      return "forgotten";
    }
    await workspace.dispatch({ type: "failure.dismiss", operation: "inspect" });
    return "kept";
  }
  const patch = restorableDraftPatch(saved, before.capabilities);
  if (patch) await workspace.dispatch(patch);
  await workspace.dispatch({ type: "review.compute" });
  await workspace.dispatch({ type: "review.refreshArchive" });
  return "restored";
}
