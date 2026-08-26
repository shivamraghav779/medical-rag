import Modal from "../ui/Modal";

interface Props {
  open: boolean;
  message: string;
  onAccept: () => void;
}

export default function DisclaimerModal({ open, message, onAccept }: Props) {
  return (
    <Modal open={open} onClose={onAccept} title="Clinical Disclaimer" size="md">
      <p className="text-sm leading-relaxed text-oky-muted">{message}</p>
      <div className="mt-6 flex justify-end">
        <button type="button" onClick={onAccept} className="btn-primary">
          I understand
        </button>
      </div>
    </Modal>
  );
}
