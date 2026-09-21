import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";

describe("frontend project setup", () => {
  it("renders the application shell", () => {
    render(<App />);

    expect(screen.getByText("PNW Information Chatbot")).toBeInTheDocument();
  });
});
