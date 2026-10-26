// The DFT Workbench chat tools and reload restore against a real goldilocks-workbench
// `createWorkspace` + `HttpCoreClient`, with core itself replaced by
// responses recorded from a real goldilocks-core
// (scripts/record-core-fixtures.mjs). The vendored client refuses fixtures
// from any other vocabulary than its own, so bumping the tarball without
// re-recording them fails here rather than in the browser.

import { HttpCoreClient, createWorkspace } from "goldilocks-workbench";
import { describe, expect, it, vi } from "vitest";
import capabilities from "./__fixtures__/core/capabilities.json";
import explanation from "./__fixtures__/core/explain.json";
import inspection from "./__fixtures__/core/inspect.json";
import structure from "./__fixtures__/core/structure.json";
import {
  dispatchDftTool,
  downloadDftBundle,
  restorableDraftPatch,
  restoreDftWorkspace,
} from "./dftWorkbench";

type Route = (body: any) => Response;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const refusal = (status: number, kind: string, message: string) =>
  json(status, { error: { kind, message, details: {} } });

// A fake core serving the recorded responses (plus any per-test override);
// any other request fails the test loudly.
function fakeCore(overrides: Record<string, Route> = {}) {
  const routes: Record<string, Route> = {
    "GET /capabilities": () => json(200, capabilities),
    "POST /inspect": () => json(200, inspection),
    "POST /explain": () => json(200, explanation),
    "POST /run": (body) =>
      body.respond_with === "archive"
        ? new Response(new Blob(["PK fake archive"]), { status: 200, headers: { "content-type": "application/zip" } })
        : json(200, {}),
    ...overrides,
  };
  const requests: { method: string; path: string; body: any }[] = [];
  const fetcher = (async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const path = new URL(url, "http://core.test").pathname;
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    requests.push({ method, path, body });
    const route = routes[`${method} ${path}`];
    if (!route) throw new Error(`unexpected core request: ${method} ${path}`);
    return route(body);
  }) as typeof fetch;
  const saved: { filename: string }[] = [];
  const workspace = createWorkspace(new HttpCoreClient("", fetcher), (archive) => saved.push(archive));
  return { workspace, requests, saved };
}

const coreUp = async () => null;
const openArgs = { structure_content: structure.structure_content, structure_name: "Si.cif" };
const archiveName = `Si.cif-${capabilities.tasks[0].id}.zip`;

describe("dispatchDftTool", () => {
  it("stops before touching core while the backend isn't up", async () => {
    const { workspace, requests } = fakeCore();
    const outcome = await dispatchDftTool(
      workspace, async () => "goldilocks-core is still starting", "dft_review", {},
    );
    expect(outcome).toEqual({ error: "goldilocks-core is still starting" });
    expect(requests).toEqual([]);
  });

  it("dft_open_structure returns core's inspection of the structure", async () => {
    const { workspace, requests } = fakeCore();
    const outcome = await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    expect(outcome).toEqual({ result: inspection });
    expect(requests.map((r) => `${r.method} ${r.path}`)).toEqual(["GET /capabilities", "POST /inspect"]);
    // Format inferred from the name when the model leaves it out.
    expect(requests[1].body).toMatchObject({ structure_name: "Si.cif", structure_format: "cif" });
  });

  it("a structure core refuses becomes the tool's error", async () => {
    const { workspace } = fakeCore({
      "POST /inspect": () => refusal(422, "invalid_structure", "cannot parse the CIF"),
    });
    const outcome = await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    expect(outcome).toEqual({ error: "invalid_structure: cannot parse the CIF" });
  });

  it("dft_review needs an open structure, then returns core's explanation", async () => {
    const { workspace, requests } = fakeCore();
    expect(await dispatchDftTool(workspace, coreUp, "dft_review", {})).toEqual({
      error: expect.stringContaining("call dft_open_structure first"),
    });

    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    const outcome = await dispatchDftTool(workspace, coreUp, "dft_review", {});

    expect(outcome).toEqual({ result: explanation });
    expect(requests.at(-1)).toMatchObject({ method: "POST", path: "/explain" });
  });

  it("dft_download_bundle generates the archive but leaves saving it to a click", async () => {
    const { workspace, saved } = fakeCore();
    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    expect(await dispatchDftTool(workspace, coreUp, "dft_download_bundle", {})).toEqual({
      error: expect.stringContaining("call dft_review first"),
    });

    await dispatchDftTool(workspace, coreUp, "dft_review", {});
    const outcome = await dispatchDftTool(workspace, coreUp, "dft_download_bundle", {});

    expect(outcome).toMatchObject({ result: { filename: archiveName } });
    // Browsers drop a save outside a direct user gesture, so the tool never
    // attempts one.
    expect(saved).toEqual([]);
  });

  it("names an unknown tool instead of guessing", async () => {
    const { workspace } = fakeCore();
    expect(await dispatchDftTool(workspace, coreUp, "dft_teleport", {})).toEqual({
      error: "Unknown DFT Workbench tool: dft_teleport",
    });
  });
});

describe("downloadDftBundle", () => {
  it("says why nothing can be saved, and saves the generated archive otherwise", async () => {
    const { workspace, saved } = fakeCore();
    expect(downloadDftBundle(workspace)).toMatch(/^Nothing to download yet/);

    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatchDftTool(workspace, coreUp, "dft_review", {});
    await dispatchDftTool(workspace, coreUp, "dft_download_bundle", {});

    expect(downloadDftBundle(workspace)).toBeNull();
    await vi.waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].filename).toBe(archiveName);
  });

  it("says the bundle is still being prepared while core works on it", async () => {
    const { workspace } = fakeCore({ "POST /explain": () => new Promise<Response>(() => {}) as any });
    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    void workspace.dispatch({ type: "review.compute" });

    expect(downloadDftBundle(workspace)).toMatch(/still preparing/);
  });

  it("says there is nothing to save when the review failed", async () => {
    const { workspace } = fakeCore({ "POST /explain": () => refusal(500, "internal", "boom") });
    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatchDftTool(workspace, coreUp, "dft_review", {});

    expect(downloadDftBundle(workspace)).toMatch(/^There is no bundle to save right now/);
  });

  it("says the bundle is stale once the settings change", async () => {
    const { workspace } = fakeCore();
    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatchDftTool(workspace, coreUp, "dft_review", {});
    await workspace.dispatch({ type: "draft.patch", overrides: { ecutwfc_ry: 60 } });

    expect(downloadDftBundle(workspace)).toMatch(/^The settings changed/);
  });
});

describe("restorableDraftPatch", () => {
  const draft = { code: "quantum_espresso", task: "relax", hpc: "scarf", overrides: { ecutwfc_ry: 60 } };

  it("restores everything core still offers under the same vocabulary", () => {
    expect(restorableDraftPatch({ vocabulary: capabilities.vocabulary_version, draft }, capabilities)).toEqual({
      type: "draft.patch", ...draft,
    });
  });

  it("drops overrides saved under another vocabulary", () => {
    const patch = restorableDraftPatch({ vocabulary: "0", draft }, capabilities);
    expect(patch).toEqual({ type: "draft.patch", code: draft.code, task: draft.task, hpc: draft.hpc });
  });

  it("counts a state saved before `vocabulary` existed as vocabulary 1", () => {
    const patch = restorableDraftPatch({ draft }, { ...capabilities, vocabulary_version: "1" });
    expect(patch).toHaveProperty("overrides", draft.overrides);
    // ...not as whatever core speaks now, which is what matters at a bump.
    const bumped = restorableDraftPatch({ draft }, { ...capabilities, vocabulary_version: "2" });
    expect(bumped).not.toHaveProperty("overrides");
  });

  it("omits a task or HPC profile core no longer offers rather than sending undefined", () => {
    // `draft.patch` applies any key that is present, so `task: undefined`
    // would blank the task instead of keeping core's default.
    const patch = restorableDraftPatch(
      { vocabulary: capabilities.vocabulary_version, draft: { ...draft, task: "band", hpc: "archer2" } },
      capabilities,
    );
    expect(patch).not.toHaveProperty("task");
    expect(patch).not.toHaveProperty("hpc");
  });

  it("keeps an explicit `hpc: null` (no HPC profile)", () => {
    const patch = restorableDraftPatch(
      { vocabulary: capabilities.vocabulary_version, draft: { ...draft, hpc: null } },
      capabilities,
    );
    expect(patch).toHaveProperty("hpc", null);
  });

  it("restores nothing without a saved draft", () => {
    expect(restorableDraftPatch({ vocabulary: "1" }, capabilities)).toBeNull();
  });
});

describe("restoreDftWorkspace", () => {
  const draft = { code: "quantum_espresso", task: "relax", hpc: "scarf", overrides: { ecutwfc_ry: 60 } };
  const savedState = () => ({
    vocabulary: capabilities.vocabulary_version,
    structureInput: { ...structure },
    draft,
  });

  it("restores the structure, its settings and the bundle once core is up", async () => {
    const { workspace, requests } = fakeCore();
    const forget = vi.fn();
    const saved = savedState();

    expect(await restoreDftWorkspace(workspace, saved, coreUp, forget)).toBe("restored");

    expect(workspace.getSnapshot().structureInput).toBe(saved.structureInput);
    expect(requests.find((r) => r.path === "/explain")?.body).toMatchObject({
      task: "relax", hpc: "scarf", overrides: { ecutwfc_ry: 60 },
    });
    expect(workspace.getSnapshot().lastDownload?.filename).toBe("Si.cif-relax.zip");
    expect(forget).not.toHaveBeenCalled();
  });

  it("does nothing until core is up", async () => {
    const { workspace, requests } = fakeCore();
    const forget = vi.fn();
    const outcome = await restoreDftWorkspace(workspace, savedState(), async () => "not up", forget);
    expect(outcome).toBe("skipped");
    expect(requests).toEqual([]);
    expect(forget).not.toHaveBeenCalled();
  });

  it("leaves a structure opened while it waited alone", async () => {
    const { workspace } = fakeCore();
    await dispatchDftTool(workspace, coreUp, "dft_open_structure", openArgs);
    const opened = workspace.getSnapshot().structureInput;
    const forget = vi.fn();

    expect(await restoreDftWorkspace(workspace, savedState(), coreUp, forget)).toBe("skipped");

    expect(workspace.getSnapshot().structureInput).toBe(opened);
    expect(forget).not.toHaveBeenCalled();
  });

  it("forgets a structure core rejects for good", async () => {
    const { workspace } = fakeCore({
      "POST /inspect": () => refusal(422, "invalid_structure", "cannot parse the CIF"),
    });
    const forget = vi.fn();
    expect(await restoreDftWorkspace(workspace, savedState(), coreUp, forget)).toBe("forgotten");
    expect(forget).toHaveBeenCalledOnce();
  });

  it("keeps the saved state after a transient failure, without a Retry banner", async () => {
    // Retry would reopen the structure with default settings, which the save
    // listener would write over the saved ones.
    const { workspace } = fakeCore({
      "POST /inspect": () => refusal(502, "core_unavailable", "still starting"),
    });
    const forget = vi.fn();
    expect(await restoreDftWorkspace(workspace, savedState(), coreUp, forget)).toBe("kept");
    expect(forget).not.toHaveBeenCalled();
    expect(workspace.getSnapshot().failure).toBeNull();
  });
});
