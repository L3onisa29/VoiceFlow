import type { ReactNode } from "react";
import { PageHeader, PageSection } from "@/components/layout/page";
import { FieldGroup } from "@/components/ui/field";

/**
 * One settings category: the page h1 (focus target of the sidebar navigation)
 * followed by its groups. `id` labels the region and receives focus.
 */
export function SettingsSection({
  id,
  title,
  description,
  actions,
  children,
}: {
  id: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-6">
      <PageHeader
        title={
          <span id={id} tabIndex={-1} className="outline-none">
            {title}
          </span>
        }
        description={description}
        actions={actions}
      />
      {children}
    </section>
  );
}

/** A titled group of settings rows inside a category. */
export function SettingsGroup({
  title,
  description,
  actions,
  children,
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const rows = <FieldGroup className="gap-0">{children}</FieldGroup>;
  if (!title) return rows;
  return (
    <PageSection title={title} description={description} actions={actions}>
      {rows}
    </PageSection>
  );
}
