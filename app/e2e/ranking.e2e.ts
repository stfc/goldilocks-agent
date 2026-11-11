// mMACE ordering ranking from the embedded DFT Workbench, through the agent's
// proxy to goldilocks-core's job routes (core#261, #89): the user sees the
// job queued or running, then the ranked orderings. No model involved.
import { expect, test } from "@playwright/test";
import { fixture, openApp } from "./helpers";

test("Run mMACE shows the job's progress, then a recommended ordering", async ({ page }) => {
  const rankingCalls: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (pathname.startsWith("/magnetic-orderings")) rankingCalls.push(`${request.method()} ${pathname}`);
  });
  await openApp(page);
  await page.getByTitle("Expand all tools").click();
  await page.locator(".tools-overview-card", { hasText: "DFT Workbench" }).click();
  // No wait for core's capabilities: since core 0.3.2 a file opened before
  // they arrive opens once they do (core#281).
  // The Workbench's own file button (its accessible name since core 0.3.1).
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("button", { name: "Browse files" }).first().click(),
  ]);
  await chooser.setFiles(fixture("Fe_bcc.cif"));

  await page.getByRole("button", { name: "Run mMACE" }).click();
  await expect(page.getByText(/Queued|Ranking candidate/).first()).toBeVisible();
  await expect(page.getByText("RECOMMENDED", { exact: false }).first()).toBeVisible({ timeout: 6 * 60_000 });
  expect(rankingCalls).toContain("POST /magnetic-orderings/rank");
  expect(rankingCalls.some((call) => call.startsWith("GET /magnetic-orderings/rank/"))).toBe(true);
});
