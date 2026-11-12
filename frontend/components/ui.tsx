"use client";

// Componentes de UI reutilizables (estilo estudio audiovisual: oscuro, minimalista).
// Sin lógica de negocio: solo presentación coherente para toda la app.

import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-white text-neutral-950 hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400",
  secondary:
    "border border-neutral-700 bg-neutral-900 text-neutral-100 hover:border-neutral-500 disabled:opacity-50",
  ghost:
    "text-neutral-300 hover:bg-neutral-800 disabled:opacity-50",
  danger:
    "border border-red-900/60 bg-red-950/40 text-red-300 hover:bg-red-900/40 disabled:opacity-50",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
}

export function Button({
  variant = "primary",
  loading = false,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-neutral-500 disabled:cursor-not-allowed ${VARIANT_CLASSES[variant]} ${className}`}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
      aria-hidden="true"
    />
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-neutral-800 bg-neutral-900/60 p-5 ${className}`}
    >
      {children}
    </div>
  );
}

export function Field({
  label,
  htmlFor,
  children,
  hint,
}: {
  label: string;
  htmlFor?: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-neutral-300">{label}</span>
      {children}
      {hint && <span className="text-xs text-neutral-500">{hint}</span>}
    </label>
  );
}

const INPUT_CLASSES =
  "w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none focus:ring-1 focus:ring-neutral-500";

export function TextInput(
  props: React.InputHTMLAttributes<HTMLInputElement>,
) {
  const { className = "", ...rest } = props;
  return <input className={`${INPUT_CLASSES} ${className}`} {...rest} />;
}

export function TextArea(
  props: React.TextareaHTMLAttributes<HTMLTextAreaElement>,
) {
  const { className = "", ...rest } = props;
  return <textarea className={`${INPUT_CLASSES} ${className}`} {...rest} />;
}

export function Select(
  props: React.SelectHTMLAttributes<HTMLSelectElement>,
) {
  const { className = "", children, ...rest } = props;
  return (
    <select className={`${INPUT_CLASSES} ${className}`} {...rest}>
      {children}
    </select>
  );
}

type StatusTone = "idle" | "progress" | "success" | "error";

const STATUS_TONE: Record<StatusTone, string> = {
  idle: "bg-neutral-800 text-neutral-300",
  progress: "bg-blue-950/60 text-blue-300",
  success: "bg-green-950/60 text-green-300",
  error: "bg-red-950/60 text-red-300",
};

export function StatusBadge({
  tone,
  children,
}: {
  tone: StatusTone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_TONE[tone]}`}
    >
      {children}
    </span>
  );
}

export function ProgressBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
      <div
        className="h-full rounded-full bg-white transition-[width] duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300">
      {message}
    </div>
  );
}

/** Mapea el estado textual de un Job/entidad a un tono visual. */
export function statusTone(status: string): StatusTone {
  switch (status) {
    case "succeeded":
    case "done":
    case "ready":
      return "success";
    case "failed":
    case "error":
      return "error";
    case "running":
    case "queued":
    case "pending":
    case "in_progress":
      return "progress";
    default:
      return "idle";
  }
}
