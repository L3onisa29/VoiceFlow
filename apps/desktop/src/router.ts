import { createHashHistory, createRouter } from "@tanstack/react-router";
import { Route as rootRoute } from "./routes/__root";
import { Route as indexRoute } from "./routes";
import { Route as dictionaryRoute } from "./routes/dictionary";
import { Route as suggestionsRoute } from "./routes/suggestions";
import { Route as analysisRoute } from "./routes/analysis";
import { Route as settingsRoute } from "./routes/settings";

// Keep navigation inside the Tauri WebView's document, including on reload.
export const router = createRouter({
  routeTree: rootRoute.addChildren([
    indexRoute,
    dictionaryRoute,
    settingsRoute,
    suggestionsRoute,
    analysisRoute,
  ]),
  history: createHashHistory(),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
