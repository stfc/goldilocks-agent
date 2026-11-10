// Shared steps for the web build's e2e tests: opening the app as a new
// visitor, sending a chat turn and waiting for it to finish, and the
// composer's Files panel.
import { expect, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** A structure file in e2e/fixtures. */
export const fixture = (name: string): string => path.join(here, "fixtures", name);

/** Opens the app as a new visitor (a fresh tab has its own session) and
 * answers the experience-level onboarding. */
export async function openApp(page: Page, level: RegExp = /Familiar/): Promise<void> {
  await page.goto("/");
  await page.locator(".option-card", { hasText: level }).click();
  await page.locator(".modal-actions .primary-btn").click();
  await expect(page.locator(".composer-text textarea")).toBeVisible();
}

/** Sends one chat turn, with optional attachments, and waits until it and
 * every client tool call it chains have finished. */
export async function send(page: Page, text: string, files: string[] = []): Promise<void> {
  if (files.length) await page.locator(".composer-input-row input[type=file]").setInputFiles(files);
  await page.locator(".composer-text textarea").fill(text);
  await page.locator("button.send-btn:not(.stop-btn)").click();
  await page.locator("button.stop-btn").waitFor({ state: "visible", timeout: 30_000 }).catch(() => {});
  await page.locator("button.stop-btn").waitFor({ state: "detached", timeout: 9 * 60_000 });
}

/** The assistant's last text reply. */
export async function lastReply(page: Page): Promise<string> {
  return page.locator(".message-row .md").last().innerText();
}

/** Opens the composer's Files panel on its Generated Files tab. */
export async function openGeneratedFiles(page: Page): Promise<void> {
  await page.locator("button.inline-widget-btn", { hasText: "Files" }).click();
  await page.getByRole("button", { name: "Generated Files" }).click();
}
