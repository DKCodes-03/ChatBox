import { expect, test } from "@playwright/test";

const QUESTION = "What are the synthetic add, drop, and refund deadlines?";

interface MessagePayload {
  message: string;
  context?: Record<string, string>;
}

test("clarifies context, preserves table citations, and requeries after a correction", async ({
  page,
}) => {
  const messagePayloads: MessagePayload[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/messages" && request.method() === "POST") {
      messagePayloads.push(request.postDataJSON() as MessagePayload);
    }
  });

  await page.goto("/");
  const composer = page.getByLabel("Ask a PNW question");
  await expect(composer).toBeEnabled();
  await composer.fill(QUESTION);
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Which PNW campus should I use for this question?")).toBeVisible();
  await expect(page.getByRole("article", { name: "Chatbot response" })).toHaveCount(0);
  await page.getByRole("button", { name: "Use Hammond for Campus" }).click();

  await expect(page.getByText("Which academic term should I use for this question?")).toBeVisible();
  await page.getByRole("button", { name: "Use Fall for Term" }).click();

  await expect(page.getByText("Which academic year should I use for this question?")).toBeVisible();
  await page.getByRole("button", { name: "Use 2030 for Year" }).click();

  await expect(
    page.getByText("Which academic session should I use for this question?"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Use Full Term for Session" }).click();

  const firstAnswer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(firstAnswer).toBeVisible();
  await expect(firstAnswer).toContainText(
    "The 80 percent refund deadline is September 13, 2030 at 5:00 p.m. Central Time.",
  );
  const firstContext = firstAnswer.getByRole("complementary", { name: "Answer context" });
  await expect(firstContext).toContainText("Hammond");
  await expect(firstContext).toContainText("Fall");
  await expect(firstContext).toContainText("2030");
  await expect(firstContext).toContainText("Full Term");
  await expect(
    firstAnswer.getByRole("link", {
      name: /Synthetic Hammond Fall 2030 Full-Term Schedule.*Add, drop, and refund deadlines/,
    }),
  ).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/us3/hammond-fall-2030-full-term/#schedule-table",
  );

  await firstContext.getByRole("button", { name: "Change campus" }).click();
  await page.getByLabel("New campus").fill("Westville");
  await page.getByRole("button", { name: "Save correction" }).click();

  await expect(page.getByText("Campus corrected to Westville")).toBeVisible();
  await expect(page.getByRole("article", { name: "Chatbot response" })).toHaveCount(2);
  const correctedAnswer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(correctedAnswer).toContainText(
    "The 80 percent refund deadline is September 15, 2030 at 5:00 p.m. Central Time.",
  );
  await expect(correctedAnswer).not.toContainText("September 13, 2030");
  const correctedContext = correctedAnswer.getByRole("complementary", {
    name: "Answer context",
  });
  await expect(correctedContext).toContainText("Westville");
  await expect(correctedContext).toContainText("Fall");
  await expect(correctedContext).toContainText("2030");
  await expect(correctedContext).toContainText("Full Term");
  await expect(
    correctedAnswer.getByRole("link", {
      name: /Synthetic Westville Fall 2030 Full-Term Schedule.*Add, drop, and refund deadlines/,
    }),
  ).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/us3/westville-fall-2030-full-term/#schedule-table",
  );

  expect(messagePayloads).toEqual([
    { message: QUESTION },
    { message: QUESTION, context: { campus: "Hammond" } },
    { message: QUESTION, context: { campus: "Hammond", term: "Fall" } },
    { message: QUESTION, context: { campus: "Hammond", term: "Fall", year: "2030" } },
    {
      message: QUESTION,
      context: { campus: "Hammond", term: "Fall", year: "2030", session: "Full Term" },
    },
    {
      message: QUESTION,
      context: { campus: "Westville", term: "Fall", year: "2030", session: "Full Term" },
    },
  ]);
});
