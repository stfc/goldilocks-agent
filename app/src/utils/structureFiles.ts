export const WEAS_SUPPORTED_EXTS = new Set([".cif", ".cube", ".poscar", ".vasp", ".xsf", ".xyz", ".weas-json"]);

// A 3D vector (or lattice vector) as plain numbers -- mirrors the loose
// array shape WEAS/ASE-style structure payloads use throughout this file.
type Vec3 = [number, number, number];
type Cell = [Vec3, Vec3, Vec3];

// The structure payload shape consumed by WeasStructureViewport's
// `applyStructurePayload` helper (from the `weas` package).
export interface StructurePayload {
  symbols: string[];
  positions: number[][];
  cell: Cell;
  pbc: [boolean, boolean, boolean];
}

function parseVector(line: string): Vec3 {
  const values = line.trim().split(/\s+/).slice(0, 3).map(Number);
  if (values.length !== 3 || values.some((value) => !Number.isFinite(value))) {
    throw new Error("Invalid POSCAR lattice vector.");
  }
  return values as Vec3;
}

function fracToCartesian(frac: number[], cell: Cell): Vec3 {
  return [
    frac[0] * cell[0][0] + frac[1] * cell[1][0] + frac[2] * cell[2][0],
    frac[0] * cell[0][1] + frac[1] * cell[1][1] + frac[2] * cell[2][1],
    frac[0] * cell[0][2] + frac[1] * cell[1][2] + frac[2] * cell[2][2],
  ];
}

export function normalizeStructureExtension(ext: string = ""): string {
  return ext.toLowerCase();
}

export function getRawFileExtension(fileName: string = ""): string {
  const trimmed = fileName.trim();
  const lastDot = trimmed.lastIndexOf(".");
  if (lastDot <= 0 || lastDot === trimmed.length - 1) return "";
  return trimmed.slice(lastDot).toLowerCase();
}

export function formatStructureLabel(ext: string = ""): string {
  const normalized = normalizeStructureExtension(ext);
  if (!normalized) return "FILE";
  if (normalized === ".weas-json") return "MLIP";
  return normalized.replace(".", "").toUpperCase();
}

export function getStructureFenceLanguage(ext: string = ""): string {
  const normalized = normalizeStructureExtension(ext);
  return normalized ? normalized.replace(".", "") : "text";
}

export function parsePoscarToPayload(content: string): StructurePayload {
  const lines = content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  if (lines.length < 8) {
    throw new Error("POSCAR/VASP file is too short.");
  }

  const rawScale = Number(lines[1]);
  const scale = Number.isFinite(rawScale) && rawScale !== 0 ? rawScale : 1;
  const cell = [lines[2], lines[3], lines[4]]
    .map(parseVector)
    .map((vector) => vector.map((value) => value * scale)) as Cell;

  let cursor = 5;
  const maybeSymbols = lines[cursor].split(/\s+/);
  const hasSymbols = maybeSymbols.some((token) => Number.isNaN(Number(token)));

  const symbols = hasSymbols ? maybeSymbols : maybeSymbols.map((_, index) => `X${index + 1}`);
  const countsLine = hasSymbols ? lines[cursor + 1] : lines[cursor];
  const counts = countsLine.split(/\s+/).map(Number);

  if (!counts.length || counts.some((count) => !Number.isFinite(count) || count < 0)) {
    throw new Error("Invalid POSCAR species counts.");
  }

  cursor += hasSymbols ? 2 : 1;

  if (lines[cursor]?.toLowerCase().startsWith("s")) {
    cursor += 1;
  }

  const coordinateMode = lines[cursor]?.toLowerCase() ?? "";
  const isDirect = coordinateMode.startsWith("d");
  const isCartesian = coordinateMode.startsWith("c") || coordinateMode.startsWith("k");

  if (!isDirect && !isCartesian) {
    throw new Error("POSCAR coordinate mode must be Direct or Cartesian.");
  }

  cursor += 1;

  const outSymbols: string[] = [];
  const positions: number[][] = [];

  for (let speciesIndex = 0; speciesIndex < counts.length; speciesIndex += 1) {
    const symbol = symbols[speciesIndex] ?? `X${speciesIndex + 1}`;
    const atomCount = counts[speciesIndex];

    for (let atomIndex = 0; atomIndex < atomCount; atomIndex += 1) {
      const line = lines[cursor];
      if (!line) {
        throw new Error("POSCAR atom list ended earlier than expected.");
      }

      const coords = line.split(/\s+/).slice(0, 3).map(Number);
      if (coords.length !== 3 || coords.some((value) => !Number.isFinite(value))) {
        throw new Error("Invalid POSCAR atomic coordinates.");
      }

      outSymbols.push(symbol);
      positions.push(isDirect ? fracToCartesian(coords, cell) : coords.map((value) => value * scale));
      cursor += 1;
    }
  }

  return {
    symbols: outSymbols,
    positions,
    cell,
    pbc: [true, true, true],
  };
}

function looksLikeCif(content: string = ""): boolean {
  const trimmed = content.trim();
  return /^data_/im.test(trimmed) || /_cell_length_a|_atom_site_/im.test(trimmed);
}

function looksLikeXsf(content: string = ""): boolean {
  return /PRIMVEC|PRIMCOORD|BEGIN_BLOCK_DATAGRID_3D/i.test(content);
}

function looksLikeXyz(content: string = ""): boolean {
  const lines = content.trim().split(/\r?\n/);
  if (lines.length < 3) return false;
  const count = Number.parseInt(lines[0], 10);
  if (!Number.isFinite(count) || count <= 0 || lines.length < count + 2) return false;
  const firstAtom = lines[2]?.trim().split(/\s+/) ?? [];
  return firstAtom.length >= 4 && firstAtom.slice(1, 4).every((value) => Number.isFinite(Number(value)));
}

function looksLikeCube(content: string = ""): boolean {
  const lines = content.trim().split(/\r?\n/);
  if (lines.length < 6) return false;
  const header = lines[2]?.trim().split(/\s+/).map(Number) ?? [];
  const axisA = lines[3]?.trim().split(/\s+/).map(Number) ?? [];
  const axisB = lines[4]?.trim().split(/\s+/).map(Number) ?? [];
  const axisC = lines[5]?.trim().split(/\s+/).map(Number) ?? [];
  return header.length >= 4 && axisA.length >= 4 && axisB.length >= 4 && axisC.length >= 4;
}

export function inferStructureExtension(fileName: string = "", content: string = ""): string {
  const lowerName = fileName.trim().toLowerCase();
  if (lowerName === "poscar" || lowerName === "contcar") return ".poscar";

  const rawExt = getRawFileExtension(fileName);
  if (WEAS_SUPPORTED_EXTS.has(rawExt)) return rawExt;

  if (looksLikeCif(content)) return ".cif";
  if (looksLikeXsf(content)) return ".xsf";
  if (looksLikeXyz(content)) return ".xyz";
  if (looksLikeCube(content)) return ".cube";

  try {
    parsePoscarToPayload(content);
    return ".poscar";
  } catch {
    return rawExt;
  }
}
