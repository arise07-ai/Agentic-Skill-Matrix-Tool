"use client";

import { useEffect, useState } from "react";

export default function PageLoading() {
  const [progress, setProgress] = useState(4);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setProgress((current) => {
        if (current >= 94) return current;
        const step = current < 45 ? 5 : current < 75 ? 2 : 1;
        return Math.min(current + step, 94);
      });
    }, 180);

    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="page-loading" role="status" aria-live="polite" aria-label="Loading page">
      <div className="page-loading__content">
        <div className="page-loading__brand">
          <span className="page-loading__mark" aria-hidden="true">
            <svg viewBox="0 0 28 28" focusable="false">
              <rect x="2" y="2" width="6" height="6" />
              <rect x="11" y="2" width="6" height="6" />
              <rect x="20" y="2" width="6" height="6" />
              <rect x="2" y="11" width="6" height="6" />
              <rect x="11" y="11" width="6" height="6" />
              <rect x="20" y="11" width="6" height="6" />
              <rect x="2" y="20" width="6" height="6" />
              <rect x="11" y="20" width="6" height="6" />
              <rect x="20" y="20" width="6" height="6" />
            </svg>
          </span>
          <span className="page-loading__name">Skill Matrix</span>
        </div>
        <div className="page-loading__status">
          <span>Loading your workspace</span>
          <span className="page-loading__percent" aria-hidden="true">{progress}%</span>
        </div>
        <div
          className="page-loading__track"
          role="progressbar"
          aria-label="Page loading progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <span className="page-loading__fill" style={{ width: `${progress}%` }} />
        </div>
      </div>
    </div>
  );
}
