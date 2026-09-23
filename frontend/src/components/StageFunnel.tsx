type FunnelStage = {
  id: string;
  label: string;
  count: number;
};

type Props = {
  stages: FunnelStage[];
  active: string;
  onChange: (id: string) => void;
  ariaLabel: string;
};

export function StageFunnel({ stages, active, onChange, ariaLabel }: Props) {
  const total = stages.reduce((sum, stage) => sum + stage.count, 0);

  return (
    <div className="workflow-funnel" role="tablist" aria-label={ariaLabel}>
      {stages.map((stage) => {
        const share = total ? Math.round((stage.count / total) * 100) : 0;
        const selected = stage.id === active;
        return (
          <button
            key={stage.id}
            type="button"
            role="tab"
            aria-selected={selected}
            className={`workflow-funnel-step${selected ? " is-active" : ""}`}
            style={{ flexGrow: Math.max(stage.count, 1) }}
            onClick={() => onChange(stage.id)}
          >
            <span>{stage.label}</span>
            <strong>{stage.count}</strong>
            <small>{share}% от всех</small>
          </button>
        );
      })}
    </div>
  );
}
