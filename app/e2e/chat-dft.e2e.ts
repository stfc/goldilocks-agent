// The web build's main path: a structure attached in chat is opened,
// reviewed and turned into input files by the model's DFT Workbench tools.
// Checks what the tools leave behind -- the review card's core values (#74),
// the bundle under Generated Files (#94) -- not what the model says.
import { expect, test } from "@playwright/test";
import { fixture, openApp, openGeneratedFiles, send } from "./helpers";

test("open, review and generate from chat, all kept on reload", async ({ page }) => {
  const inspected: { structure_name: string }[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/inspect") inspected.push(request.postDataJSON());
  });
  await openApp(page);
  await send(page, "Open this structure in DFT Workbench, review it, and generate the input files.", [
    fixture("Si.cif"),
  ]);
  // The model may stop to confirm before generating; asking once more is
  // what a user would do.
  const bundleCard = page.locator(".confirmation-card", { hasText: "is ready" });
  if (!(await bundleCard.count())) await send(page, "Yes, generate the input files now.");

  // dft_open_structure named the attached file, and core read that file (#73).
  expect(inspected.map((body) => body.structure_name)).toContain("Si.cif");

  const card = page.locator(".review-card").first();
  await expect(card).toContainText("PBEsol");
  await expect(card).toContainText("48 / 192 Ry");
  await expect(card).toContainText("6 × 6 × 6");
  await expect(card).toContainText("Goldilocks-ML prediction");
  await expect(bundleCard.first()).toBeVisible();

  await openGeneratedFiles(page);
  const row = page.locator(".files-list .files-row").first();
  await expect(row).toContainText("Si.cif-scf_single_point.zip");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    row.getByRole("button", { name: "Download" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("Si.cif-scf_single_point.zip");

  // The tab keeps its chat across a reload; cards are rebuilt from history.
  await page.reload();
  await expect(page.locator(".review-card").first()).toContainText("48 / 192 Ry");
  await openGeneratedFiles(page);
  await expect(page.locator(".files-list .files-row").first()).toContainText("Si.cif-scf_single_point.zip");
});
