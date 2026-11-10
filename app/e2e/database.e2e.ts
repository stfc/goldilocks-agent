// A structure fetched from a database by the model becomes a file in the
// chat that DFT Workbench opens by name (#73). Needs the network (Materials
// Cloud); E2E_OFFLINE=1 skips it.
import { expect, test } from "@playwright/test";
import { openApp, send } from "./helpers";

test.skip(process.env.E2E_OFFLINE === "1", "needs Materials Cloud");

test("get_structure, then dft_open_structure by the fetched file's name", async ({ page }) => {
  const inspected: { structure_name: string; structure_content: string }[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/inspect") inspected.push(request.postDataJSON());
  });
  await openApp(page);
  await send(
    page,
    "Fetch the Materials Cloud structure mc3d-13434 with get_structure, then open it in DFT Workbench.",
  );
  const opened = inspected.find((body) => body.structure_name === "mc3d-13434.cif");
  expect(opened, "DFT Workbench opened the fetched file").toBeTruthy();
  expect(opened!.structure_content).toContain("data_");
});
