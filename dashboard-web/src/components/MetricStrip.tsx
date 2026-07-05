import type { DashboardSummary } from '../types';
import { CountUpValue } from './CountUpValue';

interface MetricStripProps {
  summary: DashboardSummary | null;
}

export function MetricStrip({ summary }: MetricStripProps) {
  const metrics = [
    {
      label: 'Battles',
      value: summary?.finishedBattles,
      sub: `${summary?.wins || 0}W / ${summary?.losses || 0}L`,
    },
    {
      label: 'Win Rate',
      value: summary?.winRate,
      suffix: '%',
      decimals: 1,
      sub: `${summary?.unknownResults || 0} unknown`,
    },
    {
      label: 'Follow Rate',
      value: summary?.followRate,
      suffix: '%',
      decimals: 1,
      sub: `${summary?.followed || 0}/${summary?.followable || 0} matched`,
    },
    {
      label: 'PV Accuracy',
      value: summary?.pvHitRate,
      suffix: '%',
      decimals: 1,
      sub: `${summary?.pvHits || 0}/${summary?.pvKnown || 0} opponent moves`,
    },
    {
      label: 'Field Pressure',
      value: summary?.residualEvents,
      sub: `${summary?.hazardResidualEvents || 0} hazard / ${summary?.statusResidualEvents || 0} status`,
    },
    {
      label: 'Confidence',
      value: summary?.avgConfidence,
      suffix: '%',
      decimals: 1,
      sub: `${summary?.criticalTurns || 0} critical turns`,
    },
  ];

  return (
    <section className="metric-strip" aria-label="Battle analytics summary">
      {metrics.map((metric) => (
        <div className="metric-tile" key={metric.label}>
          <span>{metric.label}</span>
          <strong className="metric-value">
            <CountUpValue
              value={metric.value}
              suffix={metric.suffix}
              decimals={metric.decimals}
              fallback={summary ? 'n/a' : '0'}
            />
          </strong>
          <small>{metric.sub}</small>
        </div>
      ))}
    </section>
  );
}
