// The web build's All Tools page: what it offers, and what only the local
// version has (#83). No model involved.
import { expect, test } from "@playwright/test";
import { openApp } from "./helpers";

test("All Tools separates the local version's tools from the ones coming everywhere", async ({ page }) => {
  await openApp(page);
  await page.getByTitle("Expand all tools").click();
  await expect(page.locator(".tools-overview-card:not(.coming-soon) strong")).toHaveText([
    "Find in Databases",
    "DFT Workbench",
    "Post Analysis",
  ]);
  await expect(page.locator(".tools-overview-section-label")).toHaveText([
    "Coming soon to the web · in the local version now",
    "Coming soon",
  ]);
  await expect(page.locator(".tools-overview-card.coming-soon strong")).toHaveText([
    "MLIP Playground",
    "AiiDAlab-QE",
    "Beyond DFT",
  ]);
});
