import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  onTranscript: (text: string) => void;
  onError?: (message: string) => void;
}

function getSpeechRecognitionCtor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null;
}

/** Browser speech recognition (Chrome/Edge use Google STT under the hood). */
export function useSpeechInput({ onTranscript, onError }: Options) {
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const baseTextRef = useRef("");
  const [listening, setListening] = useState(false);
  const supported = getSpeechRecognitionCtor() !== null;

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setListening(false);
  }, []);

  const start = useCallback(
    (existingText = "") => {
      const Ctor = getSpeechRecognitionCtor();
      if (!Ctor) {
        onError?.("Voice input requires Chrome or Edge (uses Google speech recognition).");
        return;
      }

      stop();
      baseTextRef.current = existingText.trim();

      const recognition = new Ctor();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = "en-US";
      recognition.maxAlternatives = 1;

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        // Rebuild the full utterance from all result segments — never append to prior state.
        let spoken = "";
        for (let i = 0; i < event.results.length; i += 1) {
          spoken += event.results[i][0].transcript;
        }

        const combined = [baseTextRef.current, spoken.trim()]
          .filter(Boolean)
          .join(" ")
          .trim();

        onTranscript(combined);
      };

      recognition.onerror = (event: Event & { error?: string }) => {
        const code = event.error ?? "unknown";
        if (code === "no-speech") {
          onError?.("No speech detected. Try again.");
        } else if (code === "not-allowed") {
          onError?.("Microphone permission denied.");
        } else if (code !== "aborted") {
          onError?.("Could not capture voice input.");
        }
        setListening(false);
        recognitionRef.current = null;
      };

      recognition.onend = () => {
        setListening(false);
        recognitionRef.current = null;
      };

      recognitionRef.current = recognition;
      setListening(true);
      recognition.start();
    },
    [onError, onTranscript, stop],
  );

  const toggle = useCallback(
    (existingText = "") => {
      if (listening) {
        stop();
      } else {
        start(existingText);
      }
    },
    [listening, start, stop],
  );

  useEffect(() => () => stop(), [stop]);

  return { listening, supported, start, stop, toggle };
}
