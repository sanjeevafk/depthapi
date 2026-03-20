import { Suspense, lazy } from "react";
import type { MermaidDiagramProps } from "./MermaidDiagramRenderer";

const MermaidDiagramRenderer = lazy(() => import("./MermaidDiagramRenderer"));

export type { MermaidDiagramProps };

export default function MermaidDiagram(
  props: MermaidDiagramProps,
): JSX.Element {
  return (
    <Suspense
      fallback={
        <div className="rounded-lg border border-white/5 bg-dark-900/40 p-4 text-xs text-gray-500">
          Rendering diagram...
        </div>
      }
    >
      <MermaidDiagramRenderer {...props} />
    </Suspense>
  );
}
