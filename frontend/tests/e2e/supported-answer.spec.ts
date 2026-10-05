import { expect, test } from "@playwright/test";

const QUESTION = "How do I complete the synthetic Blue Lantern permit replacement exercise?";
test("submits a question, reads ordered cited steps, and ends the chat", async ({ page }) => {
  const apiResponses: Array<{ method: string; path: string; status: number }> = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/v1/")) {
      apiResponses.push({
        method: response.request().method(),
        path: url.pathname,
        status: response.status(),
      });
    }
  });

  await page.goto("/");

  const composer = page.getByLabel("Ask a PNW question");
  await expect(composer).toBeEnabled();
  await composer.fill(QUESTION);
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByLabel("Your question")).toHaveText(QUESTION);
  const answer = page.getByRole("article", { name: "Chatbot response" });
  await expect(answer).toBeVisible();

  const steps = answer.getByRole("listitem");
  await expect(steps).toHaveCount(5);
  await expect(steps).toHaveText([
    /Verify that both required marks appear on the fictional placard\./,
    /Record the fixture reference code BL-204, then open the linked PDF to continue with step 3\./,
    /Enter Fixture Student and reference code BL-204\./,
    /Attach the sample image labeled BLUE-LANTERN-SAMPLE\./,
    /Submit through the fixture-only submission route\. Retain the synthetic confirmation text TEST-COMPLETE\./,
  ]);

  const overviewCitation = answer.getByRole("link", {
    name: /Synthetic Blue Lantern Procedure Overview.*Scope/,
  });
  await expect(overviewCitation).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/blue-lantern/procedure/#scope",
  );
  await expect(overviewCitation).toHaveAttribute("target", "_blank");
  await expect(overviewCitation).toHaveAttribute("rel", "noreferrer noopener");

  const linkedPageCitations = answer.getByRole("link", {
    name: /Synthetic Blue Lantern Eligibility and First Steps.*Numbered procedure/,
  });
  await expect(linkedPageCitations).toHaveCount(2);
  await expect(linkedPageCitations.first()).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/blue-lantern/eligibility-and-steps/#first-steps",
  );

  const pdfPageOneCitations = answer.getByRole("link", {
    name: /Synthetic Blue Lantern Replacement Form Instructions.*Steps 3 and 4.*page 1/,
  });
  await expect(pdfPageOneCitations).toHaveCount(2);
  await expect(pdfPageOneCitations.first()).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/blue-lantern/replacement-form.pdf",
  );
  await expect(
    answer.getByRole("link", {
      name: /Synthetic Blue Lantern Replacement Form Instructions.*Step 5 and completion.*page 2/,
    }),
  ).toBeVisible();

  expect(apiResponses).toContainEqual({ method: "POST", path: "/api/v1/sessions", status: 201 });
  expect(apiResponses).toContainEqual({ method: "POST", path: "/api/v1/messages", status: 200 });

  await page.getByRole("button", { name: "End chat" }).click();

  await expect(page.getByText("Your previous conversation is closed.")).toBeVisible();
  await expect(page.getByText("This chat ended and its messages were cleared.")).toBeVisible();
  await expect(page.getByLabel("Your question")).toHaveCount(0);
  await expect(answer).toHaveCount(0);
  await expect(composer).toHaveCount(0);
  expect(apiResponses).toContainEqual({ method: "DELETE", path: "/api/v1/session", status: 204 });
});
