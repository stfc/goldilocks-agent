// A chat's DFT Workbench draft lives on the agent server (#142): the structure
// the panel opened and the settings changed in it come back after a reload,
// from the server rather than the browser's own storage. No model involved.
import { expect, test, type Page } from "@playwright/test";
import { fixture, openApp } from "./helpers";

async function openWorkbench(page: Page) {
  await page.getByTitle("Expand all tools").click();
  await page.locator(".tools-overview-card", { hasText: "DFT Workbench" }).click();
  return page.locator(".tools-fullpage");
}

test("the Workbench's structure and settings come back after a reload", async ({ page }) => {
  const saved: { status: number; task?: string }[] = [];
  page.on("response", async (response) => {
    const { pathname } = new URL(response.url());
    if (response.request().method() !== "PUT" || !/^\/api\/chat\/[^/]+\/dft\/draft$/.test(pathname)) return;
    saved.push({ status: response.status(), task: (await response.json()).draft?.task });
  });
  await openApp(page);
  let workbench = await openWorkbench(page);
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    workbench.getByRole("button", { name: "Browse files" }).first().click(),
  ]);
  await chooser.setFiles(fixture("Si.cif"));
  await expect(workbench.getByLabel("Task", { exact: true })).toBeEnabled({ timeout: 60_000 });

  await workbench.getByLabel("Task", { exact: true }).selectOption("band");

  await expect.poll(() => saved.at(-1)?.task).toBe("band");
  expect(saved.map((save) => save.status)).not.toContain(409);
  // Nothing of it in the browser's own storage any more (#18's restore).
  const stored = await page.evaluate(() =>
    [...Object.keys(localStorage), ...Object.keys(sessionStorage)].filter((key) => key.includes("dft")),
  );
  expect(stored).toEqual([]);

  await page.reload();
  workbench = await openWorkbench(page);
  await expect(workbench.getByLabel("Task", { exact: true })).toHaveValue("band", { timeout: 60_000 });
  await expect(workbench.getByText("Si.cif").first()).toBeVisible();
});
