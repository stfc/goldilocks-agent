// The DFT Workbench chat tools and reload restore against a real goldilocks-workbench
// `createWorkspace` + `HttpCoreClient`, with core itself replaced by
// responses recorded from a real goldilocks-core
// (scripts/record-core-fixtures.mjs). The vendored client refuses fixtures
// from any other vocabulary than its own, so bumping the tarball without
// re-recording them fails here rather than in the browser.

import { HttpCoreClient, createWorkspace, currentArchive } from "goldilocks-workbench";
import { describe, expect, it, vi } from "vitest";
import { isDftReview, reviewRows } from "./dftReview";
import capabilities from "./__fixtures__/core/capabilities.json";
import blockedExplanation from "./__fixtures__/core/explain-blocked.json";
import explanation from "./__fixtures__/core/explain.json";
import inspection from "./__fixtures__/core/inspect.json";
import structure from "./__fixtures__/core/structure.json";
import {
  dispatchDftTool,
  keepBundle,
  restorableDraftPatch,
  restoreDftWorkspace,
  saveBundle,
} from "./dftWorkbench";

type Route = (body: any) => Response;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const refusal = (status: number, kind: string, message: string, details = {}) =>
  json(status, { error: { kind, message, details } });

const archive = () =>
  new Response(new Blob(["PK fake archive"]), { status: 200, headers: { "content-type": "application/zip" } });

// A fake core serving the recorded responses (plus any per-test override);
// any other request fails the test loudly.
function fakeCore(overrides: Record<string, Route> = {}) {
  const routes: Record<string, Route> = {
    "GET /capabilities": () => json(200, capabilities),
    "POST /inspect": () => json(200, inspection),
    "POST /explain": () => json(200, explanation),
    "POST /run": (body) => (body.respond_with === "archive" ? archive() : json(200, {})),
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
  const paths = () => requests.map((r) => `${r.method} ${r.path}`);
  return { workspace, requests, paths, saved };
}

const coreUp = async () => null;
// The structure files of the chat a call belongs to (App.tsx's
// collectChatStructures); dft_open_structure picks one by name.
const siFile = { name: "Si.cif", content: structure.structure_content, rawExt: ".cif", ext: ".cif" };
const brokenFile = { name: "broken.cif", content: "not a structure", rawExt: ".cif", ext: ".cif" };
const chat = [siFile, brokenFile];
const openArgs = { file_name: "Si.cif" };

const dispatch = (
  workspace: Parameters<typeof dispatchDftTool>[0],
  ready: Parameters<typeof dispatchDftTool>[1],
  tool: string,
  args: Record<string, unknown>,
  structures: Parameters<typeof dispatchDftTool>[4] = chat,
) => dispatchDftTool(workspace, ready, tool, args, structures);
const archiveName = `Si.cif-${capabilities.tasks[0].id}.zip`;

describe("dispatchDftTool", () => {
  it("stops before touching core while the backend isn't up", async () => {
    const { workspace, requests } = fakeCore();
    const outcome = await dispatch(
      workspace, async () => "goldilocks-core is still starting", "dft_review", {},
    );
    expect(outcome).toEqual({ error: "goldilocks-core is still starting" });
    expect(requests).toEqual([]);
  });

  it("names a core whose vocabulary the vendored Workbench can't speak", async () => {
    const { workspace } = fakeCore({
      "GET /capabilities": () => json(200, { ...capabilities, vocabulary_version: "0" }),
    });
    const outcome = await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    expect(outcome).toEqual({ error: expect.stringMatching(/^incompatible_vocabulary: /) });
  });

  it("dft_open_structure summarizes the structure and the calculation it opened into", async () => {
    const { workspace, requests, paths } = fakeCore();
    const outcome = await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    expect(outcome).toEqual({
      result: {
        structure: {
          formula: "Si8",
          reduced_formula: "Si",
          site_count: 8,
          periodicity: [true, true, true],
          lattice: expect.objectContaining({ lengths_angstrom: [5.431, 5.431, 5.431] }),
          partially_occupied_sites: 0,
        },
        calculation: {
          code: capabilities.codes[0].id, task: capabilities.tasks[0].id, hpc: null, overrides: {},
        },
        cleared_overrides: [],
        // New core tasks reach the model from core's own capabilities.
        tasks_available: capabilities.tasks.map((task) => task.id),
      },
    });
    expect(paths()).toEqual(["GET /capabilities", "POST /inspect"]);
    // The chat's own file goes to core, its format taken from the file.
    expect(requests[1].body).toMatchObject({
      structure_content: structure.structure_content, structure_name: "Si.cif", structure_format: "cif",
    });
  });

  it("dft_open_structure names the chat's structure files when the name isn't one of them", async () => {
    const { workspace, requests } = fakeCore();
    const outcome = await dispatch(workspace, coreUp, "dft_open_structure", { file_name: "Si_cif" });

    expect(outcome).toEqual({
      error: 'No structure file named "Si_cif" in this chat. Structure files in this chat: "Si.cif", '
        + '"broken.cif". Call dft_open_structure again with one of these names, or ask the user to attach the file.',
    });
    expect(requests).toEqual([{ method: "GET", path: "/capabilities", body: undefined }]);
    expect(workspace.getSnapshot().structureInput).toBeNull();
  });

  it("dft_open_structure says so when the chat has no structure files", async () => {
    const { workspace } = fakeCore();
    const outcome = await dispatch(workspace, coreUp, "dft_open_structure", openArgs, []);
    expect(outcome).toEqual({ error: expect.stringContaining("the chat has no structure files yet") });
  });

  it("dft_open_structure sends a POSCAR file as POSCAR", async () => {
    const { workspace, requests } = fakeCore();
    const poscar = { name: "POSCAR", content: "Si\n1.0\n", rawExt: "", ext: ".poscar" };
    await dispatch(workspace, coreUp, "dft_open_structure", { file_name: "POSCAR" }, [poscar]);
    expect(requests[1].body).toMatchObject({ structure_name: "POSCAR", structure_format: "poscar" });
  });

  it("a structure core refuses becomes the tool's error, and the open one stays open", async () => {
    let refuse = false;
    const { workspace } = fakeCore({
      "POST /inspect": () =>
        refuse ? refusal(422, "invalid_structure", "cannot parse the CIF") : json(200, inspection),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    const open = workspace.getSnapshot().structureInput;
    refuse = true;

    const outcome = await dispatch(workspace, coreUp, "dft_open_structure", { file_name: "broken.cif" });

    expect(outcome).toEqual({ error: "invalid_structure: cannot parse the CIF" });
    expect(workspace.getSnapshot().structureInput).toBe(open);
  });

  it("dft_review needs an open structure, then returns the records without their provenance", async () => {
    const { workspace, paths } = fakeCore();
    expect(await dispatch(workspace, coreUp, "dft_review", {})).toEqual({
      error: expect.stringContaining("call dft_open_structure first"),
    });

    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    const outcome = await dispatch(workspace, coreUp, "dft_review", {}) as { result: any };

    expect(paths().at(-1)).toBe("POST /explain");
    const { records, warnings, blocking, calculation } = outcome.result;
    expect(Object.keys(records)).toEqual(Object.keys(explanation.records));
    expect(records.cutoffs).toEqual({
      value: explanation.records.cutoffs.value, source: explanation.records.cutoffs.source,
    });
    // The advisor's note travels where there is one: it is how the model can
    // say why, e.g., mMACE wasn't run (goldilocks-core#259).
    expect(records.vdw).toEqual({
      value: explanation.records.vdw.value,
      source: explanation.records.vdw.source,
      source_note: explanation.records.vdw.source_note,
    });
    expect(warnings).toHaveLength(explanation.warnings.length);
    expect(blocking).toEqual([]);
    expect(calculation.task).toBe(capabilities.tasks[0].id);

    // The chat's review card (#74) reads this same result.
    expect(isDftReview(outcome.result)).toBe(true);
    expect(reviewRows(outcome.result)).toEqual([
      { label: "Functional", value: "PBEsol", source: "Heuristic default" },
      { label: "Pseudopotentials", value: "PseudoDojo, efficiency", source: "Heuristic default" },
      { label: "Cutoffs (wfc / rho)", value: "48 / 192 Ry", source: "Heuristic default" },
      { label: "k-point mesh", value: "6 × 6 × 6", source: "Goldilocks-ML prediction" },
      { label: "Occupations", value: "fixed", source: "Heuristic default" },
      { label: "Spin", value: "non-magnetic", source: "Heuristic default" },
    ]);
  });

  it("dft_review passes a blocking verdict on with its remedies", async () => {
    const { workspace } = fakeCore({ "POST /explain": () => json(200, blockedExplanation) });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    const outcome = await dispatch(workspace, coreUp, "dft_review", {}) as { result: any };

    const [finding] = blockedExplanation.blocking;
    expect(outcome.result.blocking).toEqual([
      { summary: finding.summary, fields: finding.fields, remedies: finding.remedies },
    ]);
  });

  it("dft_review sends a failed review again when it is called again", async () => {
    // A failure stays its draft's outcome; without a retry the second call
    // would only read the first one's failure back.
    let fail = true;
    const { workspace, paths } = fakeCore({
      "POST /explain": () => (fail ? refusal(500, "internal_error", "boom") : json(200, explanation)),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    expect(await dispatch(workspace, coreUp, "dft_review", {})).toEqual({
      error: expect.stringContaining("internal_error: boom (goldilocks-core failed internally"),
    });
    fail = false;
    const outcome = await dispatch(workspace, coreUp, "dft_review", {});

    expect(outcome).toHaveProperty("result");
    expect(paths().filter((path) => path === "POST /explain")).toHaveLength(2);
  });

  it("doesn't send a refusal of the draft again, which would come back the same", async () => {
    const { workspace, paths } = fakeCore({
      "POST /explain": () => refusal(422, "invalid_setting", "ecutwfc_ry must be positive"),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const first = await dispatch(workspace, coreUp, "dft_review", {});
    const second = await dispatch(workspace, coreUp, "dft_review", {});

    expect(second).toEqual(first);
    expect(first).toEqual({ error: "invalid_setting: ecutwfc_ry must be positive" });
    expect(paths().filter((path) => path === "POST /explain")).toHaveLength(1);
  });

  it("says how to reinstall core's assets when one is missing", async () => {
    const { workspace } = fakeCore({
      "POST /explain": () => refusal(424, "asset_not_installed", "pseudopotential table not installed"),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    expect(await dispatch(workspace, coreUp, "dft_review", {})).toEqual({
      error: expect.stringContaining("`goldilocks assets install workbench` reinstalls them"),
    });
  });

  it("dft_download_bundle generates the archive but leaves saving it to a click", async () => {
    const { workspace, saved, requests } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const outcome = await dispatch(workspace, coreUp, "dft_download_bundle", {});

    expect(outcome).toMatchObject({ result: { filename: archiveName } });
    expect(requests.at(-1)).toMatchObject({ path: "/run", body: { respond_with: "archive" } });
    // Browsers drop a save outside a direct user gesture, so the tool never
    // attempts one.
    expect(saved).toEqual([]);
  });

  it("dft_download_bundle refuses a blocked draft with the decision it needs", async () => {
    const { workspace, paths } = fakeCore({ "POST /explain": () => json(200, blockedExplanation) });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const outcome = await dispatch(workspace, coreUp, "dft_download_bundle", {});

    const [finding] = blockedExplanation.blocking;
    expect(outcome).toEqual({ error: expect.stringContaining(finding.summary) });
    expect(outcome).toEqual({ error: expect.stringContaining('{"isolation":"periodic"}') });
    expect(paths()).not.toContain("POST /run");
  });

  it("dft_download_bundle lists the findings of a refused generation", async () => {
    const [finding] = blockedExplanation.blocking;
    const { workspace } = fakeCore({
      "POST /run": () => refusal(422, "advice_incomplete", "cannot generate", { findings: [finding] }),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const outcome = await dispatch(workspace, coreUp, "dft_download_bundle", {});

    expect(outcome).toEqual({ error: expect.stringMatching(/^advice_incomplete: .*resolved by setting/) });
  });

  it("names an unknown tool instead of guessing", async () => {
    const { workspace } = fakeCore();
    expect(await dispatch(workspace, coreUp, "dft_teleport", {})).toEqual({
      error: "Unknown DFT Workbench tool: dft_teleport",
    });
  });
});

describe("keepBundle and saveBundle", () => {
  it("save the archive the tool produced, even after the settings change", async () => {
    const { workspace } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatch(workspace, coreUp, "dft_download_bundle", {});
    const id = keepBundle(workspace);
    expect(id).not.toBeNull();

    await workspace.dispatch({ type: "draft.patch", overrides: { ecutwfc_ry: 60 } });
    expect(currentArchive(workspace.getSnapshot())).toBeNull();

    const save = vi.fn();
    expect(saveBundle(id!, save)).toBeNull();
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ filename: archiveName }));
  });

  it("keep nothing without a current archive", () => {
    const { workspace } = fakeCore();
    expect(keepBundle(workspace)).toBeNull();
  });

  it("say why a card from before a reload has nothing to save", () => {
    const save = vi.fn();
    expect(saveBundle(undefined, save)).toMatch(/before the page was reloaded/);
    expect(saveBundle("unknown-id", save)).toMatch(/before the page was reloaded/);
    expect(save).not.toHaveBeenCalled();
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
      { vocabulary: capabilities.vocabulary_version, draft: { ...draft, task: "md", hpc: "archer2" } },
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
    expect(currentArchive(workspace.getSnapshot())?.filename).toBe("Si.cif-relax.zip");
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
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    const opened = workspace.getSnapshot().structureInput;
    const forget = vi.fn();

    expect(await restoreDftWorkspace(workspace, savedState(), coreUp, forget)).toBe("skipped");

    expect(workspace.getSnapshot().structureInput).toBe(opened);
    expect(forget).not.toHaveBeenCalled();
  });

  it.each(["invalid_structure", "invalid_request"])("forgets a structure core refuses (%s)", async (kind) => {
    const { workspace } = fakeCore({
      "POST /inspect": () => refusal(422, kind, "cannot use this input"),
    });
    const forget = vi.fn();
    expect(await restoreDftWorkspace(workspace, savedState(), coreUp, forget)).toBe("forgotten");
    expect(forget).toHaveBeenCalledOnce();
  });

  it("keeps the saved state after any other failure, without a Retry banner", async () => {
    // Retry would reopen the structure with default settings, which the save
    // listener would write over the saved ones.
    const { workspace } = fakeCore({
      "POST /inspect": () => refusal(503, "core_unavailable", "still starting"),
    });
    const forget = vi.fn();
    expect(await restoreDftWorkspace(workspace, savedState(), coreUp, forget)).toBe("kept");
    expect(forget).not.toHaveBeenCalled();
    expect(workspace.getSnapshot().inspectFailure).toBeNull();
  });
});
