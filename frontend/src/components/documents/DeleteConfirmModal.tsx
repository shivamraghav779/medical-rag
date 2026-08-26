import Modal from "../ui/Modal";

interface Props {
  open: boolean;
  docName: string;
  onConfirm: () => void;
  onCancel: () => void;
  loading?: boolean;
}

export default function DeleteConfirmModal({
  open,
  docName,
  onConfirm,
  onCancel,
  loading,
}: Props) {
  return (
    <Modal open={open} onClose={onCancel} title="Delete Document" size="sm">
      <p className="text-sm text-oky-muted">
        Permanently delete <strong className="text-oky-text">{docName}</strong> and
        all indexed chunks from Pinecone and Redis?
      </p>
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-secondary" disabled={loading}>
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="inline-flex items-center rounded-lg bg-red-500/90 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-500 disabled:opacity-50"
          disabled={loading}
        >
          {loading ? "Deleting…" : "Delete"}
        </button>
      </div>
    </Modal>
  );
}
