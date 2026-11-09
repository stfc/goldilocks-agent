// The chat's compact review card (#74): which rows it shows, read from a
// dft_review result shaped as reviewForModel returns it (a resolved record is
// just value and source). dftWorkbench.test.ts checks it against a real review.

import { describe, expect, it } from "vitest";
import { type DftReview, isDftReview, reviewRows, reviewTitle } from "./dftReview";

const review = (records: DftReview["records"], blocking: DftReview["blocking"] = []): DftReview => ({
  calculation: { code: "quantum_espresso", task: "scf_single_point" },
  records,
  warnings: [],
  blocking,
});

describe("reviewRows", () => {
  it("shows smearing, Hubbard U and a vdW correction when core sets them", () => {
    const rows = reviewRows(review({
      occupations: {
        source: "heuristic",
        value: { occupations: "smearing", smearing_type: "mv", degauss: 0.02 },
      },
      hubbard: {
        source: "heuristic",
        value: { u_values: [{ element: "Fe", manifold: "3d", u_ev: 5.3 }] },
      },
      vdw: { source: "human", value: { use_vdw: true, method: "dft-d3" } },
    }));
    expect(rows).toEqual([
      { label: "Occupations", value: "smearing, mv smearing 0.02 Ry", source: "Heuristic default" },
      { label: "Hubbard U", value: "Fe 3d 5.3 eV", source: "Heuristic default" },
      { label: "vdW correction", value: "dft-d3", source: "Your override" },
    ]);
  });

  it("leaves out no Hubbard U and no vdW, and marks what core couldn't resolve", () => {
    const rows = reviewRows(review({
      hubbard: { source: "heuristic", value: { u_values: [] } },
      vdw: { source: "heuristic", value: { use_vdw: false } },
      functional: { status: "blocked" },
      k_sampling: { status: "unavailable" },
    }));
    expect(rows).toEqual([
      { label: "Functional", value: "needs a decision", source: null },
      { label: "k-point mesh", value: "not available", source: null },
    ]);
  });
});

describe("isDftReview", () => {
  it("is a review only when the call returned one", () => {
    expect(isDftReview(review({}))).toBe(true);
    expect(isDftReview({ error: "call dft_open_structure first" })).toBe(false);
    expect(isDftReview(null)).toBe(false);
  });
});

it("titles the card with the task and the code", () => {
  expect(reviewTitle(review({}))).toBe("goldilocks-core recommendation · scf_single_point · Quantum ESPRESSO");
});
