import { useState } from "react";
import Modal from "../ui/Modal";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (payload: { comment: string; correctAnswer: string }) => Promise<void>;
}

export default function FeedbackModal({ open, onClose, onSubmit }: Props) {
  const [comment, setComment] = useState("");
  const [correctAnswer, setCorrectAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClose = () => {
    if (submitting) return;
    setComment("");
    setCorrectAnswer("");
    setError(null);
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!comment.trim() && !correctAnswer.trim()) {
      setError("Please share a short note or the correct answer so we can improve.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        comment: comment.trim(),
        correctAnswer: correctAnswer.trim(),
      });
      setComment("");
      setCorrectAnswer("");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit feedback.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={handleClose} title="Help us improve" size="md">
      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm leading-relaxed text-oky-muted">
          Your feedback is valuable to us. Please tell us what went wrong and, if you
          can, share the correct answer so we can improve this clinical assistant.
        </p>

        <label className="block space-y-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-oky-muted">
            What was the issue?
          </span>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={3}
            placeholder="e.g. Missing first-line therapy, wrong guideline year, incomplete answer…"
            className="w-full resize-y rounded-xl border border-oky-border/70 bg-white/80 px-3 py-2.5 text-sm text-oky-text outline-none ring-oky-purple/30 placeholder:text-oky-muted/70 focus:ring-2"
          />
        </label>

        <label className="block space-y-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-oky-muted">
            Correct answer (optional)
          </span>
          <textarea
            value={correctAnswer}
            onChange={(e) => setCorrectAnswer(e.target.value)}
            rows={4}
            placeholder="Paste or write the answer you expected…"
            className="w-full resize-y rounded-xl border border-oky-border/70 bg-white/80 px-3 py-2.5 text-sm text-oky-text outline-none ring-oky-purple/30 placeholder:text-oky-muted/70 focus:ring-2"
          />
        </label>

        {error && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            className="btn-ghost"
          >
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="btn-primary">
            {submitting ? "Submitting…" : "Submit feedback"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
