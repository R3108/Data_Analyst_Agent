"use client";

import { ArrowUp, Mic, ShieldCheck, Square } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

export interface ComposerHandle {
  focus: () => void;
  setValue: (value: string) => void;
}

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}

function speechRecognition(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const Composer = forwardRef<
  ComposerHandle,
  { onSend: (text: string) => void; onStop: () => void; streaming: boolean; placeholder: string; disabled?: boolean }
>(function Composer({ onSend, onStop, streaming, placeholder, disabled = false }, ref) {
  const [value, setValue] = useState("");
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const recognition = useRef<SpeechRecognitionLike | null>(null);

  useImperativeHandle(ref, () => ({
    focus: () => textarea.current?.focus(),
    setValue: (next: string) => {
      setValue(next);
      textarea.current?.focus();
    },
  }));

  useEffect(() => {
    setVoiceSupported(speechRecognition() !== null);
    return () => recognition.current?.stop();
  }, []);

  useLayoutEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming || disabled) return;
    recognition.current?.stop();
    onSend(text);
    setValue("");
  };

  const toggleVoice = () => {
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const Recognition = speechRecognition();
    if (!Recognition) return;
    const rec = new Recognition();
    const base = value.trim() ? `${value.trimEnd()} ` : "";
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    rec.onresult = (event) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i++) transcript += event.results[i][0].transcript;
      setValue(base + transcript);
    };
    rec.onend = () => {
      setListening(false);
      textarea.current?.focus();
    };
    rec.onerror = () => setListening(false);
    recognition.current = rec;
    rec.start();
    setListening(true);
  };

  return (
    <div className="mx-auto w-full max-w-3xl">
      <div
        className={cn(
          "flex items-end gap-2 rounded-2xl border border-line-strong bg-panel p-2 pl-4 shadow-pop transition",
          "focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent-soft",
          listening && "border-bad/50 ring-4 ring-bad-soft",
        )}
      >
        <textarea
          ref={textarea}
          rows={1}
          value={value}
          disabled={disabled}
          maxLength={4000}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={listening ? "Listening…" : placeholder}
          aria-label="Ask a question about your data"
          // The box is one line tall until you type, so a long placeholder must not wrap into it.
          className="max-h-[220px] min-h-[36px] flex-1 resize-none bg-transparent py-2 text-[15px] leading-relaxed text-ink outline-none placeholder:truncate placeholder:text-ink-3 focus-visible:outline-none disabled:cursor-not-allowed"
        />
        {voiceSupported && !streaming && !disabled && (
          <button
            onClick={toggleVoice}
            aria-label={listening ? "Stop dictation" : "Ask with your voice"}
            aria-pressed={listening}
            title={listening ? "Stop dictation" : "Ask with your voice"}
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-xl transition",
              listening ? "animate-pulse bg-bad-soft text-bad" : "text-ink-3 hover:bg-muted hover:text-ink",
            )}
          >
            <Mic className="size-4" />
          </button>
        )}
        {streaming ? (
          <button
            onClick={onStop}
            aria-label="Stop analysis"
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-ink text-canvas transition hover:opacity-85"
          >
            <Square className="size-3.5 fill-current" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!value.trim() || disabled}
            aria-label="Send question"
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition hover:bg-accent-hover disabled:bg-subtle disabled:text-ink-3"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
      </div>
      {/* Both hints stay on one line: the keyboard hint drops first when space is tight. */}
      <div className="mt-2 flex items-center justify-between gap-4 px-1 text-[11px] text-ink-3">
        <span className="hidden whitespace-nowrap lg:inline">
          Enter to send · Shift + Enter for a new line · ⌘K to search
        </span>
        <span className="flex shrink-0 items-center gap-1 whitespace-nowrap">
          <ShieldCheck className="size-3" />
          Code runs in an isolated sandbox
        </span>
      </div>
    </div>
  );
});
