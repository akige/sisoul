// ChartSimple · 简单 SVG 折线图占位组件 (Phase 3 升级为 chart.js)
interface DataPoint {
  x: number;
  y: number;
}

interface Props {
  data: DataPoint[];
  width?: number;
  height?: number;
  class?: string;
  label?: string;
}

export default function ChartSimple(props: Props) {
  const W = () => props.width ?? 300;
  const H = () => props.height ?? 100;

  const points = () => {
    const pts = props.data;
    if (pts.length < 2) return "";
    const xMin = Math.min(...pts.map((p) => p.x));
    const xMax = Math.max(...pts.map((p) => p.x));
    const yMin = Math.min(...pts.map((p) => p.y));
    const yMax = Math.max(...pts.map((p) => p.y));
    const xRange = xMax - xMin || 1;
    const yRange = yMax - yMin || 1;
    const pad = 8;
    return pts
      .map((p) => {
        const cx = pad + ((p.x - xMin) / xRange) * (W() - pad * 2);
        const cy = H() - pad - ((p.y - yMin) / yRange) * (H() - pad * 2);
        return `${cx},${cy}`;
      })
      .join(" ");
  };

  return (
    <div class={`${props.class ?? ""}`} data-testid="chart-simple">
      <svg
        width={W()}
        height={H()}
        viewBox={`0 0 ${W()} ${H()}`}
        class="w-full"
        aria-label={props.label ?? "chart"}
      >
        {props.data.length >= 2 ? (
          <polyline
            points={points()}
            fill="none"
            stroke="#7c9cff"
            stroke-width="1.5"
            stroke-linejoin="round"
            stroke-linecap="round"
          />
        ) : (
          <text
            x={W() / 2}
            y={H() / 2}
            text-anchor="middle"
            class="text-xs fill-sisoul-muted"
            style="font-size:11px;fill:#8a92a6"
          >
            暂无数据
          </text>
        )}
      </svg>
    </div>
  );
}
