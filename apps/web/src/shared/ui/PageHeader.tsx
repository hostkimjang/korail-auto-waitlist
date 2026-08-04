import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: string;
  helper?: string;
  action?: ReactNode;
}

export function PageHeader({ title, helper, action }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {helper && <p>{helper}</p>}
      </div>
      {action}
    </header>
  );
}
