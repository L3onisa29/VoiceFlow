import { createHashHistory, createRouter } from "@tanstack/react-router";
import { queryClient } from "./lib/query-client";
import { routeTree } from "./routeTree.gen";
import {
  RouteError,
  RouteNotFound,
  RoutePending,
} from "./components/layout/route-states";

export const router = createRouter({
  routeTree,
  // Keep navigation inside the Tauri WebView's document, including on reload.
  history: createHashHistory(),
  context: { queryClient },
  defaultPreload: "intent",
  // Loaders only warm the Query cache; Query owns freshness.
  defaultPreloadStaleTime: 0,
  defaultPendingComponent: RoutePending,
  defaultErrorComponent: RouteError,
  defaultNotFoundComponent: RouteNotFound,
  // Pages scroll inside the shell's #page-content, not the window.
  scrollRestoration: true,
  scrollToTopSelectors: ["#page-content"],
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
