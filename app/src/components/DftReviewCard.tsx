// The chat's compact card for a dft_review (#74): what dftReview.ts reads out
// of the review, with a link to DFT Workbench's own cards for the rest.

import { type DftReview, reviewRows, reviewTitle } from "../dftReview";

export function DftReviewCard({ review, onOpen }: { review: DftReview; onOpen: () => void }) {
  const warnings = review.warnings.filter((warning) => warning.level !== "info").length;
  return (
    <div className="review-card">
      <div className="review-card-title">{reviewTitle(review)}</div>
      {review.blocking.length > 0 && (
        <div className="review-card-blocked">
          {review.blocking.map((finding) => (
            <div key={finding.summary}>Needs a decision: {finding.summary}</div>
          ))}
        </div>
      )}
      <table className="review-card-rows">
        <tbody>
          {reviewRows(review).map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td>{row.value}</td>
              <td className="review-card-source">{row.source ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="review-card-footer">
        <span>{warnings === 0 ? "No warnings" : `${warnings} warning${warnings === 1 ? "" : "s"}`}</span>
        <button className="secondary-btn compact" onClick={onOpen}>
          Open in DFT Workbench
        </button>
      </div>
    </div>
  );
}
