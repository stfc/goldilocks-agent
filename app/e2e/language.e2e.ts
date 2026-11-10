// The reply comes back in the language of the user's message (#91). The one
// check here on free-form model output, and only on its language.
import { expect, test } from "@playwright/test";
import { lastReply, openApp, send } from "./helpers";

const CJK = /[一-鿿]/;
const GERMAN = /\b(ich|und|die|der|nicht|ist|Sie|für)\b/g;

test("an English question gets an English reply", async ({ page }) => {
  await openApp(page);
  await send(page, "In one sentence, what is a k-point mesh?");
  const reply = await lastReply(page);
  expect(reply).not.toMatch(CJK);
  expect(reply.match(GERMAN)?.length ?? 0).toBeLessThan(3);
});

test("a Chinese question gets a Chinese reply", async ({ page }) => {
  await openApp(page);
  await send(page, "用一句话解释什么是 k 点网格。");
  expect(await lastReply(page)).toMatch(CJK);
});
