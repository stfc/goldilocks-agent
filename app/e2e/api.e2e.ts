// What the web build's server allows and refuses, called the way a browser
// on workbench.goldilocks.ac.uk would. No model involved.
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fixture } from "./helpers";

const corePin = JSON.parse(
  readFileSync(new URL("../../src/goldilocks_agent/core_pin.json", import.meta.url), "utf8"),
);
const fe = { structure_content: readFileSync(fixture("Fe_bcc.cif"), "utf8"), structure_name: "Fe.cif" };

test("the proxied goldilocks-core is the pinned release", async ({ request }) => {
  const capabilities = await (await request.get("/capabilities")).json();
  expect(capabilities.core_version).toBe(corePin.version);
  expect(capabilities.vocabulary_version).toBe(corePin.vocabulary_version);
});

test("the single-user routes are refused: preferences, conversations, credentials", async ({ request }) => {
  expect((await request.get("/api/preferences")).status()).toBe(404);
  expect((await request.get("/api/conversations")).status()).toBe(404);
  expect((await request.post("/api/credentials", { data: { provider: "anthropic", api_key: "x" } })).status()).toBe(404);
});

test("MLIP Playground is the local version's only", async ({ request }) => {
  const response = await request.post("/api/mlip/singlepoint", { data: { structure_content: "x", structure_name: "x.cif" } });
  expect(response.status()).toBe(404);
  expect((await response.json()).detail).toContain("local version");
});

test("Materials Project isn't on the website, even by its structure ids", async ({ request }) => {
  const response = await request.get("/api/fetch-structure", {
    params: { source: "Materials Project", entry_id: "mp-149" },
  });
  expect(response.status()).toBe(503);
  expect((await response.json()).detail).toContain("not searched on the website");
});

test("ranking goes through the job routes, not the old synchronous call", async ({ request }) => {
  expect((await request.post("/magnetic-orderings", { data: { ...fe, rank_with_mmace: true } })).status()).toBe(422);
  const submitted = await request.post("/magnetic-orderings/rank", { data: fe });
  expect(submitted.status()).toBe(202);
  const { job_id } = await submitted.json();
  expect((await request.delete(`/magnetic-orderings/rank/${job_id}`)).status()).toBe(204);
  expect((await request.get(`/magnetic-orderings/rank/${job_id}`)).status()).toBe(404);
});

test("a chat request with an unknown experience level is refused", async ({ request }) => {
  const response = await request.post("/api/chat", {
    data: { thread_id: "e2e", message: { role: "user", content: "Hi" }, experience_level: "expert" },
  });
  expect(response.status()).toBe(422);
});
