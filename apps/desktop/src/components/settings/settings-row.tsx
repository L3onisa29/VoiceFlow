import type { ReactNode } from "react";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";

/**
 * One setting: label and a one-line description on the left, the control on
 * the right. Every control applies on change; `saving` shows the pending write
 * and `error` a validation message that kept the value from being sent.
 */
export function SettingsRow({
  htmlFor,
  label,
  description,
  disabled,
  saving,
  error,
  children,
}: {
  htmlFor: string;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  saving?: boolean;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <Field
      data-disabled={disabled}
      data-invalid={error ? true : undefined}
      className="gap-3 border-b py-4 last:border-b-0 @xl/field-group:flex-row @xl/field-group:items-center @xl/field-group:gap-6"
    >
      <FieldContent className="min-w-0">
        <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
        {description && (
          <FieldDescription id={`${htmlFor}-description`}>
            {description}
          </FieldDescription>
        )}
        {error && <FieldError id={`${htmlFor}-error`}>{error}</FieldError>}
      </FieldContent>
      <div className="flex shrink-0 items-center gap-2 @xl/field-group:w-60 @xl/field-group:justify-end">
        {saving && <Spinner aria-label="Salvataggio in corso" />}
        {children}
      </div>
    </Field>
  );
}
