// DFT Workbench drafts kept on the agent server, one per chat (#142). Each
// chat gets its own goldilocks-workbench `Workspace`, replayed from the chat's
// server-side draft when it is first needed, and every change to its draft --
// a panel edit, a structure opened, a chat tool -- is saved back. The
// Workspace stays the package's own: it still computes the review and preview
// through the agent's proxy to core, as a view of the draft. Replaces the one
// Workspace every chat shared (#13) and its localStorage restore (#18).

import type { CalculationDraft, Workspace, WorkspaceSnapshot } from "goldilocks-workbench";
import { ensureWorkspaceStarted, waitForWorkspaceIdle } from "./dftWorkbench";

type Capabilities = NonNullable<WorkspaceSnapshot["capabilities"]>;

type StructureInput = {
  structure_content: string;
  structure_name: string;
  structure_format?: string | null;
};

// A draft as the server keeps it (dft_draft.py's `DftDraft`): the open
// structure, the calculation, and the vocabulary its overrides were set
// under -- override keys and values change between vocabularies.
export type DraftFields = {
  vocabulary: string | null;
  structure: StructureInput | null;
  code: string;
  task: string;
  hpc: string | null;
  overrides: Record<string, unknown>;
};

export type ServerDraft = DraftFields & { revision: number };

export type DraftApi = {
  get(chatId: string): Promise<ServerDraft | null>;
  // `saved` false: the server's draft moved past `baseRevision`; `draft` is
  // the one there.
  put(chatId: string, baseRevision: number, draft: DraftFields): Promise<{ saved: boolean; draft: ServerDraft }>;
};

// The agent server's /api/chat/{id}/dft/draft.
export function httpDraftApi(fetcher: typeof fetch = (...args) => fetch(...args)): DraftApi {
  const url = (chatId: string) => `/api/chat/${encodeURIComponent(chatId)}/dft/draft`;
  return {
    async get(chatId) {
      const response = await fetcher(url(chatId));
      if (!response.ok) throw new Error(`Loading the DFT Workbench draft failed: ${response.status}`);
      return (await response.json()).draft;
    },
    async put(chatId, baseRevision, draft) {
      const response = await fetcher(url(chatId), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_revision: baseRevision, draft }),
      });
      if (response.status !== 409 && !response.ok) {
        throw new Error(`Saving the DFT Workbench draft failed: ${response.status}`);
      }
      return { saved: response.ok, draft: (await response.json()).draft };
    },
  };
}

// The draft `snapshot` holds, as the server keeps it; null before core's
// capabilities (and so a draft) are there.
export function draftFields(snapshot: WorkspaceSnapshot): DraftFields | null {
  const { capabilities, draft, structureInput } = snapshot;
  if (capabilities === null || draft === null) return null;
  const input = structureInput as StructureInput | null;
  return {
    vocabulary: capabilities.vocabulary_version,
    structure: input && {
      structure_content: input.structure_content,
      structure_name: input.structure_name,
      ...(input.structure_format ? { structure_format: input.structure_format } : {}),
    },
    code: draft.code,
    task: draft.task,
    hpc: draft.hpc,
    overrides: { ...draft.overrides },
  };
}

function sameStructure(open: unknown, structure: StructureInput): boolean {
  const input = open as StructureInput | null;
  return (
    input !== null &&
    input.structure_content === structure.structure_content &&
    input.structure_name === structure.structure_name &&
    (input.structure_format ?? null) === (structure.structure_format ?? null)
  );
}

// `draft` as a `draft.patch` for the capabilities core reports now: code,
// task and HPC profile only if core still offers them, and the overrides only
// if they were set under the same vocabulary -- an override core rejects
// sticks to the draft. Fields core no longer offers are omitted rather than
// set to `undefined`: `draft.patch` applies any key that is present. With
// `current`, the overrides replace its own, so keys the draft lacks go back
// to automatic.
export function draftPatch(
  draft: Pick<DraftFields, "vocabulary" | "code" | "task" | "hpc" | "overrides">,
  capabilities: Capabilities,
  current?: Pick<CalculationDraft, "overrides"> | null,
) {
  const offered = (items: readonly { id: string }[], id: string | null) => items.some((item) => item.id === id);
  const cleared = Object.fromEntries(Object.keys(current?.overrides ?? {}).map((key) => [key, undefined]));
  return {
    type: "draft.patch" as const,
    ...(offered(capabilities.codes, draft.code) ? { code: draft.code } : {}),
    ...(offered(capabilities.tasks, draft.task) ? { task: draft.task } : {}),
    ...(draft.hpc === null || offered(capabilities.hpc_profiles, draft.hpc) ? { hpc: draft.hpc } : {}),
    ...(draft.vocabulary === capabilities.vocabulary_version ? { overrides: { ...cleared, ...draft.overrides } } : {}),
  };
}

export type HydrateOutcome = "restored" | "skipped" | "failed";

// The /inspect refusals of the input itself: a structure core can't read, or
// a request document it rejects. Anything else (core unreachable or failing,
// a frontend/backend mismatch) says nothing about the saved structure.
const PERMANENT_INSPECT_FAILURES = new Set(["invalid_structure", "invalid_request"]);

// Replays `draft` into a workspace nothing has used yet -- a real inspect of
// its structure, since the Workspace has no "just set the state" action --
// then its calculation. Leaves a workspace already in use alone ("skipped"):
// the user may have opened a structure while core was starting. A structure
// core can't open now fails; unless core refuses the input itself, the
// failure is dismissed rather than left as a Retry banner, since Retry would
// reopen it with default settings.
export async function hydrateWorkspace(workspace: Workspace, draft: DraftFields): Promise<HydrateOutcome> {
  await ensureWorkspaceStarted(workspace);
  const before = workspace.getSnapshot();
  if (
    before.capabilities === null ||
    before.structureInput !== null ||
    before.inspectFailure !== null ||
    before.operation !== null
  ) return "skipped";
  if (draft.structure) {
    const input = draft.structure;
    await workspace.dispatch({ type: "source.open", input });
    const opened = workspace.getSnapshot();
    if (opened.structureInput !== input) {
      const failed = opened.inspectFailure;
      if (failed?.input === input && !PERMANENT_INSPECT_FAILURES.has(failed.failure.kind)) {
        await workspace.dispatch({ type: "failure.dismiss", operation: "inspect" });
      }
      return "failed";
    }
  }
  await workspace.dispatch(draftPatch(draft, before.capabilities, workspace.getSnapshot().draft));
  return "restored";
}

// Brings a workspace in use to `draft`: opens its structure if another is
// open, then sets its calculation, overrides included.
export async function applyDraft(workspace: Workspace, draft: DraftFields): Promise<void> {
  await waitForWorkspaceIdle(workspace);
  const capabilities = workspace.getSnapshot().capabilities;
  if (capabilities === null) return;
  if (draft.structure && !sameStructure(workspace.getSnapshot().structureInput, draft.structure)) {
    const input = draft.structure;
    await workspace.dispatch({ type: "source.open", input });
    if (workspace.getSnapshot().structureInput !== input) return;
  }
  await workspace.dispatch(draftPatch(draft, capabilities, workspace.getSnapshot().draft));
}

type Entry = {
  workspace: Workspace;
  // The server revision this tab's draft is based on (0: none there yet).
  revision: number;
  // The draft last known to match the server's, as JSON.
  synced: string | null;
  // Replaying a server draft: its changes aren't saved back. Defensive:
  // today a replay's changes all land before a save timer can fire, and the
  // replay then marks the result synced.
  applying: boolean;
  timer: ReturnType<typeof setTimeout> | null;
  saving: Promise<void> | null;
  ready: Promise<void>;
  unsubscribe: () => void;
};

export type ChatWorkspacesOptions = {
  createWorkspace: () => Workspace;
  api: DraftApi;
  // Null once goldilocks-core is up, else why not.
  ensureCoreServerReady: () => Promise<string | null>;
  // How long a burst of edits settles before it is saved.
  syncDelayMs?: number;
};

// One goldilocks-workbench Workspace per chat, kept in step with the chat's
// draft on the agent server. A chat's workspace is made the first time it is
// asked for -- when its panel shows, or a chat tool acts on it -- so a chat
// whose DFT Workbench is never used costs nothing.
export class ChatWorkspaces {
  private readonly entries = new Map<string, Entry>();
  private scratch: Workspace | null = null;

  constructor(private readonly options: ChatWorkspacesOptions) {}

  // `chatId`'s workspace; null (no chat yet) gets one that isn't saved.
  workspaceFor(chatId: string | null): Workspace {
    if (chatId === null) return (this.scratch ??= this.options.createWorkspace());
    return this.entry(chatId).workspace;
  }

  // Resolves once `chatId`'s workspace holds its server draft (or found none).
  ready(chatId: string): Promise<void> {
    return this.entry(chatId).ready;
  }

  // Saves a pending change to `chatId`'s draft now.
  async flush(chatId: string): Promise<void> {
    const entry = this.entries.get(chatId);
    if (!entry) return;
    if (entry.timer !== null) {
      clearTimeout(entry.timer);
      entry.timer = null;
      await this.save(chatId, entry);
    }
    await entry.saving;
  }

  // Drops a deleted chat's workspace.
  forget(chatId: string): void {
    const entry = this.entries.get(chatId);
    if (!entry) return;
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.unsubscribe();
    this.entries.delete(chatId);
  }

  private entry(chatId: string): Entry {
    const existing = this.entries.get(chatId);
    if (existing) return existing;
    const workspace = this.options.createWorkspace();
    const entry: Entry = {
      workspace,
      revision: 0,
      synced: null,
      applying: false,
      timer: null,
      saving: null,
      ready: Promise.resolve(),
      unsubscribe: () => {},
    };
    this.entries.set(chatId, entry);
    entry.unsubscribe = workspace.subscribe(() => this.changed(chatId, entry));
    entry.ready = this.hydrate(chatId, entry);
    return entry;
  }

  private async hydrate(chatId: string, entry: Entry): Promise<void> {
    let draft: ServerDraft | null;
    try {
      draft = await this.options.api.get(chatId);
    } catch {
      // Starts empty; a later save then finds the server's draft (409).
      return;
    }
    if (draft === null) return;
    entry.revision = draft.revision;
    // Core may take long to start the first time; edits meanwhile are saved
    // as usual, and the replay then leaves the workspace alone.
    if (await this.options.ensureCoreServerReady()) return;
    await this.replay(entry, () => hydrateWorkspace(entry.workspace, draft));
  }

  private async replay(entry: Entry, run: () => Promise<unknown>): Promise<void> {
    entry.applying = true;
    try {
      await run();
    } finally {
      entry.applying = false;
      entry.synced = JSON.stringify(draftFields(entry.workspace.getSnapshot()));
    }
  }

  // Only a draft with a structure is saved: without one the panel changes
  // nothing, and every chat that merely showed it would save its defaults.
  private pending(entry: Entry): DraftFields | null {
    const fields = draftFields(entry.workspace.getSnapshot());
    if (fields?.structure == null || JSON.stringify(fields) === entry.synced) return null;
    return fields;
  }

  private changed(chatId: string, entry: Entry): void {
    if (entry.applying || this.pending(entry) === null) return;
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
      entry.timer = null;
      void this.save(chatId, entry);
    }, this.options.syncDelayMs ?? 300);
  }

  // One save at a time per chat; a change made while one is in flight is
  // saved after it, from the revision it returns.
  private async save(chatId: string, entry: Entry): Promise<void> {
    while (entry.saving) await entry.saving;
    const fields = this.pending(entry);
    if (fields === null) return;
    entry.saving = (async () => {
      try {
        const result = await this.options.api.put(chatId, entry.revision, fields);
        entry.revision = result.draft.revision;
        if (result.saved) entry.synced = JSON.stringify(fields);
        // Someone else wrote first: theirs stands, and this tab replays it.
        else await this.replay(entry, () => applyDraft(entry.workspace, result.draft));
      } catch {
        // Not saved; the next change tries again.
      } finally {
        entry.saving = null;
      }
    })();
    await entry.saving;
    if (this.pending(entry) !== null) this.changed(chatId, entry);
  }
}
