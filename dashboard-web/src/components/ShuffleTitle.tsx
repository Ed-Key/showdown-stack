import { useEffect, useMemo, useState } from 'react';

interface ShuffleTitleProps {
  text: string;
  className?: string;
}

const SHUFFLE_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';

export function ShuffleTitle({ text, className = '' }: ShuffleTitleProps) {
  const [displayText, setDisplayText] = useState(text);
  const [ready, setReady] = useState(false);
  const characters = useMemo(() => text.split(''), [text]);

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      setDisplayText(text);
      setReady(true);
      return;
    }

    let frame = 0;
    let cancelled = false;
    const totalFrames = 18;
    const intervalMs = 34;
    setReady(false);

    const timer = window.setInterval(() => {
      if (cancelled) return;
      frame += 1;
      const settledCount = Math.floor((frame / totalFrames) * characters.length);
      const next = characters.map((char, index) => {
        if (char === ' ') return ' ';
        if (index < settledCount || frame >= totalFrames) return char;
        return SHUFFLE_CHARS[Math.floor(Math.random() * SHUFFLE_CHARS.length)];
      });
      setDisplayText(next.join(''));
      if (frame >= totalFrames) {
        setReady(true);
        window.clearInterval(timer);
      }
    }, intervalMs);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [characters, text]);

  return (
    <span className={`shuffle-title ${ready ? 'is-ready' : 'is-animating'} ${className}`} aria-label={text}>
      <span className="shuffle-title-baseline" aria-hidden="true">{text}</span>
      <span className="shuffle-title-live" aria-hidden="true">{displayText}</span>
    </span>
  );
}
