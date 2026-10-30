// DFT Workbench's chat-tool executor and reload restore: everything that
// drives the embedded goldilocks-workbench `Workspace` on the model's or the
// page's behalf (graph.py's CLIENT_EXECUTED_TOOLS, #18's restore). Kept out
// of App.tsx so its use of the `Workspace` and snapshot types is
// type-checked, and so it is tested against a real `createWorkspace` with
// recorded core responses (dftWorkbench.test.ts). The API payload types
// themselves (capabilities, structure input, task) aren't checked: the
// vendored tarball ships without the generated schema types they come from.

import type { CalculationDraft, Workspace } from "goldilocks-workbench";

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

function coreFailureMessage(failure: { kind: string; message: string }): string {
  return `${failure.kind}: ${failure.message}`;
}

const NO_STRUCTURE =
  "No structure has been opened in DFT Workbench yet -- call dft_open_structure first.";

// Executes a client-executed DFT Workbench tool call by dispatching into the
// exact same `workspace` the embedded panel's own buttons use -- one shared
// state object, not a second implementation calling goldilocks-core's HTTP
// API from Python. Pre-checks preconditions itself: `workspace.ts`'s actions
// silently no-op on an unmet precondition rather than throwing, which would
// otherwise be indistinguishable from "nothing to report."
// `ensureCoreServerReady` resolves to null once goldilocks-core is up, or to
// why it isn't.
export async function dispatchDftTool(
  workspace: Workspace,
  ensureCoreServerReady: (signal?: AbortSignal) => Promise<string | null>,
  tool: string,
  args: Record<string, any>,
  signal?: AbortSignal,
): Promise<DftToolOutcome> {
  const readyError = await ensureCoreServerReady(signal);
  if (readyError) return { error: readyError };
  await waitForWorkspaceIdle(workspace, signal);
  await untilAborted(ensureWorkspaceStarted(workspace), signal);

  if (tool === "dft_open_structure") {
    await untilAborted(workspace.dispatch({
      type: "source.open",
      input: {
        structure_content: args.structure_content,
        structure_name: args.structure_name,
        structure_format: args.structure_format
          ?? (args.structure_name?.toLowerCase().endsWith(".cif") ? "cif" : "poscar"),
      },
    }), signal);
    const snapshot = workspace.getSnapshot();
    if (snapshot.failure) return { error: coreFailureMessage(snapshot.failure) };
    return { result: snapshot.inspection ?? {} };
  }

  if (tool === "dft_review") {
    if (workspace.getSnapshot().structureInput === null) return { error: NO_STRUCTURE };
    await untilAborted(workspace.dispatch({ type: "review.compute" }), signal);
    const snapshot = workspace.getSnapshot();
    if (snapshot.failure) return { error: coreFailureMessage(snapshot.failure) };
    return { result: snapshot.reviewed ?? {} };
  }

  if (tool === "dft_download_bundle") {
    const before = workspace.getSnapshot();
    if (before.structureInput === null) return { error: NO_STRUCTURE };
    if (before.reviewed === null) {
      return { error: "No review has been run yet -- call dft_review first." };
    }
    if (before.outOfDate) await untilAborted(workspace.dispatch({ type: "review.compute" }), signal);
    // Deliberately `review.refreshArchive`, not `review.download`: the
    // latter ends in a real `saveArchiveToBrowser` -- an `<a>` click --
    // which browsers silently drop unless it happens inside a *direct*
    // user gesture's call stack. By the time this runs (an SSE event
    // from the server, several `await`s past whatever click sent the
    // chat message or approved the confirmation card), that gesture has
    // long expired -- confirmed live: no file ever reached ~/Downloads.
    // `refreshArchive` does the exact same server-side generation and
    // fills `lastDownload` (so BundleCard shows "ready") without ever
    // touching the DOM -- the user's own click on BundleCard's real
    // "Download (.zip)" button then reuses that cached archive and
    // *is* a direct gesture, so the browser actually saves it.
    await untilAborted(workspace.dispatch({ type: "review.refreshArchive" }), signal);
    const snapshot = workspace.getSnapshot();
    if (snapshot.failure) return { error: coreFailureMessage(snapshot.failure) };
    if (snapshot.lastDownload === null) {
      return { error: "Failed to generate the DFT input bundle." };
    }
    return {
      result: {
        filename: snapshot.lastDownload.filename,
        note: "Ready -- a Download button for it appears in the chat right here, and the Bundle card on the right has the same file too (browsers won't let a chat action save it automatically, a real click is needed either way).",
      },
    };
  }

  return { error: `Unknown DFT Workbench tool: ${tool}` };
}

// The dft-bundle-ready chat card's own button -- a *direct* click, unlike
// the automatic dispatch inside `dispatchDftTool`, so the browser actually
// allows the resulting `saveArchiveToBrowser` call. Reuses the
// already-cached `lastDownload` (set by `dispatchDftTool`'s
// `review.refreshArchive`) instead of regenerating it -- same
// `downloadReviewed()` path the Bundle card's own button already goes
// through. Returns why nothing can be saved instead, for the card to show
// (#18): `review.download` itself silently does nothing with no structure
// open (e.g. a card rehydrated after a reload, before the restore has
// finished), with settings changed since, while the bundle is still being
// generated, or when there is no review to generate it from.
export function downloadDftBundle(workspace: Workspace): string | null {
  const snapshot = workspace.getSnapshot();
  if (snapshot.structureInput === null) {
    return "Nothing to download yet: DFT Workbench has no structure open. If the page was just reloaded, wait for DFT Workbench to finish restoring, or ask for the bundle again.";
  }
  if (snapshot.outOfDate) {
    return "The settings changed since this bundle was generated. Ask for it again, or use the Bundle card in DFT Workbench.";
  }
  if (snapshot.lastDownload === null && snapshot.operation !== null) {
    return "DFT Workbench is still preparing this bundle -- try again in a moment.";
  }
  if (snapshot.lastDownload === null && snapshot.reviewed === null) {
    return "There is no bundle to save right now. Ask for it again, or use the Bundle card in DFT Workbench.";
  }
  void workspace.dispatch({ type: "review.download" });
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

// Replays a persisted DFT Workbench state into `workspace` after a reload
// (#18) -- a real re-run of inspect/review/generate against goldilocks-core,
// since the Workspace has no "just set the state" action. Starts core first
// (`ensureCoreServerReady`, null once it's up): after a reload nothing else
// may have asked for it yet. Only replays into an untouched workspace, so a
// structure a chat DFT tool or the panel opened while this waited for core
// is kept ("skipped"). If core rejects the saved structure for good it is
// forgotten (`forget`); a transient failure keeps it for the next reload,
// and its failure is dismissed rather than left as a Retry banner -- Retry
// would reopen the structure with default settings, which the save
// listener would then write over the saved ones ("kept").
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
    before.attemptedStructureInput !== null ||
    before.operation !== null
  ) return "skipped";
  await workspace.dispatch({ type: "source.open", input: saved.structureInput });
  const opened = workspace.getSnapshot();
  if (opened.structureInput !== saved.structureInput) {
    if (opened.attemptedStructureInput !== saved.structureInput || !opened.failure) return "skipped";
    if (!opened.failure.retryable) {
      forget();
      return "forgotten";
    }
    await workspace.dispatch({ type: "failure.dismiss" });
    return "kept";
  }
  const patch = restorableDraftPatch(saved, before.capabilities);
  if (patch) await workspace.dispatch(patch);
  await workspace.dispatch({ type: "review.compute" });
  await workspace.dispatch({ type: "review.refreshArchive" });
  return "restored";
}
