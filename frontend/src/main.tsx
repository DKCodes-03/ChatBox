import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing application root element");
}

createRoot(root).render(
  <StrictMode>
    <main>
      <h1>PNW Student Chatbot</h1>
      <p>Chat is not available yet.</p>
    </main>
  </StrictMode>,
);
