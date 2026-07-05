interface ShinyStatusProps {
  children: string;
  active?: boolean;
}

export function ShinyStatus({ children, active = false }: ShinyStatusProps) {
  return (
    <span className={active ? 'shiny-status active' : 'shiny-status'}>
      {children}
    </span>
  );
}
