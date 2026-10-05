import { expect, test, type Page, type Route } from "@playwright/test";

const timestamps = {
  expires_at: "2026-09-30T18:30:00Z",
  server_time: "2026-09-30T18:00:00Z",
};

interface MockResponse {
  status?: number;
  headers?: Record<string, string>;
  body: unknown;
}

type MessageResponder = (message: string, requestNumber: number) => MockResponse;

async function fulfillJson(route: Route, response: MockResponse) {
  await route.fulfill({
    status: response.status ?? 200,
    contentType: "application/json",
    headers: response.headers,
    body: JSON.stringify(response.body),
  });
}

async function mockChatApi(page: Page, respondToMessage: MessageResponder) {
  let messageRequests = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/v1/sessions" && method === "POST") {
      await fulfillJson(route, { status: 201, body: timestamps });
      return;
    }
    if (path === "/api/v1/session" && method === "GET") {
      await fulfillJson(route, { body: timestamps });
      return;
    }
    if (path === "/api/v1/session" && method === "DELETE") {
      await route.fulfill({ status: 204 });
      return;
    }
    if (path === "/api/v1/messages" && method === "POST") {
      messageRequests += 1;
      const payload = request.postDataJSON() as { message: string };
      await fulfillJson(route, respondToMessage(payload.message, messageRequests));
      return;
    }

    await fulfillJson(route, {
      status: 404,
      body: {
        error: { code: "not_found", message: "Not found.", retryable: false },
        server_time: timestamps.server_time,
      },
    });
  });

  return { messageRequestCount: () => messageRequests };
}

async function openChatAndAsk(page: Page, question: string) {
  await page.goto("/");
  const composer = page.getByLabel("Ask a PNW question");
  await expect(composer).toBeEnabled();
  await composer.fill(question);
  await page.getByRole("button", { name: "Send" }).click();
}

function unableAnswer(
  reasonCode: string,
  limitation: string,
  options: {
    citationIds?: string[];
    citations?: unknown[];
    referral?: unknown;
  } = {},
) {
  return {
    outcome: "unable",
    segments: [
      {
        kind: "limitation",
        text: limitation,
        citation_ids: options.citationIds ?? [],
      },
    ],
    citations: options.citations ?? [],
    context: {},
    clarification: null,
    referral: options.referral ?? null,
    reason_code: reasonCode,
    ...timestamps,
  };
}

test("shows a safe limitation without inventing an office or answer", async ({ page }) => {
  const question = "What is the unpublished synthetic policy?";
  await mockChatApi(page, () => ({
    body: unableAnswer(
      "missing_evidence",
      "I cannot provide a reliable answer from the available university information.",
    ),
  }));

  await openChatAndAsk(page, question);

  await expect(page.getByLabel("Your question")).toHaveText(question);
  const response = page.getByRole("article", { name: "Chatbot response" });
  await expect(
    response.getByRole("heading", { name: "Reliable information unavailable" }),
  ).toBeVisible();
  await expect(
    response.getByText(
      "I cannot provide a reliable answer from the available university information.",
    ),
  ).toBeVisible();
  await expect(response.getByLabel("Verified university contact")).toHaveCount(0);
  await expect(response.getByRole("link")).toHaveCount(0);
  await expect(response.getByRole("button", { name: "Retry" })).toHaveCount(0);
});

test("withholds a disputed conclusion and links both conflicting sources", async ({ page }) => {
  const question = "Which synthetic deadline applies?";
  await mockChatApi(page, () => ({
    body: unableAnswer(
      "conflict",
      "I cannot provide a definitive answer because the available university information conflicts.",
      {
        citationIds: ["conflict-a", "conflict-b"],
        citations: [
          {
            id: "conflict-a",
            source_title: "Synthetic Current Policy A",
            url: "https://www.pnw.edu/__test__/conflict-a",
            section: "Deadline",
            version_id: "34000000-0000-4000-8000-000000000001",
          },
          {
            id: "conflict-b",
            source_title: "Synthetic Current Policy B",
            url: "https://www.pnw.edu/__test__/conflict-b",
            section: "Deadline",
            version_id: "34000000-0000-4000-8000-000000000002",
          },
        ],
      },
    ),
  }));

  await openChatAndAsk(page, question);

  const response = page.getByRole("article", { name: "Chatbot response" });
  await expect(
    response.getByRole("heading", { name: "University sources disagree" }),
  ).toBeVisible();
  await expect(response.getByText(/cannot provide a definitive answer/i)).toBeVisible();
  await expect(response.getByText("The deadline is September 1.")).toHaveCount(0);
  await expect(response.getByRole("link", { name: /Synthetic Current Policy A/ })).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/conflict-a",
  );
  await expect(response.getByRole("link", { name: /Synthetic Current Policy B/ })).toHaveAttribute(
    "href",
    "https://www.pnw.edu/__test__/conflict-b",
  );
});

test("keeps a personal case boundary and shows only the verified referral", async ({ page }) => {
  const question = "Can you inspect my record and remove my synthetic registration hold?";
  await mockChatApi(page, () => ({
    body: unableAnswer(
      "personal_case",
      "I cannot access student records or determine the result of a personal case.",
      {
        citations: [
          {
            id: "verified-registrar-contact",
            source_title: "Synthetic Registrar Contact Directory",
            url: "https://www.pnw.edu/__test__/registrar-contact",
            section: "Registration help",
            version_id: "35000000-0000-4000-8000-000000000001",
          },
        ],
        referral: {
          office_name: "Synthetic Office of the Registrar",
          contact_label: "Email the synthetic Registrar",
          contact_url: "mailto:synthetic-registrar@example.edu",
          citation_ids: ["verified-registrar-contact"],
        },
      },
    ),
  }));

  await openChatAndAsk(page, question);

  const response = page.getByRole("article", { name: "Chatbot response" });
  await expect(
    response.getByRole("heading", { name: "Help with your personal case" }),
  ).toBeVisible();
  await expect(response.getByText(/cannot access student records/i)).toBeVisible();
  await expect(response.getByText(/removed your.*hold/i)).toHaveCount(0);

  const referral = response.getByRole("complementary", {
    name: "Verified university contact",
  });
  await expect(
    referral.getByText("Synthetic Office of the Registrar", { exact: true }),
  ).toBeVisible();
  await expect(
    referral.getByRole("link", { name: "Email the synthetic Registrar" }),
  ).toHaveAttribute("href", "mailto:synthetic-registrar@example.edu");
  await expect(
    referral.getByRole("link", { name: /Synthetic Registrar Contact Directory/ }),
  ).toHaveAttribute("href", "https://www.pnw.edu/__test__/registrar-contact");
});

test("does not resubmit after a provider failure until the student selects Retry", async ({
  page,
}) => {
  const question = "How do I complete the synthetic procedure?";
  const providerDetail = "provider-request-id-secret-123";
  const api = await mockChatApi(page, (_message, requestNumber) =>
    requestNumber === 1
      ? {
          status: 503,
          body: {
            error: {
              code: "processing_unavailable",
              message: providerDetail,
              retryable: true,
            },
            server_time: timestamps.server_time,
          },
        }
      : {
          body: unableAnswer(
            "source_unavailable",
            "I cannot provide a reliable answer because the relevant university source is unavailable.",
          ),
        },
  );

  await openChatAndAsk(page, question);

  const alert = page.getByRole("alert");
  await expect(
    alert.getByRole("heading", { name: "Answer service temporarily unavailable" }),
  ).toBeVisible();
  await expect(alert).toContainText("Your question was not retried.");
  await expect(page.getByText(providerDetail)).toHaveCount(0);
  expect(api.messageRequestCount()).toBe(1);

  await page.waitForTimeout(250);
  expect(api.messageRequestCount()).toBe(1);

  await alert.getByRole("button", { name: "Retry" }).click();
  await expect.poll(api.messageRequestCount).toBe(2);

  const response = page.getByRole("article", { name: "Chatbot response" });
  await expect(
    response.getByRole("heading", { name: "University source unavailable" }),
  ).toBeVisible();
  await expect(page.getByLabel("Your question")).toHaveCount(1);
  await expect(page.getByLabel("Your question")).toHaveText(question);
});
