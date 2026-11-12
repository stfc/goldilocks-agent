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

  it("dft_set switches the task the way the panel's Task select does, and returns the new review", async () => {
    const { workspace, requests } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const outcome = await dispatch(workspace, coreUp, "dft_set", { values: { task: "phonon" } }) as { result: any };

    expect(outcome.result.changed).toEqual({ task: "phonon" });
    expect(outcome.result.calculation.task).toBe("phonon");
    // The panel and core read the same draft.
    expect(workspace.getSnapshot().draft?.task).toBe("phonon");
    expect(requests.at(-1)).toMatchObject({ path: "/explain", body: { task: "phonon" } });
    // The chat shows it as a review card (#74), as it does dft_review's.
    expect(isDftReview(outcome.result)).toBe(true);
  });

  it("dft_set sets any panel setting as an override, and null puts it back to automatic", async () => {
    const { workspace, requests } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const set = await dispatch(workspace, coreUp, "dft_set", {
      values: { walltime_h: 24, smearing_type: "cold", k_grid: [8, 8, 8], is_metal: "metal", use_vdw: true },
    }) as { result: any };

    const overrides = { walltime_h: 24, smearing_type: "cold", k_grid: [8, 8, 8], is_metal: "metal", use_vdw: true };
    expect(set.result.changed).toEqual(overrides);
    expect(workspace.getSnapshot().draft?.overrides).toEqual(overrides);
    expect(requests.at(-1)?.body.overrides).toEqual(overrides);

    const reset = await dispatch(workspace, coreUp, "dft_set", { values: { k_grid: null } }) as { result: any };

    expect(reset.result.changed).toEqual({ k_grid: "automatic" });
    // Cleared, not sent as null: the panel's own draft.patch would keep null.
    expect(workspace.getSnapshot().draft?.overrides).not.toHaveProperty("k_grid");
    expect(requests.at(-1)?.body.overrides).not.toHaveProperty("k_grid");
  });

  it("dft_set checks settings against the task the same call sets", async () => {
    const { workspace } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    // q_grid is a phonon setting: refused on SCF, taken with the switch.
    expect(await dispatch(workspace, coreUp, "dft_set", { values: { q_grid: [4, 4, 4] } })).toEqual({
      error: expect.stringContaining("q_grid doesn't apply to the scf_single_point task (it applies to phonon, relax-phonon, vc-relax-phonon)"),
    });
    const outcome = await dispatch(workspace, coreUp, "dft_set", { values: { task: "phonon", q_grid: [4, 4, 4] } });

    expect(outcome).toHaveProperty("result");
    expect(workspace.getSnapshot().draft).toMatchObject({ task: "phonon", overrides: { q_grid: [4, 4, 4] } });
  });

  it("dft_set refuses every wrong value at once, names the closest keys, and changes nothing", async () => {
    const { workspace, paths } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    const before = workspace.getSnapshot().draft;
    const explains = paths().filter((path) => path === "POST /explain").length;

    const outcome = await dispatch(workspace, coreUp, "dft_set", {
      values: { walltime: 24, smearing_type: "marzari", k_grid: [8, 8], nstep: 10, use_vdw: "yes", needs_soc: true },
    }) as { error: string };

    expect(outcome.error).toMatch(/^Nothing was set: /);
    expect(outcome.error).toContain('"walltime" isn\'t a setting (closest: walltime_h');
    expect(outcome.error).toContain("smearing_type must be one of cold, fd, gaussian, mp");
    expect(outcome.error).toContain("k_grid must be a list of 3 integers");
    expect(outcome.error).toContain("nstep doesn't apply to the scf_single_point task");
    expect(outcome.error).toContain("use_vdw must be true or false");
    expect(outcome.error).toContain("needs_soc is advice only");
    expect(workspace.getSnapshot().draft).toBe(before);
    expect(paths().filter((path) => path === "POST /explain")).toHaveLength(explains);
  });

  it("dft_set refuses an unknown task or HPC profile with the ones core offers", async () => {
    const { workspace } = fakeCore();
    expect(await dispatch(workspace, coreUp, "dft_set", { values: { task: "phonons", hpc: "archer2" } })).toEqual({
      error: expect.stringMatching(/"phonons" isn't a task goldilocks-core offers: .*phonon \(Phonon \(no relaxation\)\).*"archer2" isn't a HPC profile goldilocks-core offers: scarf \(scarf\)/),
    });
  });

  it("dft_set refuses a task the code doesn't run", async () => {
    const { workspace } = fakeCore({
      "GET /capabilities": () => json(200, {
        ...capabilities,
        codes: [...capabilities.codes, { id: "vasp", name: "VASP", tasks: ["scf_single_point"] }],
        tasks: capabilities.tasks.map((task) =>
          task.id === "scf_single_point" ? { ...task, codes: [...task.codes, "vasp"] } : task),
      }),
    });

    expect(await dispatch(workspace, coreUp, "dft_set", { values: { code: "vasp", task: "band" } })).toEqual({
      error: expect.stringContaining("the band task can't run with vasp (codes that run it: quantum_espresso)"),
    });
    expect(workspace.getSnapshot().draft?.code).toBe("quantum_espresso");
  });

  it("dft_set changes the functional the way the panel's select does", async () => {
    const { workspace } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatch(workspace, coreUp, "dft_set", { values: { pseudo_table_id: "sssp-pbesol-precision-sr" } });

    // A table of another functional than the one set is refused...
    expect(await dispatch(workspace, coreUp, "dft_set", {
      values: { functional: "PBE", pseudo_table_id: "sssp-pbesol-precision-sr" },
    })).toEqual({ error: expect.stringContaining("pseudo_table_id must be a table covering this structure for PBE") });
    // ...and changing the functional alone puts the table back to automatic.
    const outcome = await dispatch(workspace, coreUp, "dft_set", { values: { functional: "PBE" } }) as { result: any };

    expect(outcome.result.changed).toEqual({ functional: "PBE", pseudo_table_id: "automatic" });
    expect(workspace.getSnapshot().draft?.overrides).toEqual({ functional: "PBE" });
    expect(await dispatch(workspace, coreUp, "dft_set", { values: { functional: "B3LYP" } })).toEqual({
      error: expect.stringContaining("functional must be one of PBEsol, PBE, LDA"),
    });
  });

  it("dft_set keeps structure settings until a structure is open, and the next one opens into the task", async () => {
    const { workspace } = fakeCore();
    expect(await dispatch(workspace, coreUp, "dft_set", { values: { k_grid: [4, 4, 4], is_metal: "metal" } })).toEqual({
      error: expect.stringContaining("k_grid describes the structure, so it can only be set once one is open"),
    });

    const outcome = await dispatch(workspace, coreUp, "dft_set", { values: { task: "band", walltime_h: 12 } });
    const opened = await dispatch(workspace, coreUp, "dft_open_structure", openArgs) as { result: any };

    expect(outcome).toEqual({ result: { changed: { task: "band", walltime_h: 12 }, calculation: expect.objectContaining({ task: "band" }) } });
    expect(opened.result.calculation).toMatchObject({ task: "band", overrides: { walltime_h: 12 } });
  });

  it("dft_set undoes what it set when core refuses it, keeping a change made in the panel meanwhile", async () => {
    let refuse = false;
    const { workspace } = fakeCore({
      "POST /explain": () => (refuse ? refusal(422, "invalid_setting", "degauss must be positive") : json(200, explanation)),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatch(workspace, coreUp, "dft_set", { values: { walltime_h: 24 } });
    refuse = true;

    const outcome = await dispatch(workspace, coreUp, "dft_set", { values: { degauss: -0.01, walltime_h: 6 } });

    expect(outcome).toEqual({ error: "Nothing was set, goldilocks-core refused it: invalid_setting: degauss must be positive" });
    expect(workspace.getSnapshot().draft?.overrides).toEqual({ walltime_h: 24 });
  });

  it("dft_set sends nothing again for what is already set", async () => {
    const { workspace, paths } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatch(workspace, coreUp, "dft_set", { values: { walltime_h: 24 } });
    const revision = workspace.getSnapshot().revision;

    const outcome = await dispatch(workspace, coreUp, "dft_set", {
      values: { task: capabilities.tasks[0].id, walltime_h: 24, k_index: null },
    }) as { result: any };

    expect(outcome.result.changed).toEqual({});
    expect(workspace.getSnapshot().revision).toBe(revision);
    // Only the first dft_set's review: the second reads it back.
    expect(paths().filter((path) => path === "POST /explain")).toHaveLength(1);
  });

  it("dft_set asks for values when given none", async () => {
    const { workspace } = fakeCore();
    expect(await dispatch(workspace, coreUp, "dft_set", {})).toEqual({
      error: expect.stringContaining("Nothing to set"),
    });
  });

  it("dft_settings lists what the panel shows for the task, with current values and who set them", async () => {
    const { workspace } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    await dispatch(workspace, coreUp, "dft_set", { values: { walltime_h: 24 } });

    const outcome = await dispatch(workspace, coreUp, "dft_settings", {}) as { result: any };

    const { calculation, settings } = outcome.result;
    expect(calculation.task).toEqual({
      value: "scf_single_point",
      options: capabilities.tasks.map((task) => `${task.id} (${task.name})`),
    });
    const byKey = Object.fromEntries(settings.map((entry: any) => [entry.key, entry]));
    // Every setting of the task and every overridable fact; no other task's.
    const scfKeys = capabilities.settings.filter((s) => s.tasks.includes("scf_single_point")).map((s) => s.key);
    expect(Object.keys(byKey)).toEqual([...scfKeys, "is_metal", "is_magnetic", "needs_correlation"]);
    expect(byKey.k_grid).toEqual({
      key: "k_grid", group: "k_sampling", type: "list of 3 integers",
      value: [6, 6, 6], source: "ml", description: expect.stringMatching(/^Explicit Monkhorst-Pack mesh/),
    });
    expect(byKey.smearing_type.options).toEqual(["cold", "fd", "gaussian", "mp"]);
    expect(byKey.functional).toMatchObject({ value: "PBEsol", source: "heuristic", options: ["PBEsol", "PBE", "LDA"] });
    // The fake core answers every draft with the recorded review (168 h).
    expect(byKey.walltime_h).toMatchObject({ unit: "hours", value: 168, set_by_user: true });
    expect(byKey.nodes).not.toHaveProperty("set_by_user");
    expect(byKey.is_metal).toMatchObject({ group: "analysis", options: ["metal", "non_metal"], value: "non_metal" });
    // Small enough to send on demand.
    expect(JSON.stringify(outcome.result).length).toBeLessThan(12_000);
  });

  it("dft_settings narrows to a group, and names the groups there are", async () => {
    const { workspace } = fakeCore();
    const job = await dispatch(workspace, coreUp, "dft_settings", { group: "job" }) as { result: any };

    expect(job.result.settings.map((entry: any) => entry.key)).toEqual(["partition", "nodes", "ntasks", "walltime_h", "account"]);
    // No structure open: no current values yet.
    expect(job.result.note).toMatch(/No structure is open/);
    expect(await dispatch(workspace, coreUp, "dft_settings", { group: "jobs" })).toEqual({
      error: expect.stringMatching(/^No settings group "jobs" for the scf_single_point task\. Groups: .*job/),
    });
  });

  it("dft_magnetic_orderings needs a spin-polarized calculation", async () => {
    const { workspace } = fakeCore();
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);
    expect(await dispatch(workspace, coreUp, "dft_magnetic_orderings", {})).toEqual({
      error: expect.stringContaining("isn't spin-polarized"),
    });
  });

  it("dft_magnetic_orderings lists the candidates, then ranks them when asked", async () => {
    const magnetic = { ...explanation.records.magnetic, value: { ...explanation.records.magnetic.value, spin_polarized: true } };
    const candidate = (label: string, energy: number | null, recommended = false) => ({
      label, formula: "Si8", natoms: 8, energy_per_atom_ev: energy, status: energy === null ? null : "ok",
      is_recommended: recommended, overrides: { magnetic_ordering: label },
    });
    const { workspace, paths } = fakeCore({
      "POST /explain": () => json(200, { ...explanation, records: { ...explanation.records, magnetic } }),
      "POST /magnetic-orderings": () => json(200, {
        ranked: false, candidates: [candidate("fm", null), candidate("afm1", null)], warnings: [],
      }),
      "POST /magnetic-orderings/rank": () => json(200, {
        job_id: "j1", state: "done",
        report: { ranked: true, candidates: [candidate("afm1", -5.4, true), candidate("fm", -5.3)], warnings: [] },
      }),
    });
    await dispatch(workspace, coreUp, "dft_open_structure", openArgs);

    const listed = await dispatch(workspace, coreUp, "dft_magnetic_orderings", {}) as { result: any };
    const ranked = await dispatch(workspace, coreUp, "dft_magnetic_orderings", { rank: true }) as { result: any };

    expect(listed.result).toEqual({
      ranked: false,
      candidates: [
        { label: "fm", formula: "Si8", natoms: 8, energy_per_atom_ev: null, status: null, is_recommended: false },
        { label: "afm1", formula: "Si8", natoms: 8, energy_per_atom_ev: null, status: null, is_recommended: false },
      ],
      warnings: [],
    });
    // The candidates' override values stay with the panel, not the model.
    expect(JSON.stringify(listed.result)).not.toContain("magnetic_ordering");
    expect(ranked.result.ranked).toBe(true);
    expect(ranked.result.candidates[0]).toMatchObject({ label: "afm1", energy_per_atom_ev: -5.4, is_recommended: true });
    expect(paths().filter((path) => path.startsWith("POST /magnetic-orderings"))).toEqual([
      "POST /magnetic-orderings", "POST /magnetic-orderings/rank",
    ]);
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
