import { X } from "lucide-react";
import type { ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}

const SIZES = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

export default function Modal({
  open,
  onClose,
  title,
  children,
  size = "md",
}: ModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-oky-purple-dark/20 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <div
        className={`glass-card relative z-10 flex max-h-[90vh] w-full flex-col shadow-glass ${SIZES[size]}`}
        role="dialog"
        aria-modal
      >
        <div className="flex items-center justify-between border-b border-oky-border/50 px-6 py-4">
          <h2 className="text-lg font-semibold text-oky-text">{title}</h2>
          <button type="button" onClick={onClose} className="btn-ghost p-1" aria-label="Close dialog">
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  );
}
