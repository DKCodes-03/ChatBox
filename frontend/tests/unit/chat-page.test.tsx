import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientError } from "../../src/api/client";
import { ChatPage } from "../../src/chat/ChatPage";
import type { ChatSessionApi } from "../../src/chat/session";
import type { AnswerEnvelope, ReasonCode } from "../../src/chat/types";

const timestamps = {
  expires_at: "2026-09-30T18:30:00Z",
  server_time: "2026-09-30T18:00:00Z",
};

function supportedAnswer(): AnswerEnvelope {
  return {
    outcome: "answer",
    segments: [
      {
        kind: "explanation",
        text: "Use the published parking appeal process.",
        citation_ids: ["parking"],
      },
      {
        kind: "step",
        text: "Open the appeal form.",
        citation_ids: ["parking"],
      },
      {
        kind: "step",
        text: "Submit the form by the listed deadline.",
        citation_ids: ["parking"],
      },
    ],
    citations: [
      {
        id: "parking",
        source_title: "Parking Regulations",
        url: "https://www.pnw.edu/parking",
        section: "Appeals",
        version_id: "018f8418-bcd6-7000-8000-000000000001",
      },
    ],
    context: {},
    clarification: null,
    referral: null,
    reason_code: null,
    ...timestamps,
  };
}

function unableAnswer(
  reasonCode: ReasonCode,
  overrides: Partial<AnswerEnvelope> = {},
): AnswerEnvelope {
  return {
    outcome: "unable",
    segments: [
      {
        kind: "limitation",
        text: "I cannot provide a reliable answer from the available university information.",
        citation_ids: [],
      },
    ],
    citations: [],
    context: {},
    clarification: null,
    referral: null,
    reason_code: reasonCode,
    ...timestamps,
    ...overrides,
  };
}

function chatClient(
  sendMessage: ChatSessionApi["sendMessage"] = vi.fn(async () => supportedAnswer()),
): ChatSessionApi {
  return {
    createSession: vi.fn(async () => timestamps),
    getSession: vi.fn(async () => timestamps),
    endSession: vi.fn(async () => undefined),
    sendMessage,
  };
}

async function waitForActiveChat() {
  const composer = await screen.findByLabelText("Ask a PNW question");
  await waitFor(() => expect(composer).toBeEnabled());
  return composer;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("ChatPage", () => {
  it("shows the scope and retention notice with a labeled composer", async () => {
    render(<ChatPage client={chatClient()} />);

    expect(screen.getByRole("heading", { name: "PNW Student Chatbot" })).toBeInTheDocument();
    expect(screen.getByText(/general PNW information/i)).toBeInTheDocument();
    expect(
      screen.getByText(/deleted when you end the chat or after 30 minutes/i),
    ).toBeInTheDocument();
    expect(await waitForActiveChat()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "End chat" })).toBeInTheDocument();
  });

  it("submits a question, shows loading, and renders ordered segments with claim citations", async () => {
    const pending = deferred<AnswerEnvelope>();
    const onSubmit = vi.fn(() => pending.promise);
    render(<ChatPage client={chatClient(onSubmit)} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "How do I appeal a parking ticket?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSubmit).toHaveBeenCalledWith(
      { message: "How do I appeal a parking ticket?" },
      { signal: expect.any(AbortSignal) },
    );
    expect(screen.getByText("How do I appeal a parking ticket?")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Looking for supported information");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    pending.resolve(supportedAnswer());

    const response = await screen.findByRole("article", { name: "Chatbot response" });
    expect(
      within(response).getByText("Use the published parking appeal process."),
    ).toBeInTheDocument();
    const steps = within(response).getAllByRole("listitem");
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveTextContent("Open the appeal form.");
    expect(steps[1]).toHaveTextContent("Submit the form by the listed deadline.");

    const links = within(response).getAllByRole("link", { name: /Parking Regulations/ });
    expect(links).toHaveLength(3);
    expect(links[0]).toHaveAttribute("href", "https://www.pnw.edu/parking");
    expect(links[0]).toHaveAttribute("rel", "noreferrer noopener");
  });

  it("renders a clarification question and its choices without inventing an answer", async () => {
    const answer: AnswerEnvelope = {
      outcome: "clarification",
      segments: [],
      citations: [],
      context: {},
      clarification: {
        question: "Which campus should I use?",
        fields: ["campus"],
        options: ["Hammond", "Westville"],
      },
      referral: null,
      reason_code: "context_required",
      ...timestamps,
    };
    render(<ChatPage client={chatClient(vi.fn().mockResolvedValue(answer))} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "When is the deadline?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Which campus should I use?")).toBeInTheDocument();
    expect(screen.getByText("Hammond")).toBeInTheDocument();
    expect(screen.getByText("Westville")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Chatbot response" })).not.toBeInTheDocument();
  });

  it("submits a clarification choice as explicit context and shows the selected context", async () => {
    const clarification: AnswerEnvelope = {
      outcome: "clarification",
      segments: [],
      citations: [],
      context: {},
      clarification: {
        question: "Which campus should I use?",
        fields: ["campus"],
        options: ["Hammond", "Westville"],
      },
      referral: null,
      reason_code: "context_required",
      ...timestamps,
    };
    const resolved = supportedAnswer();
    resolved.context = { campus: "Hammond" };
    const onSubmit = vi.fn().mockResolvedValueOnce(clarification).mockResolvedValueOnce(resolved);
    render(<ChatPage client={chatClient(onSubmit)} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "When is the deadline?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use Hammond for Campus" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenLastCalledWith(
        { message: "When is the deadline?", context: { campus: "Hammond" } },
        { signal: expect.any(AbortSignal) },
      ),
    );
    expect(screen.getByText("Campus: Hammond")).toBeInTheDocument();
    const context = await screen.findByRole("complementary", { name: "Answer context" });
    expect(within(context).getByText("Hammond")).toBeInTheDocument();
    expect(within(context).getByRole("button", { name: "Change campus" })).toBeInTheDocument();
  });

  it("rechecks an answer after a context correction and labels a past deadline", async () => {
    const hammond = supportedAnswer();
    hammond.context = { campus: "Hammond", term: "Fall", year: "2030" };
    hammond.expires_at = "2030-09-20T18:30:00Z";
    hammond.server_time = "2030-09-20T18:00:00Z";
    hammond.segments = [
      {
        kind: "explanation",
        text: "The 80 percent refund deadline was September 13, 2030.",
        citation_ids: ["parking"],
      },
    ];
    const westville = {
      ...hammond,
      context: { ...hammond.context, campus: "Westville" },
    };
    const onSubmit = vi.fn().mockResolvedValueOnce(hammond).mockResolvedValueOnce(westville);
    render(<ChatPage client={chatClient(onSubmit)} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "What is the refund deadline?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Past deadline")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change campus" }));
    fireEvent.change(screen.getByLabelText("New campus"), {
      target: { value: "Westville" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenLastCalledWith(
        {
          message: "What is the refund deadline?",
          context: { campus: "Westville", term: "Fall", year: "2030" },
        },
        { signal: expect.any(AbortSignal) },
      ),
    );
    expect(screen.getByText("Campus corrected to Westville")).toBeInTheDocument();
    const contexts = await screen.findAllByRole("complementary", { name: "Answer context" });
    expect(within(contexts.at(-1)!).getByText("Westville")).toBeInTheDocument();
  });

  it("uses a fixed safe error and retries only after the student selects Retry", async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValueOnce(new Error("provider request id: secret-123"))
      .mockResolvedValueOnce(supportedAnswer());
    render(<ChatPage client={chatClient(onSubmit)} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "Where can I find the procedure?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not get a response. Your question was not retried.");
    expect(alert).not.toHaveTextContent("secret-123");
    expect(onSubmit).toHaveBeenCalledTimes(1);

    await Promise.resolve();
    expect(onSubmit).toHaveBeenCalledTimes(1);

    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit).toHaveBeenLastCalledWith(
      { message: "Where can I find the procedure?" },
      { signal: expect.any(AbortSignal) },
    );
    expect(
      await screen.findByText("Use the published parking appeal process."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Where can I find the procedure?")).toHaveLength(1);
  });

  it("renders a source-conflict limitation with its university citations and no retry", async () => {
    const answer = unableAnswer("conflict", {
      segments: [
        {
          kind: "limitation",
          text: "I cannot provide a definitive answer because the university sources conflict.",
          citation_ids: ["source-a", "source-b"],
        },
      ],
      citations: [
        {
          id: "source-a",
          source_title: "Current Policy A",
          url: "https://www.pnw.edu/policy-a",
          version_id: "018f8418-bcd6-7000-8000-000000000002",
        },
        {
          id: "source-b",
          source_title: "Current Policy B",
          url: "https://www.pnw.edu/policy-b",
          version_id: "018f8418-bcd6-7000-8000-000000000003",
        },
      ],
    });
    render(<ChatPage client={chatClient(vi.fn().mockResolvedValue(answer))} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "Which policy applies?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const response = await screen.findByRole("article", { name: "Chatbot response" });
    expect(
      within(response).getByRole("heading", { name: "University sources disagree" }),
    ).toBeInTheDocument();
    expect(within(response).getByRole("link", { name: "Current Policy A" })).toHaveAttribute(
      "href",
      "https://www.pnw.edu/policy-a",
    );
    expect(within(response).getByRole("link", { name: "Current Policy B" })).toHaveAttribute(
      "href",
      "https://www.pnw.edu/policy-b",
    );
    expect(within(response).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("renders only the evidence-backed referral supplied by the API", async () => {
    const answer = unableAnswer("personal_case", {
      segments: [
        {
          kind: "limitation",
          text: "I cannot access student records or determine the result of a personal case.",
          citation_ids: [],
        },
      ],
      citations: [
        {
          id: "registrar-contact",
          source_title: "Office of the Registrar",
          url: "https://www.pnw.edu/registrar/contact",
          version_id: "018f8418-bcd6-7000-8000-000000000004",
        },
      ],
      referral: {
        office_name: "Office of the Registrar",
        contact_label: "Email the Registrar",
        contact_url: "mailto:registrar@pnw.edu",
        citation_ids: ["registrar-contact"],
      },
    });
    render(<ChatPage client={chatClient(vi.fn().mockResolvedValue(answer))} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "Why is there a registration hold on my record?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const referral = await screen.findByRole("complementary", {
      name: "Verified university contact",
    });
    expect(
      within(referral).getByText("Office of the Registrar", {
        selector: ".chat-failure__office",
      }),
    ).toBeInTheDocument();
    expect(within(referral).getByRole("link", { name: "Email the Registrar" })).toHaveAttribute(
      "href",
      "mailto:registrar@pnw.edu",
    );
    expect(within(referral).getByRole("link", { name: "Office of the Registrar" })).toHaveAttribute(
      "href",
      "https://www.pnw.edu/registrar/contact",
    );
  });

  it("presents academic requirements, source conditions, boundaries, and referrals", async () => {
    const answer: AnswerEnvelope = {
      outcome: "partial",
      segments: [
        {
          kind: "explanation",
          text: "The published catalog lists these requirements for SYN 35000.",
          citation_ids: ["catalog"],
        },
        {
          kind: "step",
          text: "Either complete SYN 21000 with a minimum grade of B and SYN 22000 with C.",
          citation_ids: ["catalog"],
        },
        {
          kind: "step",
          text: "Alternatively, complete SYN 23000 with a minimum grade of B.",
          citation_ids: ["catalog"],
        },
        {
          kind: "limitation",
          text: "The catalog does not confirm current course availability.",
          citation_ids: ["catalog"],
        },
      ],
      citations: [
        {
          id: "catalog",
          source_title: "Synthetic Course Catalog",
          url: "https://catalog.pnw.edu/__test__/us4/syn-35000/",
          section: "Requirements",
          version_id: "24000000-0000-4000-8000-000000000003",
        },
        {
          id: "schedule",
          source_title: "Synthetic Current Schedule",
          url: "https://www.pnw.edu/__test__/us4/current-class-schedule/",
          version_id: "24000000-0000-4000-8000-000000000007",
        },
      ],
      context: {
        campus: "Hammond",
        program: "Synthetic Computing, BS",
        student_level: "Undergraduate",
        catalog_year: "2030-2031",
      },
      clarification: null,
      referral: {
        office_name: "Synthetic Registrar Schedule Service",
        contact_label: "Open the synthetic current class schedule",
        contact_url: "https://www.pnw.edu/__test__/us4/current-class-schedule/",
        citation_ids: ["schedule"],
      },
      reason_code: null,
      ...timestamps,
    };
    render(<ChatPage client={chatClient(vi.fn().mockResolvedValue(answer))} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "What are the SYN 35000 requirements?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const response = await screen.findByRole("article", { name: "Chatbot response" });
    expect(
      within(response).getByRole("heading", { name: "Published prerequisite groups" }),
    ).toBeInTheDocument();
    expect(
      within(response).getByRole("region", { name: "Published prerequisite groups" }),
    ).toHaveTextContent("Either complete SYN 21000");
    expect(
      within(response).getByRole("region", { name: "Academic source conditions" }),
    ).toHaveTextContent("2030-2031");
    expect(
      within(response).getByRole("complementary", { name: "Source limitation" }),
    ).toHaveTextContent("does not confirm current course availability");
    expect(
      within(response).getByRole("complementary", { name: "Academic advising boundary" }),
    ).toHaveTextContent("cannot determine your personal course eligibility");
    const referral = within(response).getByRole("complementary", {
      name: "Verified university next step",
    });
    expect(referral).toHaveTextContent("Synthetic Registrar Schedule Service");
    expect(
      within(referral).getByRole("link", { name: "Open the synthetic current class schedule" }),
    ).toHaveAttribute("href", "https://www.pnw.edu/__test__/us4/current-class-schedule/");
    expect(
      within(referral).getByRole("link", { name: "Synthetic Current Schedule" }),
    ).toHaveAttribute("href", "https://www.pnw.edu/__test__/us4/current-class-schedule/");
  });

  it("does not automatically resubmit after a provider outage and retries once on request", async () => {
    const onSubmit = vi
      .fn()
      .mockResolvedValueOnce(
        unableAnswer("processing_unavailable", {
          segments: [
            {
              kind: "limitation",
              text: "The answer service is temporarily unavailable. Please try again later.",
              citation_ids: [],
            },
          ],
        }),
      )
      .mockResolvedValueOnce(supportedAnswer());
    render(<ChatPage client={chatClient(onSubmit)} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "How do I appeal a parking ticket?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const outage = await screen.findByRole("heading", {
      name: "Answer service temporarily unavailable",
    });
    expect(outage).toBeInTheDocument();
    expect(onSubmit).toHaveBeenCalledTimes(1);

    await Promise.resolve();
    expect(onSubmit).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    expect(onSubmit).toHaveBeenLastCalledWith(
      { message: "How do I appeal a parking ticket?" },
      { signal: expect.any(AbortSignal) },
    );
    expect(
      await screen.findByText("Use the published parking appeal process."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("How do I appeal a parking ticket?")).toHaveLength(1);
  });

  it("renders answer text as text rather than untrusted HTML", async () => {
    const answer = supportedAnswer();
    answer.segments[0] = {
      ...answer.segments[0],
      text: '<img src=x onerror="alert(1)">Read the source.',
    };
    render(<ChatPage client={chatClient(vi.fn().mockResolvedValue(answer))} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "Show source" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/<img src=x/)).toBeInTheDocument();
    expect(document.querySelector("img")).not.toBeInTheDocument();
  });

  it("clears the transcript immediately when End chat is selected", async () => {
    const client = chatClient();
    render(<ChatPage client={client} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "A distinctive question" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(
      await screen.findByText("Use the published parking appeal process."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "End chat" }));

    expect(screen.queryByText("A distinctive question")).not.toBeInTheDocument();
    expect(screen.queryByText("Use the published parking appeal process.")).not.toBeInTheDocument();
    expect(await screen.findByText("Your previous conversation is closed.")).toBeInTheDocument();
    expect(client.endSession).toHaveBeenCalledTimes(1);
  });

  it("shows an expiry notice and requires a fresh submission in a new session", async () => {
    const sendMessage = vi.fn().mockRejectedValue(
      new ApiClientError({
        code: "session_expired",
        message: "The session has ended.",
        status: 410,
        retryable: false,
      }),
    );
    const client = chatClient(sendMessage);
    render(<ChatPage client={client} />);
    await waitForActiveChat();

    fireEvent.change(screen.getByLabelText("Ask a PNW question"), {
      target: { value: "Do not submit me again" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/ended after 30 minutes/i)).toBeInTheDocument();
    expect(screen.queryByText("Do not submit me again")).not.toBeInTheDocument();
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(client.createSession).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Start new chat" }));
    await waitForActiveChat();

    expect(client.createSession).toHaveBeenCalledTimes(2);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Ask a PNW question")).toHaveValue("");
  });
});
