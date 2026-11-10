import { useId } from "react";
import type { LearningResult } from "@/lib/types";

type Props = {
  result: LearningResult;
  onApprove: () => void;
  onDecline: () => void;
};

export default function LearningPlanApproval({ result, onApprove, onDecline }: Props) {
  const titleId = useId();
  return (
    <section className="card" aria-labelledby={titleId}>
      <h2 id={titleId}>Review computed schedule changes</h2>
      <p>
        The generated plan’s summary budget, skill-gap table, and weekly schedule will be replaced with
        these values calculated from the selected requirements. Do you approve?
      </p>
      <strong>{result.computed_budget}</strong>
      <ul>
        {result.gaps.map((gap) => (
          <li key={gap.skill}>{gap.skill}: level {gap.current} to {gap.target} (gap {gap.delta})</li>
        ))}
      </ul>
      <ul>
        {result.computed_schedule?.map((line) => <li key={line}>{line}</li>)}
      </ul>
      <div className="row">
        <button className="btn" onClick={onApprove}>Approve and use this schedule</button>
        <button className="btn ghost" onClick={onDecline}>Decline and use template</button>
      </div>
    </section>
  );
}
