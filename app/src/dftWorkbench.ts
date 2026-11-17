// Helpers for driving a chat's goldilocks-workbench `Workspace` in the
// browser. The chat's DFT Workbench tools run on the agent server (#142);
// dftDraftSync.ts keeps each chat's Workspace in step with its draft there,
// and the panel opens a chat's structure file with `structureInputFor`.

import type { Workspace } from "goldilocks-workbench";
import type { ChatStructure } from "./utils/structureFiles";

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
// a Stop click abandoned).
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
// mount effect, which a replay before the panel shows may not have had.
export async function ensureWorkspaceStarted(workspace: Workspace): Promise<void> {
  if (workspace.getSnapshot().capabilities !== null) return;
  await workspace.dispatch({ type: "workspace.start" });
}

// The structure input core's /inspect takes for one of the chat's structure
// files, as the server's dft_open_structure builds it (structures.py).
export function structureInputFor(file: Pick<ChatStructure, "name" | "content" | "ext">) {
  return {
    structure_content: file.content,
    structure_name: file.name,
    structure_format: file.ext === ".cif" ? "cif" : "poscar",
  };
}
