// A chat's DFT Workbench draft on the agent server and its Workspace in the
// page (#142), against real goldilocks-workbench workspaces with core replaced
// by its recorded responses and the server by an in-memory one that keeps its
// revision rule (a write from an older revision gets the newer draft back).

import { HttpCoreClient, createWorkspace } from "goldilocks-workbench";
import { describe, expect, it } from "vitest";
import capabilities from "./__fixtures__/core/capabilities.json";
import explanation from "./__fixtures__/core/explain.json";
import inspection from "./__fixtures__/core/inspect.json";
import structure from "./__fixtures__/core/structure.json";
import {
  ChatWorkspaces,
  type DraftApi,
  type DraftFields,
  type ServerDraft,
  draftFields,
  draftPatch,
  hydrateWorkspace,
} from "./dftDraftSync";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const refusal = (status: number, kind: string, message: string) =>
  json(status, { error: { kind, message, details: {} } });

// Workspaces sharing one fake core; `inspectWith` overrides /inspect.
function fakeCore(inspectWith?: () => Response) {
  const requests: string[] = [];
  const fetcher = (async (url: string, init: RequestInit = {}) => {
    const path = new URL(url, "http://core.test").pathname;
    requests.push(`${init.method ?? "GET"} ${path}`);
    if (path === "/capabilities") return json(200, capabilities);
    if (path === "/inspect") return inspectWith ? inspectWith() : json(200, inspection);
    if (path === "/explain") return json(200, explanation);
    throw new Error(`unexpected core request: ${path}`);
  }) as typeof fetch;
  return { create: () => createWorkspace(new HttpCoreClient("", fetcher)), requests };
}

// The agent server's draft store, in memory, with its revision rule.
function fakeServer(initial: Record<string, ServerDraft> = {}) {
  const drafts = new Map(Object.entries(initial));
  const puts: { chatId: string; baseRevision: number; draft: DraftFields }[] = [];
  let release: (() => void) | null = null;
  let held: Promise<void> | null = null;
  const api: DraftApi = {
    async get(chatId) {
      return drafts.get(chatId) ?? null;
    },
    async put(chatId, baseRevision, draft) {
      puts.push({ chatId, baseRevision, draft });
      if (held) await held;
      const current = drafts.get(chatId);
      if ((current?.revision ?? 0) !== baseRevision) return { saved: false, draft: current! };
      const next = { ...draft, revision: baseRevision + 1 };
      drafts.set(chatId, next);
      return { saved: true, draft: next };
    },
  };
  return {
    api,
    drafts,
    puts,
    // Holds the next saves in flight until `release()`.
    hold() {
      held = new Promise((resolve) => (release = resolve));
      return () => {
        held = null;
        release?.();
      };
    },
  };
}

const coreUp = async () => null;
const si = () => ({ ...structure });
const vocabulary = capabilities.vocabulary_version;
const siDraft = (revision: number, changes: Partial<DraftFields> = {}): ServerDraft => ({
  revision,
  vocabulary,
  structure: si(),
  code: "quantum_espresso",
  task: "relax",
  hpc: "scarf",
  overrides: { walltime_h: 24 },
  ...changes,
});

function chats(server: ReturnType<typeof fakeServer>, core = fakeCore(), ready = coreUp) {
  return new ChatWorkspaces({
    createWorkspace: core.create,
    api: server.api,
    ensureCoreServerReady: ready,
    syncDelayMs: 0,
  });
}

describe("draftPatch", () => {
  // The recorded JSON, typed as the package's capabilities.
  const caps = capabilities as unknown as Parameters<typeof draftPatch>[1];
  const fields = { code: "quantum_espresso", task: "relax", hpc: "scarf", overrides: { ecutwfc_ry: 60 } };
  const draft = { vocabulary, ...fields };

  it("sets everything core still offers under the same vocabulary", () => {
    expect(draftPatch(draft, caps)).toEqual({ type: "draft.patch", ...fields });
  });

  it("drops overrides set under another vocabulary, or an unknown one", () => {
    expect(draftPatch({ ...draft, vocabulary: "0" }, caps)).not.toHaveProperty("overrides");
    expect(draftPatch({ ...draft, vocabulary: null }, caps)).not.toHaveProperty("overrides");
  });

  it("omits a task or HPC profile core no longer offers rather than sending undefined", () => {
    // `draft.patch` applies any key that is present, so `task: undefined`
    // would blank the task instead of keeping core's default.
    const patch = draftPatch({ ...draft, task: "md", hpc: "archer2" }, caps);
    expect(patch).not.toHaveProperty("task");
    expect(patch).not.toHaveProperty("hpc");
    expect(draftPatch({ ...draft, hpc: null }, caps)).toHaveProperty("hpc", null);
  });

  it("replaces the current overrides, putting the ones the draft lacks back to automatic", () => {
    const patch = draftPatch(draft, caps, { overrides: { k_grid: [4, 4, 4], ecutwfc_ry: 40 } });
    expect(patch.overrides).toEqual({ k_grid: undefined, ecutwfc_ry: 60 });
  });
});

describe("hydrateWorkspace", () => {
  it("opens the draft's structure, then sets its calculation", async () => {
    const workspace = fakeCore().create();
    const draft = siDraft(3);

    expect(await hydrateWorkspace(workspace, draft)).toBe("restored");

    const snapshot = workspace.getSnapshot();
    expect(snapshot.structureInput).toBe(draft.structure);
    expect(snapshot.draft).toMatchObject({ task: "relax", hpc: "scarf", overrides: { walltime_h: 24 } });
  });

  it("sets a draft without a structure on the default one", async () => {
    // The chat can set the task before any structure is open.
    const workspace = fakeCore().create();
    expect(await hydrateWorkspace(workspace, siDraft(1, { structure: null, task: "band" }))).toBe("restored");
    expect(workspace.getSnapshot().draft?.task).toBe("band");
    expect(workspace.getSnapshot().structureInput).toBeNull();
  });

  it("leaves a workspace in use alone", async () => {
    const workspace = fakeCore().create();
    await workspace.dispatch({ type: "workspace.start" });
    const opened = si();
    await workspace.dispatch({ type: "source.open", input: opened });

    expect(await hydrateWorkspace(workspace, siDraft(2, { task: "band" }))).toBe("skipped");

    expect(workspace.getSnapshot().structureInput).toBe(opened);
    expect(workspace.getSnapshot().draft?.task).toBe(capabilities.tasks[0].id);
  });

  it("dismisses a failure that says nothing about the structure, without a Retry banner", async () => {
    // Retry would reopen the structure with default settings, saved over the
    // chat's own.
    const workspace = fakeCore(() => refusal(503, "core_unavailable", "still starting")).create();
    expect(await hydrateWorkspace(workspace, siDraft(1))).toBe("failed");
    expect(workspace.getSnapshot().inspectFailure).toBeNull();
  });

  it("keeps the banner for a structure core can't read", async () => {
    const workspace = fakeCore(() => refusal(422, "invalid_structure", "cannot parse")).create();
    expect(await hydrateWorkspace(workspace, siDraft(1))).toBe("failed");
    expect(workspace.getSnapshot().inspectFailure?.failure.kind).toBe("invalid_structure");
  });
});

describe("ChatWorkspaces", () => {
  it("gives each chat its own workspace, replayed from its own draft", async () => {
    const server = fakeServer({ a: siDraft(4), b: siDraft(7, { task: "band", overrides: {} }) });
    const workspaces = chats(server);

    const a = workspaces.workspaceFor("a");
    const b = workspaces.workspaceFor("b");
    await Promise.all([workspaces.ready("a"), workspaces.ready("b")]);

    expect(a).not.toBe(b);
    expect(workspaces.workspaceFor("a")).toBe(a);
    expect(a.getSnapshot().draft).toMatchObject({ task: "relax", overrides: { walltime_h: 24 } });
    expect(b.getSnapshot().draft).toMatchObject({ task: "band", overrides: {} });
    // Replaying a draft doesn't save it back.
    expect(server.puts).toEqual([]);
  });

  it("saves a panel edit from the server's revision", async () => {
    const server = fakeServer({ a: siDraft(4) });
    const workspaces = chats(server);
    const workspace = workspaces.workspaceFor("a");
    await workspaces.ready("a");

    await workspace.dispatch({ type: "draft.patch", task: "phonon" });
    await workspaces.flush("a");

    expect(server.puts).toHaveLength(1);
    expect(server.puts[0]).toMatchObject({ chatId: "a", baseRevision: 4 });
    expect(server.drafts.get("a")).toMatchObject({ revision: 5, task: "phonon", structure: { structure_name: "Si.cif" } });
    expect(draftFields(workspace.getSnapshot())).toMatchObject({ vocabulary, task: "phonon" });
  });

  it("saves the first structure a chat opens, and nothing before", async () => {
    const server = fakeServer();
    const workspaces = chats(server);
    const workspace = workspaces.workspaceFor("new");
    await workspaces.ready("new");
    await workspace.dispatch({ type: "workspace.start" });
    await workspaces.flush("new");
    // A chat that only showed the panel has no draft to save.
    expect(server.puts).toEqual([]);

    await workspace.dispatch({ type: "source.open", input: si() });
    await workspaces.flush("new");

    expect(server.puts).toEqual([expect.objectContaining({ baseRevision: 0 })]);
    expect(server.drafts.get("new")?.revision).toBe(1);
  });

  it("replays the server's draft when another writer got there first", async () => {
    // e.g. the same chat open in another tab saved first.
    const server = fakeServer({ a: siDraft(4) });
    const workspaces = chats(server);
    const workspace = workspaces.workspaceFor("a");
    await workspaces.ready("a");
    server.drafts.set("a", siDraft(5, { task: "band", overrides: { k_grid: [8, 8, 8] } }));

    await workspace.dispatch({ type: "draft.patch", task: "phonon" });
    await workspaces.flush("a");

    expect(server.drafts.get("a")?.task).toBe("band");
    expect(workspace.getSnapshot().draft).toMatchObject({ task: "band", overrides: { k_grid: [8, 8, 8] } });
    // ...and saving goes on from its revision, without saving the replay.
    await workspace.dispatch({ type: "draft.patch", hpc: null });
    await workspaces.flush("a");
    expect(server.puts.at(-1)).toMatchObject({ baseRevision: 5 });
    expect(server.drafts.get("a")).toMatchObject({ revision: 6, task: "band", hpc: null });
  });

  it("saves an edit made while a save is in flight after it, from the revision it returned", async () => {
    const server = fakeServer({ a: siDraft(4) });
    const workspaces = chats(server);
    const workspace = workspaces.workspaceFor("a");
    await workspaces.ready("a");
    const release = server.hold();

    await workspace.dispatch({ type: "draft.patch", task: "phonon" });
    const first = workspaces.flush("a");
    await workspace.dispatch({ type: "draft.patch", overrides: { walltime_h: 6 } });
    release();
    await first;
    await workspaces.flush("a");

    expect(server.puts.map((put) => put.baseRevision)).toEqual([4, 5]);
    expect(server.drafts.get("a")).toMatchObject({ revision: 6, task: "phonon", overrides: { walltime_h: 6 } });
  });

  it("waits for core before replaying, and leaves alone a structure opened meanwhile", async () => {
    let up: (value: null) => void = () => {};
    const coreStarting = new Promise<null>((resolve) => (up = resolve));
    const server = fakeServer({ a: siDraft(4) });
    const core = fakeCore();
    const workspaces = chats(server, core, () => coreStarting);
    const workspace = workspaces.workspaceFor("a");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(core.requests).toEqual([]);

    await workspace.dispatch({ type: "workspace.start" });
    const opened = { ...si(), structure_name: "mine.cif" };
    await workspace.dispatch({ type: "source.open", input: opened });
    await workspaces.flush("a");
    up(null);
    await workspaces.ready("a");

    expect(workspace.getSnapshot().structureInput).toBe(opened);
    expect(server.drafts.get("a")?.structure?.structure_name).toBe("mine.cif");
  });

  it("keeps a chat with no chat id out of the server", async () => {
    const server = fakeServer();
    const workspaces = chats(server);
    const scratch = workspaces.workspaceFor(null);
    await scratch.dispatch({ type: "workspace.start" });
    await scratch.dispatch({ type: "source.open", input: si() });
    expect(workspaces.workspaceFor(null)).toBe(scratch);
    expect(server.puts).toEqual([]);
  });

  it("drops a deleted chat's workspace", async () => {
    const server = fakeServer({ a: siDraft(4) });
    const workspaces = chats(server);
    const first = workspaces.workspaceFor("a");
    await workspaces.ready("a");

    workspaces.forget("a");
    await first.dispatch({ type: "draft.patch", task: "band" });

    expect(workspaces.workspaceFor("a")).not.toBe(first);
    expect(server.puts).toEqual([]);
  });
});
