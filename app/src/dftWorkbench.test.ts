// The browser-side helpers for a chat's goldilocks-workbench Workspace. The
// chat's DFT Workbench tools themselves run on the agent server (#142) and
// are tested there (tests/tools/test_dft_workbench.py).

import { HttpCoreClient, createWorkspace } from "goldilocks-workbench";
import { describe, expect, it } from "vitest";
import capabilities from "./__fixtures__/core/capabilities.json";
import structure from "./__fixtures__/core/structure.json";
import { ensureWorkspaceStarted, structureInputFor, untilAborted, waitForWorkspaceIdle } from "./dftWorkbench";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function fakeCore() {
  const paths: string[] = [];
  const fetcher = (async (url: string, init: RequestInit = {}) => {
    const path = new URL(url, "http://core.test").pathname;
    paths.push(`${init.method ?? "GET"} ${path}`);
    return json(200, capabilities);
  }) as typeof fetch;
  return { workspace: createWorkspace(new HttpCoreClient("", fetcher)), paths };
}

describe("structureInputFor", () => {
  it("sends a CIF as CIF and anything else as POSCAR, as the server does", () => {
    expect(structureInputFor({ name: "Si.cif", content: structure.structure_content, ext: ".cif" })).toEqual({
      structure_content: structure.structure_content,
      structure_name: "Si.cif",
      structure_format: "cif",
    });
    expect(structureInputFor({ name: "POSCAR", content: "x", ext: ".poscar" }).structure_format).toBe("poscar");
  });
});

describe("ensureWorkspaceStarted", () => {
  it("loads core's capabilities once", async () => {
    const { workspace, paths } = fakeCore();
    await ensureWorkspaceStarted(workspace);
    await ensureWorkspaceStarted(workspace);
    expect(workspace.getSnapshot().capabilities).not.toBeNull();
    expect(paths).toEqual(["GET /capabilities"]);
  });
});

describe("waitForWorkspaceIdle", () => {
  it("resolves at once for an idle workspace", async () => {
    const { workspace } = fakeCore();
    await expect(waitForWorkspaceIdle(workspace)).resolves.toBeUndefined();
  });
});

describe("untilAborted", () => {
  it("settles with the promise, or rejects as soon as the signal aborts", async () => {
    await expect(untilAborted(Promise.resolve(1))).resolves.toBe(1);
    const controller = new AbortController();
    const pending = untilAborted(new Promise(() => {}), controller.signal);
    controller.abort(new Error("stopped"));
    await expect(pending).rejects.toThrow("stopped");
  });
});
