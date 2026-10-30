// Records the goldilocks-core responses src/dftWorkbench.test.ts replays,
// from a running core (`poe serve` in a goldilocks-core checkout, or
// `goldilocks serve http`) at CORE_URL (default http://127.0.0.1:8000).
// Rerun it against the release in src/goldilocks_agent/core_pin.json
// whenever that moves to a new vocabulary: the vendored Workbench client
// refuses responses from any other one, so the tests fail until then.
//
//   node scripts/record-core-fixtures.mjs path/to/Si.cif
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename } from "node:path";

const coreUrl = process.env.CORE_URL ?? "http://127.0.0.1:8000";
const [structurePath] = process.argv.slice(2);
if (!structurePath) {
  console.error("usage: node scripts/record-core-fixtures.mjs <structure file>");
  process.exit(2);
}

async function call(path, body) {
  const init = body === undefined
    ? {}
    : {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
  const response = await fetch(`${coreUrl}${path}`, init);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
  return response.json();
}

const name = basename(structurePath);
const structure = {
  structure_content: await readFile(structurePath, "utf8"),
  structure_name: name,
  structure_format: name.toLowerCase().endsWith(".cif") ? "cif" : "poscar",
};
const capabilities = await call("/capabilities");
// What the Workbench sends for a freshly opened structure: the first code
// and task core offers, no HPC profile, no overrides.
const compute = {
  ...structure,
  code: capabilities.codes[0].id,
  task: capabilities.tasks[0].id,
  hpc: null,
  overrides: {},
  fetch_missing: false,
};
const fixtures = {
  "structure.json": structure,
  "capabilities.json": capabilities,
  "inspect.json": await call("/inspect", structure),
  "explain.json": await call("/explain", compute),
};
const out = new URL("../src/__fixtures__/core/", import.meta.url);
await mkdir(out, { recursive: true });
for (const [file, data] of Object.entries(fixtures)) {
  await writeFile(new URL(file, out), `${JSON.stringify(data, null, 2)}\n`);
}
console.log(`recorded vocabulary ${capabilities.vocabulary_version} from ${coreUrl}`);
