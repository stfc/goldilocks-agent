// What DFT Workbench's panel lets a user set, for the chat's dft_settings
// (read) and dft_set (change) tools: the Calculation form's code, task and HPC
// profile, and every override control -- one per goldilocks-core setting
// whose tasks include the draft's task, plus the overridable facts. Keys are
// core's own, exactly as the panel writes them into `draft.overrides` (flat,
// never `steps.<step>.<key>`), so a value the chat sets is one the panel shows.
// Pure functions of the workspace snapshot; dftWorkbench.ts dispatches them.

import type { CalculationDraft, WorkspaceSnapshot } from "goldilocks-workbench";

type Capabilities = NonNullable<WorkspaceSnapshot["capabilities"]>;
type ExplainResult = NonNullable<WorkspaceSnapshot["reviewed"]>;
type StructureInspection = NonNullable<WorkspaceSnapshot["inspection"]>;
type PseudopotentialTable = Capabilities["pseudopotential_tables"][number];
// `items`/`minItems`/`maxItems` are on the wire (array settings such as
// k_grid) but not in the package's `Setting` type.
type Setting = Capabilities["settings"][number] & {
  readonly items?: string;
  readonly minItems?: number;
  readonly maxItems?: number;
};
type Fact = Capabilities["facts"][number];

const CALCULATION_FIELDS = ["code", "task", "hpc"] as const;
type CalculationField = (typeof CALCULATION_FIELDS)[number];

// A `draft.patch` minus its type: the calculation fields that change, and the
// overrides that do, `undefined` putting one back to automatic.
export type DraftChanges = {
  code?: string;
  task?: string;
  hpc?: string | null;
  overrides?: Record<string, unknown>;
};

// --- Rules the panel keeps inside its own components -----------------------
// goldilocks-workbench 0.3.3 applies these in its Functional and
// Pseudopotential table selects (index.js `_p`, `vp`, `yp`) without exporting
// them. They are mirrored here so the chat offers and sets what the panel
// would; replace them with the package's own once it exports them
// (junwen94/goldilocks-core#297).

// The functionals the Functional select offers: those of core's tables.
function functionalOptions(capabilities: Capabilities): string[] {
  return [...new Set(capabilities.pseudopotential_tables.map((table) => table.functional))];
}

// The tables the Pseudopotential table select offers: those covering every
// element of the structure and, when the functional is overridden, of that
// functional.
function pseudoTableOptions(
  capabilities: Capabilities,
  inspection: StructureInspection | null,
  functionalOverride: unknown,
): PseudopotentialTable[] {
  const elements = inspection === null ? [] : structureElements(inspection);
  return capabilities.pseudopotential_tables.filter(
    (table) =>
      (functionalOverride === undefined || table.functional === functionalOverride) &&
      elements.every((element) => table.elements.includes(element)),
  );
}

function structureElements(inspection: StructureInspection): string[] {
  return [...new Set(inspection.structure.sites.flatMap((site) => site.species.map((s) => s.symbol)))];
}

// Changing the functional, or putting it back to automatic, also puts the
// pseudopotential table back to automatic, as the Functional select does.
const CLEARED_WITH: Record<string, string> = { functional: "pseudo_table_id" };

// --- Reading ---------------------------------------------------------------

// The settings `task` offers, in core's order.
function settingsFor(capabilities: Capabilities, task: string): Setting[] {
  return (capabilities.settings as readonly Setting[]).filter((setting) => setting.tasks.includes(task));
}

// The value core resolved for `setting` in `explain`, and the source that
// decided it, as the panel's override control shows them.
function resolvedSetting(setting: Setting, explain: ExplainResult | null) {
  const record = explain?.records[setting.resolved_in.record];
  if (!record || record.status !== "resolved") return {};
  const path = setting.resolved_in.path;
  let value: unknown = undefined;
  if (path !== null) {
    value = path.reduce<unknown>(
      (node, key) => (node !== null && typeof node === "object" ? (node as Record<string, unknown>)[key] : undefined),
      record.value,
    );
  }
  const source = (record.field_sources as Record<string, string> | null | undefined)?.[setting.key] ?? record.source;
  return { ...(value !== undefined ? { value } : {}), ...(source ? { source } : {}) };
}

// "array of 3 integers" for k_grid, the setting's type otherwise.
function kindOf(setting: Setting): string {
  if (!setting.items) return setting.type;
  const size = setting.minItems !== undefined && setting.minItems === setting.maxItems ? `${setting.minItems} ` : "";
  return `list of ${size}${setting.items}s`;
}

function firstSentence(text: string): string {
  const end = text.search(/\.(\s|$)/);
  return end === -1 ? text : text.slice(0, end + 1);
}

// What the model needs to set `setting`: its key, kind and options, and what
// is set now. `options` is the enum, or for the functional and the
// pseudopotential table the panel's own option lists.
function settingEntry(setting: Setting, snapshot: WorkspaceSnapshot, explain: ExplainResult | null) {
  const capabilities = snapshot.capabilities!;
  const overrides = snapshot.draft?.overrides ?? {};
  const options =
    setting.key === "functional"
      ? functionalOptions(capabilities)
      : setting.key === "pseudo_table_id"
        ? pseudoTableOptions(capabilities, snapshot.inspection, overrides.functional).map(
            (table) => `${table.id} (${table.provider} ${table.functional} ${table.accuracy})`,
          )
        : setting.enum;
  return {
    key: setting.key,
    group: setting.group,
    type: kindOf(setting),
    ...(options && options.length > 0 ? { options } : {}),
    ...(setting.unit ? { unit: setting.unit } : {}),
    ...resolvedSetting(setting, explain),
    ...(setting.key in overrides ? { set_by_user: true } : {}),
    description: firstSentence(setting.description),
  };
}

function factEntry(fact: Fact, snapshot: WorkspaceSnapshot, explain: ExplainResult | null) {
  const record = explain?.records[fact.key];
  return {
    key: fact.key,
    group: "analysis",
    type: fact.type,
    ...(fact.values ? { options: fact.values } : {}),
    ...(record?.status === "resolved" ? { value: record.value, source: record.source } : {}),
    ...(fact.key in (snapshot.draft?.overrides ?? {}) ? { set_by_user: true } : {}),
    description: firstSentence(fact.description),
  };
}

// The groups dft_settings can narrow to for the draft's task.
function groupsFor(capabilities: Capabilities, task: string): string[] {
  return [...new Set([...settingsFor(capabilities, task).map((setting) => setting.group), "analysis"])];
}

// dft_settings's result: the calculation form, then every setting the panel
// shows for the draft's task with its current value from `explain` (null
// when no structure is open or core couldn't review it). `group` narrows the
// settings to one group; an unknown group is refused with the groups there
// are.
export function settingsCatalogue(
  snapshot: WorkspaceSnapshot,
  explain: ExplainResult | null,
  group?: string,
): { result: unknown } | { error: string } {
  const capabilities = snapshot.capabilities!;
  const draft = snapshot.draft!;
  const groups = groupsFor(capabilities, draft.task);
  if (group !== undefined && !groups.includes(group)) {
    return { error: `No settings group ${JSON.stringify(group)} for the ${draft.task} task. Groups: ${groups.join(", ")}.` };
  }
  const settings = [
    ...settingsFor(capabilities, draft.task).map((setting) => settingEntry(setting, snapshot, explain)),
    ...capabilities.facts.filter((fact) => fact.overridable).map((fact) => factEntry(fact, snapshot, explain)),
  ].filter((entry) => group === undefined || entry.group === group);
  const ids = (items: readonly { id: string; name: string }[]) => items.map((item) => `${item.id} (${item.name})`);
  return {
    result: {
      calculation: {
        code: { value: draft.code, options: ids(capabilities.codes) },
        task: { value: draft.task, options: ids(capabilities.tasks) },
        hpc: { value: draft.hpc, options: ids(capabilities.hpc_profiles) },
      },
      ...(snapshot.inspection === null ? { note: "No structure is open, so no current values yet." } : {}),
      settings,
    },
  };
}

// --- Changing ----------------------------------------------------------------

// Edit distance, for "did you mean" suggestions.
function distance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let previous = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const current = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] === b[j - 1] ? 0 : 1));
      previous = current;
    }
  }
  return row[b.length];
}

function closest(key: string, candidates: string[]): string[] {
  const lower = key.toLowerCase();
  return candidates
    .map((candidate) => ({
      candidate,
      score: candidate.includes(lower) || lower.includes(candidate) ? 0 : distance(lower, candidate),
    }))
    .sort((a, b) => a.score - b.score)
    .slice(0, 3)
    .map(({ candidate }) => candidate);
}

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

// Why `value` can't be `setting`'s, or null. Ranges are core's to check.
function settingValueError(
  setting: Setting,
  value: unknown,
  capabilities: Capabilities,
  inspection: StructureInspection | null,
  functionalOverride: unknown,
): string | null {
  const key = setting.key;
  if (key === "functional") {
    const options = functionalOptions(capabilities);
    return options.includes(value as string) ? null : `functional must be one of ${options.join(", ")}`;
  }
  if (key === "pseudo_table_id") {
    const options = pseudoTableOptions(capabilities, inspection, functionalOverride).map((table) => table.id);
    return options.includes(value as string)
      ? null
      : `pseudo_table_id must be a table covering this structure${functionalOverride === undefined ? "" : ` for ${functionalOverride}`}: ${options.join(", ") || "none does"}`;
  }
  switch (setting.type) {
    case "boolean":
      return typeof value === "boolean" ? null : `${key} must be true or false`;
    case "integer":
      return Number.isInteger(value) ? null : `${key} must be a whole number`;
    case "number":
      return typeof value === "number" && Number.isFinite(value) ? null : `${key} must be a number`;
    case "string":
      if (typeof value !== "string") return `${key} must be a string`;
      return setting.enum && !setting.enum.includes(value) ? `${key} must be one of ${setting.enum.join(", ")}` : null;
    case "array": {
      const sized =
        Array.isArray(value) &&
        (setting.minItems === undefined || value.length >= setting.minItems) &&
        (setting.maxItems === undefined || value.length <= setting.maxItems);
      const typed =
        Array.isArray(value) &&
        value.every((item) => (setting.items === "integer" ? Number.isInteger(item) : typeof item === "number"));
      return sized && typed ? null : `${key} must be a ${setting.items ? kindOf(setting) : "list of numbers"}`;
    }
    case "object":
      return isPlainObject(value) ? null : `${key} must be an object (${firstSentence(setting.description)})`;
    default:
      return null;
  }
}

function factValueError(fact: Fact, value: unknown): string | null {
  if (fact.type === "boolean") return typeof value === "boolean" ? null : `${fact.key} must be true or false`;
  return fact.values?.includes(value as string) ? null : `${fact.key} must be one of ${fact.values?.join(", ")}`;
}

// Whether `a` and `b` are the same override value.
const sameValue = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

// The draft changes dft_set's `values` ask for -- only what differs from the
// draft, so asking for what is already set sends nothing to core again -- or
// why they can't be made, every reason at once: a key that isn't a setting of
// the task (with the closest ones), a value of the wrong kind, a structure
// setting with no structure open. Settings are checked against the task the
// same call sets, if it sets one. One bad value refuses them all.
export function settingChanges(
  values: unknown,
  snapshot: WorkspaceSnapshot,
): { changes: DraftChanges } | { error: string } {
  const capabilities = snapshot.capabilities!;
  const draft = snapshot.draft!;
  if (!isPlainObject(values) || Object.keys(values).length === 0) {
    return { error: 'Nothing to set -- give `values`, e.g. {"task": "band"} or {"walltime_h": 24}.' };
  }
  const errors: string[] = [];

  const offered: Record<CalculationField, [string, readonly { id: string; name: string }[]]> = {
    code: ["code", capabilities.codes],
    task: ["task", capabilities.tasks],
    hpc: ["HPC profile", capabilities.hpc_profiles],
  };
  for (const field of CALCULATION_FIELDS) {
    if (!(field in values)) continue;
    const [what, items] = offered[field];
    if (!items.some((item) => item.id === values[field])) {
      const ids = items.map((item) => `${item.id} (${item.name})`).join(", ");
      errors.push(`${JSON.stringify(values[field])} isn't a ${what} goldilocks-core offers: ${ids}`);
    }
  }
  const code = (values.code as string | undefined) ?? draft.code;
  const taskId = (values.task as string | undefined) ?? draft.task;
  const task = capabilities.tasks.find((item) => item.id === taskId);
  if (errors.length === 0 && task && !task.codes.includes(code)) {
    errors.push(`the ${task.id} task can't run with ${code} (codes that run it: ${task.codes.join(", ")})`);
  }
  // The settings are checked against the task, so not against a wrong one.
  if (errors.length > 0) return { error: `Nothing was set: ${errors.join("; ")}.` };

  const settings = new Map((capabilities.settings as readonly Setting[]).map((setting) => [setting.key, setting]));
  const facts = new Map(capabilities.facts.map((fact) => [fact.key, fact]));
  const offeredKeys = [
    ...CALCULATION_FIELDS,
    ...settingsFor(capabilities, taskId).map((setting) => setting.key),
    ...capabilities.facts.filter((fact) => fact.overridable).map((fact) => fact.key),
  ];
  const nextOverride = (key: string) => (key in values ? (values[key] ?? undefined) : draft.overrides[key]);
  const overrides: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(values)) {
    if ((CALCULATION_FIELDS as readonly string[]).includes(key)) continue;
    const setting = settings.get(key);
    const fact = facts.get(key);
    if (!setting && !fact) {
      errors.push(`${JSON.stringify(key)} isn't a setting (closest: ${closest(key, offeredKeys).join(", ")}; dft_settings lists them all)`);
      continue;
    }
    if (fact && !fact.overridable) {
      errors.push(`${key} is advice only and can't be set (${firstSentence(fact.description)})`);
      continue;
    }
    if (setting && !setting.tasks.includes(taskId)) {
      errors.push(`${key} doesn't apply to the ${taskId} task (it applies to ${setting.tasks.join(", ")})`);
      continue;
    }
    if (raw === null) {
      if (key in draft.overrides) overrides[key] = undefined;
      continue;
    }
    if ((fact || setting?.structure_specific) && snapshot.inspection === null) {
      errors.push(`${key} describes the structure, so it can only be set once one is open (open it with dft_open_structure first)`);
      continue;
    }
    const error = fact
      ? factValueError(fact, raw)
      : settingValueError(setting!, raw, capabilities, snapshot.inspection, nextOverride("functional"));
    if (error) errors.push(error);
    else if (!sameValue(raw, draft.overrides[key])) overrides[key] = raw;
  }
  for (const [key, cleared] of Object.entries(CLEARED_WITH)) {
    if (key in overrides && !(cleared in values) && cleared in draft.overrides) overrides[cleared] = undefined;
  }
  if (errors.length > 0) return { error: `Nothing was set: ${errors.join("; ")}.` };

  const changes: DraftChanges = {};
  for (const field of CALCULATION_FIELDS) {
    if (field in values && values[field] !== draft[field as keyof CalculationDraft]) {
      changes[field] = values[field] as string;
    }
  }
  if (Object.keys(overrides).length > 0) changes.overrides = overrides;
  return { changes };
}

// What `changes` set, for the model: each key with its new value, or
// "automatic" for one put back.
export function describeChanges(changes: DraftChanges): Record<string, unknown> {
  const { overrides = {}, ...calculation } = changes;
  return {
    ...calculation,
    ...Object.fromEntries(Object.entries(overrides).map(([key, value]) => [key, value === undefined ? "automatic" : value])),
  };
}

// Whether `draft` holds every change in `changes`.
export function holds(changes: DraftChanges, draft: CalculationDraft): boolean {
  return (
    CALCULATION_FIELDS.every((field) => !(field in changes) || draft[field] === changes[field]) &&
    Object.entries(changes.overrides ?? {}).every(([key, value]) => sameValue(draft.overrides[key], value))
  );
}

// The changes that undo `changes` on a draft that was `before`, for the
// fields still holding what `changes` set (`now`) -- a field changed again
// meanwhile, in the panel, is left as it is.
export function revertChanges(
  changes: DraftChanges,
  before: CalculationDraft,
  now: CalculationDraft,
): DraftChanges {
  const revert: DraftChanges = {};
  for (const field of CALCULATION_FIELDS) {
    if (field in changes && now[field] === changes[field]) {
      (revert as Record<string, unknown>)[field] = before[field];
    }
  }
  const overrides = Object.fromEntries(
    Object.entries(changes.overrides ?? {})
      .filter(([key, value]) => sameValue(now.overrides[key], value))
      .map(([key]) => [key, before.overrides[key]]),
  );
  if (Object.keys(overrides).length > 0) revert.overrides = overrides;
  return revert;
}
