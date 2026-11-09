// The structure files a chat holds (#73): what dft_open_structure can open
// by name, and what the Structure Viewer and the DFT Workbench picker list.

import { describe, expect, it } from "vitest";
import structure from "../__fixtures__/core/structure.json";
import {
  TRUNCATED_ATTACHMENT_MARKER,
  collectChatStructures,
  extractStructuresFromMessageContent,
  fetchedStructureMessage,
  structuresInTranscript,
} from "./structureFiles";

const SI_CIF = structure.structure_content;
const POSCAR = "NaCl\n1.0\n5.64 0 0\n0 5.64 0\n0 0 5.64\nNa Cl\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n";

// As App.tsx's send() embeds an attached file.
const attached = (name: string, lang: string, content: string) =>
  `[Attached file: ${name}]\n\`\`\`${lang}\n${content}\n\`\`\``;

describe("extractStructuresFromMessageContent", () => {
  it("finds every structure a message attaches, in order", () => {
    const text = `Compare these.\n\n${attached("Si.cif", "cif", SI_CIF)}\n\n${attached("POSCAR", "poscar", POSCAR)}`;
    expect(extractStructuresFromMessageContent(text).map((s) => [s.name, s.ext])).toEqual([
      ["Si.cif", ".cif"],
      ["POSCAR", ".poscar"],
    ]);
  });

  it("leaves out a truncated attachment and a file that isn't a structure", () => {
    const text = [
      attached("Si.cif", "cif", SI_CIF.slice(0, 200)),
      `${TRUNCATED_ATTACHMENT_MARKER} only the first 200 of ${SI_CIF.length} characters of Si.cif are included above.]`,
      attached("pw.out", "text", "Program PWSCF v.7.3 starts on 9Nov2026\n     total energy = -15.8 Ry"),
      attached("POSCAR", "poscar", POSCAR),
    ].join("\n");
    expect(extractStructuresFromMessageContent(text).map((s) => s.name)).toEqual(["POSCAR"]);
  });

  it("reads the text part of a message with images", () => {
    const content = [
      { type: "text", text: attached("Si.cif", "cif", SI_CIF) },
      { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
    ];
    expect(extractStructuresFromMessageContent(content).map((s) => s.name)).toEqual(["Si.cif"]);
  });
});

describe("fetchedStructureMessage", () => {
  it("keeps a fetched structure under the filename get_structure returned", () => {
    expect(fetchedStructureMessage({ filename: "GaAs_mp-2534.cif", content: SI_CIF, format: "cif" })).toEqual({
      role: "fetched-structure", name: "GaAs_mp-2534.cif", content: SI_CIF,
    });
  });

  it("has nothing for a failed call", () => {
    expect(fetchedStructureMessage({ error: "unknown entry_id" })).toBeNull();
  });
});

describe("structuresInTranscript", () => {
  it("lists attachments and fetched structures; a repeated name keeps its place and its latest content", () => {
    const newer = SI_CIF.replace("5.43100000", "5.46000000");
    const messages = [
      { role: "user", content: attached("Si.cif", "cif", SI_CIF) },
      { role: "assistant", content: "Opened." },
      { role: "fetched-structure", name: "GaAs_mp-2534.cif", content: SI_CIF },
      { role: "user", content: attached("Si.cif", "cif", newer) },
    ];
    const found = structuresInTranscript(messages);
    expect(found.map((s) => s.name)).toEqual(["Si.cif", "GaAs_mp-2534.cif"]);
    expect(found[0].content).toContain("5.46000000");
  });
});

describe("collectChatStructures", () => {
  it("puts the session's structure files first, keeping their fields, and skips non-structures", () => {
    const sessionFiles = [
      { name: "Si.cif", content: SI_CIF, rawExt: ".cif", ext: ".cif", isStructure: true, uploadedAt: 1 },
      { name: "pw.out", content: "log", rawExt: ".out", ext: ".out", isStructure: false },
    ];
    const messages = [
      { role: "user", content: attached("Si.cif", "cif", "an older upload") },
      { role: "fetched-structure", name: "GaAs_mp-2534.cif", content: SI_CIF },
    ];
    const found = collectChatStructures(sessionFiles, messages);
    expect(found.map((s) => s.name)).toEqual(["Si.cif", "GaAs_mp-2534.cif"]);
    expect(found[0]).toBe(sessionFiles[0]);
  });
});
