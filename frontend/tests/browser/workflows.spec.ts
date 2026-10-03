import { expect, test } from "@playwright/test";
test("dashboard, responsive navigation, authorization and unlock failure", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/login");
  await page.getByRole("button", { name: "SUPER ADMIN", exact: false }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Demo mode — hardware actions are simulated."),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `test-results/${info.project.name}-dashboard.png`,
    fullPage: true,
  });
  await page.getByText("Demo simulation controls", { exact: true }).click();
  await page.getByLabel("Unlock timeout", { exact: true }).check();
  await page.getByRole("button", { name: "Timed unlock", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByRole("button", { name: "Confirm unlock for 5 seconds" })
    .click();
  await expect(
    page.getByText(/Unlock timed out — outcome uncertain/).first(),
  ).toBeVisible();
  await page.getByLabel("Development identity").selectOption("account-manager");
  await expect(page.getByLabel("Development identity")).toHaveValue(
    "account-manager",
  );
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Timed unlock", exact: true }),
  ).toHaveCount(0);
  await page.evaluate(() => {
    history.pushState({}, "", "/people");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(
    page.getByRole("heading", { name: "Access forbidden" }),
  ).toBeVisible();
  await page.evaluate(() => {
    history.pushState({}, "", "/cards-activity");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(
    page.getByRole("button", { name: "CSV", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("Card identifier").fill("008899");
  await expect(page.getByText("1 records · page 1")).toBeVisible();
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(
      page.getByRole("dialog", { name: "Navigation menu" }),
    ).toBeVisible();
    await page
      .getByRole("dialog")
      .getByRole("link", { name: "My Access", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "My Access", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});
test("registration, profile upload, payment evidence, rejection and resubmission", async ({
  page,
}, info) => {
  await page.goto("/register");
  await page
    .getByLabel("Registration number / staff ID")
    .fill("DEMO/" + info.project.name);
  await page.getByLabel("First name", { exact: true }).fill("Test");
  await page.getByLabel("Last name", { exact: true }).fill("Applicant");
  await page.getByLabel("University email").fill("test@sub.futo.edu.ng");
  await page.getByLabel("Phone", { exact: true }).fill("08000000000");
  await page.getByLabel("Department", { exact: true }).fill("IFT");
  await page.getByLabel("Photo", { exact: true }).setInputFiles({
    name: "photo.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJYoAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await page.getByLabel("Password", { exact: true }).fill("synthetic-only");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "exactly futo.edu.ng" }),
  ).toBeVisible();
  await page
    .getByLabel("University email")
    .fill(`test-${info.project.name}@futo.edu.ng`);
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "My Access", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Request first card" }).click();
  await page.getByLabel("Payment evidence", { exact: true }).setInputFiles({
    name: "evidence.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\nsynthetic test"),
  });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(
    page.locator(".badge").filter({ hasText: "Submitted" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "View evidence" }).click();
  await expect(
    page.getByRole("link", { name: "Download evidence" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.screenshot({
    path: `test-results/${info.project.name}-application.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByLabel("Development identity").selectOption("account-manager");
  await expect(page.getByLabel("Development identity")).toHaveValue(
    "account-manager",
  );
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    history.pushState({}, "", "/requests");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  const request = page
    .locator(".panel")
    .filter({ has: page.getByRole("heading", { name: "Test Applicant" }) });
  await request.getByRole("button", { name: "Reject", exact: true }).click();
  await page
    .getByLabel("Reason visible to applicant")
    .fill("Please show payment reference");
  await page.getByRole("button", { name: "Reject evidence" }).click();
  await expect(
    request.getByText("Rejected: Please show payment reference"),
  ).toBeVisible();
  await expect(
    request.getByRole("button", { name: "Issue card", exact: true }),
  ).toHaveCount(0);
  const newId = await page
    .getByLabel("Development identity")
    .locator("option")
    .filter({ hasText: "Test Applicant" })
    .getAttribute("value");
  await page.getByLabel("Development identity").selectOption(newId!);
  await page.getByLabel("Resubmit payment evidence").setInputFiles({
    name: "revised.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 synthetic revision"),
  });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(
    page.locator(".badge").filter({ hasText: "Submitted" }),
  ).toBeVisible();
});

test("card enrolment and safe visitor return", async ({ page }, info) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "ADMIN", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    history.pushState({}, "", "/requests");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await page.getByRole("button", { name: "Approve payment" }).click();
  await page.getByRole("button", { name: "Issue card", exact: true }).click();
  await page.getByRole("button", { name: "Simulate scan card" }).click();
  await page.getByRole("button", { name: "Use confirmed card number" }).click();
  await page
    .getByRole("button", { name: "Confirm enrolment & activate" })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.locator(".badge").filter({ hasText: /^Issued$/ }),
  ).toBeVisible();
  await page.evaluate(() => {
    history.pushState({}, "", "/visitors");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await page.getByRole("button", { name: "Confirm external approval" }).click();
  await page
    .getByLabel("External approval reference / confirmation")
    .fill("Demo departmental approval");
  await page
    .getByRole("button", { name: "Confirm approval", exact: true })
    .click();
  await page.getByRole("button", { name: "Assign pool card" }).click();
  await page.getByLabel("Available disabled card").selectOption("pool-04");
  await page
    .getByRole("button", { name: "Assign and simulate activation" })
    .click();
  await page.getByText("Demo simulation controls", { exact: true }).click();
  await page.getByLabel("Fail credential disable", { exact: true }).check();
  await expect(
    page.getByLabel("Fail credential disable", { exact: true }),
  ).toBeChecked();
  await page.getByRole("button", { name: "Record return / checkout" }).click();
  await page.getByRole("button", { name: "End access and disable" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
    "physical card return",
  );
  await page
    .getByLabel("Physical card return", { exact: true })
    .selectOption("yes");
  await page.getByRole("button", { name: "End access and disable" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const pool = page.locator(".panel").filter({
    has: page.getByRole("heading", { name: "Card 009004", exact: true }),
  });
  await expect(
    pool.getByText("Return Pending Disable", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: `test-results/${info.project.name}-visitors.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByLabel("Fail credential disable", { exact: true }).uncheck();
  await expect(
    page.getByLabel("Fail credential disable", { exact: true }),
  ).not.toBeChecked();
  await page
    .getByRole("button", { name: "Retry simulated sync", exact: true })
    .click();
  await expect(
    pool.getByText("Available Disabled", { exact: true }),
  ).toBeVisible();
});
