import { useEffect, useState } from 'react';

interface CountUpValueProps {
  value: number | null | undefined;
  suffix?: string;
  decimals?: number;
  fallback?: string;
}

const COUNT_UP_MS = 720;

export function CountUpValue({
  value,
  suffix = '',
  decimals = 0,
  fallback = 'n/a',
}: CountUpValueProps) {
  const [displayValue, setDisplayValue] = useState(value ?? 0);

  useEffect(() => {
    if (value == null) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      setDisplayValue(value);
      return;
    }

    let animationFrame = 0;
    const startTime = performance.now();
    const startValue = 0;
    const endValue = value;

    const tick = (now: number) => {
      const progress = Math.min(1, (now - startTime) / COUNT_UP_MS);
      const eased = 1 - ((1 - progress) ** 3);
      setDisplayValue(startValue + ((endValue - startValue) * eased));
      if (progress < 1) {
        animationFrame = window.requestAnimationFrame(tick);
      }
    };

    animationFrame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [value]);

  if (value == null) return <>{fallback}</>;

  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  }).format(displayValue);

  return <>{formatted}{suffix}</>;
}
