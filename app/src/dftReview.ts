// What the chat's compact review card shows (#74, components/DftReviewCard):
// goldilocks-core's key values and the tier that decided each, read from the
// review dispatchDftTool returns to the model (dftWorkbench.ts's
// reviewForModel), so the user reads core's numbers rather than the model's
// retelling of them.

type Source = "human" | "ml" | "llm" | "heuristic";

interface ReviewRecord {
  readonly status?: string;
  readonly value?: any;
  readonly source?: Source;
}

export interface DftReview {
  readonly calculation: { readonly code: string; readonly task: string };
  readonly records: Readonly<Record<string, ReviewRecord>>;
  readonly warnings: readonly { readonly level: string }[];
  readonly blocking: readonly { readonly summary: string }[];
}

export interface ReviewRow {
  readonly label: string;
  readonly value: string;
  /** The tier's display name, or null for a record core couldn't resolve. */
  readonly source: string | null;
}

// goldilocks-workbench's own names for the tiers (its src/api/sourceNames.ts,
// SOURCE_NAMES), which the library doesn't export.
const SOURCE_NAMES: Record<Source, string> = {
  human: "Your override",
  ml: "Goldilocks-ML prediction",
  llm: "LLM suggestion",
  heuristic: "Heuristic default",
};

const CODE_NAMES: Record<string, string> = { quantum_espresso: "Quantum ESPRESSO" };
const PROVIDER_NAMES: Record<string, string> = { pseudodojo: "PseudoDojo", sssp: "SSSP" };

// The rows a card shows, in order: a label, the record, and how to read its
// value. A reader returning null leaves the row out (no Hubbard U, no vdW).
const ROWS: readonly (readonly [string, string, (value: any) => string | null])[] = [
  ["Functional", "functional", (v) => String(v)],
  [
    "Pseudopotentials",
    "pseudo_table",
    (v) => [PROVIDER_NAMES[v.provider] ?? v.provider, v.accuracy].filter(Boolean).join(", "),
  ],
  ["Cutoffs (wfc / rho)", "cutoffs", (v) => `${v.ecutwfc_ry} / ${v.ecutrho_ry} Ry`],
  [
    "k-point mesh",
    "k_sampling",
    (v) => (v.mesh ? `${v.mesh.join(" × ")}${v.shift?.some(Boolean) ? ", shifted" : ""}` : null),
  ],
  [
    "Occupations",
    "occupations",
    (v) => (v.smearing_type ? `${v.occupations}, ${v.smearing_type} smearing ${v.degauss} Ry` : v.occupations),
  ],
  ["Spin", "magnetic", (v) => (v.spin_polarized ? "spin-polarized" : "non-magnetic")],
  [
    "Hubbard U",
    "hubbard",
    (v) =>
      v.u_values?.length
        ? v.u_values.map((u: any) => `${u.element} ${u.manifold} ${u.u_ev} eV`).join(", ")
        : null,
  ],
  ["vdW correction", "vdw", (v) => (v.use_vdw ? (v.method ?? "on") : null)],
];

/** Whether a dft_review result is a review (not an `{ error }`). */
export function isDftReview(result: unknown): result is DftReview {
  const review = result as DftReview | null;
  return Boolean(review?.records && review?.calculation && Array.isArray(review?.blocking));
}

export function reviewRows(review: DftReview): ReviewRow[] {
  return ROWS.flatMap(([label, name, read]) => {
    const record = review.records[name];
    if (!record) return [];
    // reviewForModel gives a resolved record no status, only value and source.
    if (record.status && record.status !== "resolved") {
      const value = record.status === "blocked" ? "needs a decision" : "not available";
      return [{ label, value, source: null }];
    }
    const value = read(record.value);
    return value === null ? [] : [{ label, value, source: record.source ? SOURCE_NAMES[record.source] : null }];
  });
}

export function reviewTitle(review: DftReview): string {
  const { code, task } = review.calculation;
  return `goldilocks-core recommendation · ${task} · ${CODE_NAMES[code] ?? code}`;
}
