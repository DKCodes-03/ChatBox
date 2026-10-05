import { describe, expect, it } from "vitest";

describe("frontend unit-test environment", () => {
  it("loads jsdom and Testing Library matchers", () => {
    const status = document.createElement("p");
    status.textContent = "Test environment ready";
    document.body.append(status);

    expect(status).toBeInTheDocument();
    expect(status).toHaveTextContent("Test environment ready");

    status.remove();
  });
});
