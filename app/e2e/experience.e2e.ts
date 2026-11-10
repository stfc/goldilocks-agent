// The tab's experience level reaches the system prompt with every chat
// request, and the server's one global preferences file is left alone (#72).
// Only the request is checked; the turn is stopped without waiting for it.
import { expect, test } from "@playwright/test";
import { openApp } from "./helpers";

test("the level chosen at onboarding travels with the chat request", async ({ page }) => {
  const chats: { experience_level?: string }[] = [];
  const preferenceCalls: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (pathname === "/api/chat" && request.method() === "POST") chats.push(request.postDataJSON());
    if (pathname.startsWith("/api/preferences")) preferenceCalls.push(request.method());
  });

  await openApp(page, /Advanced/);
  await page.locator(".composer-text textarea").fill("Hello");
  await page.locator("button.send-btn:not(.stop-btn)").click();
  await expect.poll(() => chats.length).toBeGreaterThan(0);
  expect(chats[0].experience_level).toBe("advanced");
  expect(preferenceCalls).toEqual([]);
  await page.locator("button.stop-btn").click().catch(() => {});
});
