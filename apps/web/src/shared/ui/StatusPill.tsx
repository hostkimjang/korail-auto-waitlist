import type { ReactNode } from "react";

export interface StatusPillProps {
  status: string;
  children: ReactNode;
}

export function StatusPill({ status, children }: StatusPillProps) {
  return (
    <span className={`status-pill status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}
