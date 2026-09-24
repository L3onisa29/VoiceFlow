import { createRootRoute, Link, Outlet } from "@tanstack/react-router";

export const Route = createRootRoute({
  component: Outlet,
  notFoundComponent: () => (
    <main className="p-8">
      <h1 className="text-2xl font-semibold">Pagina non trovata</h1>
      <Link to="/" className="mt-4 inline-block underline">
        Torna alla dettatura
      </Link>
    </main>
  ),
});
