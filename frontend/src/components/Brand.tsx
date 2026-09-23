type BrandProps = {
  compact?: boolean;
  subtitle?: string;
};

export function Brand({ compact = false, subtitle }: BrandProps) {
  return (
    <div
      className={`brand-block${compact ? " compact" : ""}`}
      title={compact ? "NEXTGENZ" : undefined}
    >
      <div className="brand-mark" aria-hidden>
        <img src="/logo.png?v=2" alt="" className="brand-logo" />
      </div>
      <div className="brand-text">
        <div className="brand">NEXTGENZ</div>
        {subtitle ? <div className="brand-sub">{subtitle}</div> : null}
      </div>
    </div>
  );
}
