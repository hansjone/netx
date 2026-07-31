type Props = {
  kind?: string;
};

/** Generic placeholder for unfinished network sub-pages. */
export function NetworkPlaceholderPage(_props: Props) {
  return (
    <section className="panel network-placeholder">
      <h2>Coming soon</h2>
      <p className="muted">This capability is not available yet.</p>
    </section>
  );
}
