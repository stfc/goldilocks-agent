// What the web build's Settings and sidebar offer a visitor: where their chat
// goes and how long it stays, what only the local version has, and nothing
// they can't use -- no API key fields, no AiiDA placeholder toggle, no
// history search. No model involved.
import { expect, test } from "@playwright/test";
import { openApp } from "./helpers";

test("Settings says where a visitor's chat goes and what the local version adds, with no keys, AiiDA toggle or search", async ({ page }) => {
  await openApp(page);
  await expect(page.locator(".sidebar .nav-row", { hasText: "Search" })).toHaveCount(0);

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Chat model", exact: true })).toBeVisible();
  await expect(page.getByText("running on STFC's server", { exact: false })).toBeVisible();
  await expect(page.getByText("deleted 24 hours after your last message", { exact: false })).toBeVisible();
  await expect(page.locator(".settings-list li")).toHaveText([
    /Claude, OpenAI or Gemini with your own API key/,
    /Materials Project search/,
    /MLIP Playground/,
    /stay on your computer/,
  ]);
  await expect(page.getByRole("link", { name: "Get the local version" })).toHaveAttribute(
    "href",
    /github\.com\/stfc\/goldilocks-agent/,
  );
  await expect(page.getByText("Chat model and API keys")).toHaveCount(0);
  await expect(page.locator(".credential-input")).toHaveCount(0);
  await expect(page.getByText("AiiDA / HPC connection")).toHaveCount(0);
});
