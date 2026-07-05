import { useEffect, useRef } from 'react';

interface DashboardBackgroundProps {
  borderColor?: string;
  fillColor?: string;
  hexSize?: number;
  speed?: number;
  hoverTrailAmount?: number;
}

export function DashboardBackground({
  borderColor = 'rgba(255, 216, 61, 0.14)',
  fillColor = 'rgba(255, 216, 61, 0.12)',
  hexSize = 38,
  speed = 0.1,
  hoverTrailAmount = 3,
}: DashboardBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const offsetRef = useRef({ x: 0, y: 0 });
  const activeHexRef = useRef<{ x: number; y: number } | null>(null);
  const trailRef = useRef<Array<{ x: number; y: number }>>([]);
  const fillMapRef = useRef(new Map<string, number>());

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrame = 0;
    let cancelled = false;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const step = reducedMotion ? 0 : Math.max(speed, 0.05);
    const spacingX = hexSize * 1.5;
    const spacingY = hexSize * Math.sqrt(3);

    const resize = () => {
      const ratio = window.devicePixelRatio || 1;
      const width = canvas.offsetWidth;
      const height = canvas.offsetHeight;
      canvas.width = Math.max(1, Math.floor(width * ratio));
      canvas.height = Math.max(1, Math.floor(height * ratio));
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    const drawHex = (x: number, y: number, radius: number) => {
      ctx.beginPath();
      for (let index = 0; index < 6; index += 1) {
        const angle = (Math.PI / 3) * index;
        const px = x + radius * Math.cos(angle);
        const py = y + radius * Math.sin(angle);
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
    };

    const updateTrail = () => {
      const targets = new Map<string, number>();
      if (activeHexRef.current) {
        targets.set(`${activeHexRef.current.x},${activeHexRef.current.y}`, 1);
      }
      for (let index = 0; index < trailRef.current.length; index += 1) {
        const hex = trailRef.current[index];
        const key = `${hex.x},${hex.y}`;
        if (!targets.has(key)) {
          targets.set(key, (trailRef.current.length - index) / (trailRef.current.length + 1));
        }
      }
      for (const key of targets.keys()) {
        if (!fillMapRef.current.has(key)) fillMapRef.current.set(key, 0);
      }
      for (const [key, alpha] of fillMapRef.current.entries()) {
        const target = targets.get(key) || 0;
        const next = alpha + (target - alpha) * 0.14;
        if (next < 0.004) fillMapRef.current.delete(key);
        else fillMapRef.current.set(key, next);
      }
    };

    const draw = () => {
      const width = canvas.offsetWidth;
      const height = canvas.offsetHeight;
      ctx.clearRect(0, 0, width, height);
      ctx.lineWidth = 1;

      const gridOffsetX = ((offsetRef.current.x % spacingX) + spacingX) % spacingX;
      const gridOffsetY = ((offsetRef.current.y % spacingY) + spacingY) % spacingY;
      const columnOffset = Math.floor(offsetRef.current.x / spacingX);
      const columns = Math.ceil(width / spacingX) + 4;
      const rows = Math.ceil(height / spacingY) + 4;

      for (let column = -2; column < columns; column += 1) {
        for (let row = -2; row < rows; row += 1) {
          const x = column * spacingX + gridOffsetX;
          const y = row * spacingY + ((column + columnOffset) % 2 !== 0 ? spacingY / 2 : 0) + gridOffsetY;
          const key = `${column},${row}`;
          const fillAlpha = fillMapRef.current.get(key);
          if (fillAlpha) {
            drawHex(x, y, hexSize);
            ctx.globalAlpha = fillAlpha;
            ctx.fillStyle = fillColor;
            ctx.fill();
            ctx.globalAlpha = 1;
          }
          drawHex(x, y, hexSize);
          ctx.strokeStyle = borderColor;
          ctx.stroke();
        }
      }
    };

    const tick = () => {
      if (cancelled) return;
      offsetRef.current.x = (offsetRef.current.x - step + spacingX * 2) % (spacingX * 2);
      offsetRef.current.y = (offsetRef.current.y - step + spacingY) % spacingY;
      updateTrail();
      draw();
      animationFrame = window.requestAnimationFrame(tick);
    };

    const handlePointerMove = (event: PointerEvent) => {
      const bounds = canvas.getBoundingClientRect();
      const pointerX = event.clientX - bounds.left;
      const pointerY = event.clientY - bounds.top;
      const gridOffsetX = ((offsetRef.current.x % spacingX) + spacingX) % spacingX;
      const gridOffsetY = ((offsetRef.current.y % spacingY) + spacingY) % spacingY;
      const columnOffset = Math.floor(offsetRef.current.x / spacingX);
      const column = Math.round((pointerX - gridOffsetX) / spacingX);
      const rowShift = (column + columnOffset) % 2 !== 0 ? spacingY / 2 : 0;
      const row = Math.round((pointerY - gridOffsetY - rowShift) / spacingY);

      const previous = activeHexRef.current;
      if (!previous || previous.x !== column || previous.y !== row) {
        if (previous && hoverTrailAmount > 0) {
          trailRef.current.unshift(previous);
          trailRef.current = trailRef.current.slice(0, hoverTrailAmount);
        }
        activeHexRef.current = { x: column, y: row };
      }
    };

    const handlePointerLeave = () => {
      if (activeHexRef.current && hoverTrailAmount > 0) {
        trailRef.current.unshift(activeHexRef.current);
        trailRef.current = trailRef.current.slice(0, hoverTrailAmount);
      }
      activeHexRef.current = null;
    };

    resize();
    draw();
    window.addEventListener('resize', resize);
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('blur', handlePointerLeave);
    document.addEventListener('mouseleave', handlePointerLeave);
    animationFrame = window.requestAnimationFrame(tick);

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('blur', handlePointerLeave);
      document.removeEventListener('mouseleave', handlePointerLeave);
    };
  }, [borderColor, fillColor, hexSize, hoverTrailAmount, speed]);

  return (
    <div className="dashboard-background" aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}
