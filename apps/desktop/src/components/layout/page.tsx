import { useId, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/lib/utils";

type HeaderContent = {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
};

/** Page root: stacks the header and the content of one route. */
function Page({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="page"
      className={cn("flex flex-col gap-6", className)}
      {...props}
    />
  );
}

/** The single h1 of a route, with an optional description and actions. */
function PageHeader({
  title,
  description,
  actions,
  className,
  children,
  ...props
}: HeaderContent & Omit<ComponentProps<"header">, "title">) {
  return (
    <header
      data-slot="page-header"
      className={cn("flex flex-col gap-4", className)}
      {...props}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1">
          <h1
            data-slot="page-title"
            className="text-xl font-semibold tracking-tight"
          >
            {title}
          </h1>
          {description && (
            <p
              data-slot="page-description"
              className="text-sm text-muted-foreground"
            >
              {description}
            </p>
          )}
        </div>
        {actions && (
          <div
            data-slot="page-actions"
            className="flex flex-wrap items-center gap-2"
          >
            {actions}
          </div>
        )}
      </div>
      {children}
    </header>
  );
}

function PageContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="page-content"
      className={cn("flex flex-col gap-5", className)}
      {...props}
    />
  );
}

/** Section-level header (h2). Use it when the content follows as siblings. */
function PageSectionHeader({
  title,
  description,
  actions,
  titleId,
  className,
  ...props
}: HeaderContent & { titleId?: string } & Omit<
    ComponentProps<"div">,
    "title" | "children"
  >) {
  return (
    <div
      data-slot="page-section-header"
      className={cn(
        "flex flex-wrap items-start justify-between gap-3",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1">
        <h2
          id={titleId}
          data-slot="page-section-title"
          className="text-base font-medium"
        >
          {title}
        </h2>
        {description && (
          <p
            data-slot="page-section-description"
            className="text-sm text-muted-foreground"
          >
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div
          data-slot="page-section-actions"
          className="flex flex-wrap items-center gap-2"
        >
          {actions}
        </div>
      )}
    </div>
  );
}

/** A titled region of a page, labelled by its h2. */
function PageSection({
  title,
  description,
  actions,
  className,
  children,
  ...props
}: HeaderContent & Omit<ComponentProps<"section">, "title">) {
  const titleId = useId();
  return (
    <section
      data-slot="page-section"
      aria-labelledby={titleId}
      className={cn("flex flex-col gap-4", className)}
      {...props}
    >
      <PageSectionHeader
        titleId={titleId}
        title={title}
        description={description}
        actions={actions}
      />
      {children}
    </section>
  );
}

export { Page, PageHeader, PageContent, PageSection, PageSectionHeader };
