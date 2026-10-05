import { createRoot } from "react-dom/client";

import { ChatPage } from "./chat";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing application root element");
}

createRoot(root).render(<ChatPage />);
