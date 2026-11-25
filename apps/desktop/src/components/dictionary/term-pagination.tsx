import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
} from "@/components/ui/pagination";

/** Page numbers around the current one, with gaps as `null`. */
function pageWindow(current: number, count: number): (number | null)[] {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i + 1);
  const pages = new Set([1, count, current - 1, current, current + 1]);
  const sorted = [...pages]
    .filter((page) => page >= 1 && page <= count)
    .sort((a, b) => a - b);
  return sorted.flatMap((page, index) =>
    index > 0 && page - sorted[index - 1] > 1 ? [null, page] : [page],
  );
}

export function TermPagination({
  page,
  pageCount,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">
        {total
          ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} di ${total} parole`
          : "0 parole"}{" "}
        · Pagina {page} di {pageCount}
      </p>
      {pageCount > 1 && (
        <Pagination
          aria-label="Pagine del dizionario"
          className="mx-0 w-auto justify-end"
        >
          <PaginationContent>
            <PaginationItem>
              <Button
                variant="ghost"
                size="sm"
                disabled={page === 1}
                onClick={() => onPage(page - 1)}
              >
                <ChevronLeft data-icon="inline-start" />
                Precedente
              </Button>
            </PaginationItem>
            {pageWindow(page, pageCount).map((item, index) => (
              <PaginationItem key={item ?? `gap-${index}`}>
                {item === null ? (
                  <PaginationEllipsis />
                ) : (
                  <Button
                    variant={item === page ? "outline" : "ghost"}
                    size="icon-sm"
                    aria-label={`Pagina ${item}`}
                    aria-current={item === page ? "page" : undefined}
                    onClick={() => onPage(item)}
                  >
                    {item}
                  </Button>
                )}
              </PaginationItem>
            ))}
            <PaginationItem>
              <Button
                variant="ghost"
                size="sm"
                disabled={page === pageCount}
                onClick={() => onPage(page + 1)}
              >
                Successiva
                <ChevronRight data-icon="inline-end" />
              </Button>
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      )}
    </div>
  );
}
