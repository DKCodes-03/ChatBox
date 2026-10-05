import { expect, test, type Page } from "@playwright/test";

const PREREQUISITE_QUESTION = "What are the SYN 35000 catalog requirements and corequisite?";
const PLAN_QUESTION = "How do I prepare the synthetic plan of study?";
const PROGRAM_QUESTION = "Does the Synthetic Data Science MS program exist?";
const ADMISSION_QUESTION = "What are the synthetic graduate admission steps?";
const GRADUATION_QUESTION = "How do I apply for synthetic graduation?";
const AVAILABILITY_QUESTION = "Is SYN 46000 available now?";

async function openChatAndAsk(page: Page, question: string) {
  await page.goto("/");
  const composer = page.getByLabel("Ask a PNW question");
  await expect(composer).toBeEnabled();
  await composer.fill(question);
  await page.getByRole("button", { name: "Send" }).click();
}

async function chooseContext(page: Page, value: string, field: string) {
  await page.getByRole("button", { name: `Use ${value} for ${field}` }).click();
}

test("preserves prerequisite alternatives, grades, corequisites, and catalog scope", async ({
  page,
}) => {
  const messageResponses: number[] = [];
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === "/api/v1/messages") {
      messageResponses.push(response.status());
    }
  });

  await openChatAndAsk(page, PREREQUISITE_QUESTION);
  await chooseContext(page, "Hammond", "Campus");
  await chooseContext(page, "Synthetic Computing, BS", "Program");
  await chooseContext(page, "Undergraduate", "Student level");
  await chooseContext(page, "2030-2031", "Catalog year");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(answer).toBeVisible();
  const groups = answer.getByRole("region", { name: "Published prerequisite groups" });
  await expect(groups.getByRole("listitem")).toHaveCount(2);
  await expect(groups).toContainText(
    "Complete either both SYN 21000 with a minimum grade of B and SYN 22000 with a minimum grade of C, or SYN 23000 with a minimum grade of B.",
  );
  await expect(groups).toContainText(
    "SYN 35001 is a corequisite and may be taken concurrently with SYN 35000.",
  );

  const conditions = answer.getByRole("region", { name: "Academic source conditions" });
  await expect(conditions).toContainText("Hammond");
  await expect(conditions).toContainText("Synthetic Computing, BS");
  await expect(conditions).toContainText("Undergraduate");
  await expect(conditions).toContainText("2030-2031");
  await expect(answer.getByRole("complementary", { name: "Source limitation" })).toContainText(
    "do not establish a student's personal eligibility",
  );
  await expect(
    answer.getByRole("complementary", { name: "Academic advising boundary" }),
  ).toContainText("cannot determine your personal course eligibility");
  await expect(
    answer
      .getByRole("link", {
        name: /SYN 35000 Synthetic Analytics Prerequisites.*2030-2031 Hammond catalog requirements/,
      })
      .first(),
  ).toHaveAttribute("href", "https://catalog.pnw.edu/__test__/us4/syn-35000/#requirements");
  expect(messageResponses).toEqual([200, 200, 200, 200, 200]);
});

test("shows ordered plan-of-study steps and keeps advisor approval as a boundary", async ({
  page,
}) => {
  await openChatAndAsk(page, PLAN_QUESTION);
  await chooseContext(page, "Synthetic Data Science, MS", "Program");
  await chooseContext(page, "Graduate", "Student level");
  await chooseContext(page, "2030-2031", "Catalog year");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(answer).toBeVisible();
  const steps = answer.getByRole("region", { name: "Published steps" });
  await expect(steps.getByRole("listitem")).toHaveCount(4);
  await expect(steps.getByRole("listitem")).toHaveText([
    /Review the published program requirements and draft the proposed course list\./,
    /Discuss the draft with the assigned academic advisor\./,
    /Obtain the advisor's approval of the proposed plan\./,
    /Submit the approved plan through the fictional Graduate Plan portal\./,
  ]);
  await expect(answer.getByRole("complementary", { name: "Source limitation" })).toContainText(
    "does not select courses for a student",
  );
  await expect(
    answer.getByRole("complementary", { name: "Academic advising boundary" }),
  ).toContainText("approve a plan of study");
  await expect(
    answer
      .getByRole("link", {
        name: /Synthetic Data Science MS Plan of Study.*Synthetic plan-of-study procedure.*Submission steps/,
      })
      .first(),
  ).toHaveAttribute("href", "https://www.pnw.edu/__test__/us4/plan-of-study/#submission-steps");
});

test("answers program existence only for the selected catalog scope", async ({ page }) => {
  await openChatAndAsk(page, PROGRAM_QUESTION);
  await chooseContext(page, "Synthetic Data Science, MS", "Program");
  await chooseContext(page, "Graduate", "Student level");
  await chooseContext(page, "2030-2031", "Catalog year");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(answer).toContainText(
    "The Synthetic Data Science, MS program is explicitly listed as offered",
  );
  await expect(answer.getByRole("region", { name: "Academic source conditions" })).toContainText(
    "2030-2031",
  );
  await expect(answer).not.toContainText("you are admitted");
});

test("does not infer program absence from an incomplete catalog inventory", async ({ page }) => {
  await openChatAndAsk(page, "INCOMPLETE-CATALOG-SENTINEL");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(
    answer.getByRole("heading", { name: "University source unavailable" }),
  ).toBeVisible();
  await expect(answer).toContainText("cannot provide a reliable answer");
  await expect(answer).not.toContainText("Synthetic Orbital Studies does not exist");
});

test("shows graduate-admission steps without deciding personal admission", async ({ page }) => {
  await openChatAndAsk(page, ADMISSION_QUESTION);
  await chooseContext(page, "Graduate", "Student level");
  await chooseContext(page, "2030-2031", "Catalog year");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(
    answer.getByRole("region", { name: "Published steps" }).getByRole("listitem"),
  ).toHaveCount(3);
  await expect(answer.getByRole("complementary", { name: "Source limitation" })).toContainText(
    "do not decide whether any person is admissible",
  );
});

test("shows graduation steps without deciding graduation status", async ({ page }) => {
  await openChatAndAsk(page, GRADUATION_QUESTION);
  await chooseContext(page, "Synthetic Data Science, MS", "Program");
  await chooseContext(page, "Graduate", "Student level");
  await chooseContext(page, "2030-2031", "Catalog year");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(
    answer.getByRole("region", { name: "Published steps" }).getByRole("listitem"),
  ).toHaveCount(4);
  await expect(answer.getByRole("complementary", { name: "Source limitation" })).toContainText(
    "does not establish that degree requirements are complete",
  );
});

test("does not treat a typical offering as current availability", async ({ page }) => {
  await openChatAndAsk(page, AVAILABILITY_QUESTION);
  await chooseContext(page, "Hammond", "Campus");
  await chooseContext(page, "Synthetic Computing, BS", "Program");
  await chooseContext(page, "Undergraduate", "Student level");
  await chooseContext(page, "2030-2031", "Catalog year");

  const answer = page.getByRole("article", { name: "Chatbot response" }).last();
  await expect(
    answer.getByRole("heading", { name: "Reliable information unavailable" }),
  ).toBeVisible();
  await expect(answer).toContainText("cannot provide a reliable answer");
  const referral = answer.getByRole("complementary", { name: "Verified university contact" });
  await expect(referral).toContainText("Synthetic Registrar Schedule Service");
  await expect(
    referral.getByRole("link", { name: "Open the synthetic current class schedule" }),
  ).toHaveAttribute("href", "https://www.pnw.edu/__test__/us4/current-class-schedule/");
  await expect(answer).not.toContainText("SYN 46000 is available now");
});
