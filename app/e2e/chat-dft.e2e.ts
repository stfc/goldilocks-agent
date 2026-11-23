// The web build's main path: a structure attached in chat is opened,
// reviewed and turned into input files by the model's DFT Workbench tools.
// Checks what the tools leave behind -- the review card's core values (#74)
// and its button, the bundle under Generated Files (#94) -- not what the
// model says.
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
  // Its button opens the full-page Workbench, every card side by side.
  await card.getByRole("button", { name: "Open in DFT Workbench" }).click();
  await expect(page.locator(".tools-fullpage")).toBeVisible();
  await expect(page.locator(".tools-fullpage #advisors-panel")).toBeVisible();
  await page.getByRole("button", { name: "← Chat" }).click();
  await expect(bundleCard.first()).toBeVisible();

  await openGeneratedFiles(page);
  const row = page.locator(".files-list .files-row").first();
  await expect(row).toContainText("Si.cif-scf_single_point.zip");
  // A link to the bundle the server kept for the chat (#142).
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    row.getByRole("link", { name: "Download" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("Si.cif-scf_single_point.zip");

  // The tab keeps its chat across a reload; cards are rebuilt from history,
  // and the message shows the attached file's name, not its contents.
  await page.reload();
  const firstMessage = page.locator(".user-bubble").first();
  await expect(firstMessage).toContainText("📎 Si.cif");
  await expect(firstMessage).not.toContainText("_cell_length_a");
  await expect(page.locator(".review-card").first()).toContainText("48 / 192 Ry");
  await openGeneratedFiles(page);
  const keptRow = page.locator(".files-list .files-row").first();
  await expect(keptRow).toContainText("Si.cif-scf_single_point.zip");
  // The server kept the bundle, so it still downloads after the reload.
  const [again] = await Promise.all([
    page.waitForEvent("download"),
    keptRow.getByRole("link", { name: "Download" }).click(),
  ]);
  expect(again.suggestedFilename()).toBe("Si.cif-scf_single_point.zip");
});

// 2026-11-12 on the web: after an SCF review the user typed "PHONON" and the
// model, with no tool to switch the task, told them to do it in the panel.
// dft_set switches the panel's own Task, and its review runs the new task.
test("the chat switches the task the user asks for, in the panel too", async ({ page }) => {
  const explained: { task: string }[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/explain") explained.push(request.postDataJSON());
  });
  await openApp(page);
  await send(page, "Open this structure in DFT Workbench and review it.", [fixture("Si.cif")]);
  await expect(page.locator(".review-card").first()).toContainText("scf_single_point");

  await send(page, "PHONON");

  const phonon = /^(vc-)?(relax-)?phonon$/;
  expect(explained.at(-1)?.task).toMatch(phonon);
  const card = page.locator(".review-card").last();
  await expect(card).toContainText("phonon");
  await card.getByRole("button", { name: "Open in DFT Workbench" }).click();
  await expect(page.locator(".tools-fullpage").getByLabel("Task")).toHaveValue(phonon);
});

// Any panel setting, not just the task: a value the user gives in chat lands
// in the panel's own control, as their override, and in core's next review.
test("the chat sets a value the user gives, in the panel too", async ({ page }) => {
  const explained: { overrides: Record<string, unknown> }[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/explain") explained.push(request.postDataJSON());
  });
  await openApp(page);
  await send(page, "Open this structure in DFT Workbench and review it.", [fixture("Si.cif")]);

  await send(page, "Set the walltime to 24 hours.");

  expect(explained.at(-1)?.overrides).toMatchObject({ walltime_h: 24 });
  await page.locator(".review-card").last().getByRole("button", { name: "Open in DFT Workbench" }).click();
  const workbench = page.locator(".tools-fullpage");
  await workbench.getByRole("button", { name: /^Job/ }).click();
  await expect(workbench.getByLabel(/walltime/i)).toHaveValue("24");
});
