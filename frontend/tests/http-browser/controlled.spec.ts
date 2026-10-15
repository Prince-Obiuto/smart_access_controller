import { expect, test } from "@playwright/test";

test("controlled mode offers explicit reviews while blocking generic command replay", async ({
  page,
}, info) => {
  await page.goto("/login");
  await page
    .getByLabel("Email address", { exact: true })
    .fill("super_admin@futo.edu.ng");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Synthetic-browser-password!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dashboard", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Controller changes require individual preview, authorization and SDK readback/,
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Remote unlock", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Simulated live monitor", { exact: true }),
  ).toHaveCount(0);
  const snapshot = await (await page.request.get("/api/workspace")).json();
  expect(snapshot.controllerMode).toBe("HIKVISION_CONTROLLED");
  expect(snapshot.controllerTestWritesEnabled).toBe(false);
  expect(snapshot.controllerWritesAvailable).toBe(false);
  const csrf = (await (await page.request.get("/api/auth/csrf")).json())
    .csrfToken;
  for (const [path, data] of [
    ["/api/commands", { type: "unlock", doorId: "door-1", confirmed: true }],
    ["/api/enrolment-sessions", {}],
  ] as const) {
    const response = await page.request.post(path, {
      headers: {
        "X-CSRF-Token": csrf,
        "Idempotency-Key": "controlled-block-" + info.project.name,
      },
      data,
    });
    expect(response.status()).toBe(409);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
