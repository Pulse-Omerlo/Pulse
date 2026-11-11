// AI contribution: 50% or more AI-generated

// Layout only: goals and recommendations have no backend yet.
export function GoalsView() {
  return (
    <div className="columns">
      <section aria-label="Goals">
        <div className="history-heading">
          <h2>Goals</h2>
          <button type="button" disabled title="Coming soon">
            Add goal
          </button>
        </div>
        <p className="empty">No goals yet. Goals set by the network will appear here.</p>
      </section>

      <section aria-label="Daily recommendations">
        <h2>Daily recommendations</h2>
        <p className="muted">Suggestions toward your goals, refreshed every day.</p>
        <ul className="change-list">
          {[1, 2, 3].map((slot) => (
            <li key={slot} className="change placeholder">
              <span className="badge">recommendation</span>{" "}
              <span className="muted">Coming soon</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
