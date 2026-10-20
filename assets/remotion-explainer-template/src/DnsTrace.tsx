import React from "react";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import schedule from "./schedule.json";
import { CHAINS, Chain, Node, Step } from "./steps";

const FONT = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif';
const MONO = '"SF Mono", Menlo, Consolas, monospace';
const C = {
  bg: "#F7F8FA",
  ink: "#1F2937",
  sub: "#6B7280",
  line: "#94A3B8",
  query: "#2563EB",
  referral: "#EA580C",
  answer: "#16A34A",
  priming: "#7C3AED",
  final: "#16A34A",
};

// 布局（1920×1080）
const CLIENT = { x: 120, y: 500 };
const BOX = { x: 320, y: 190, w: 520, h: 714 };
const QCARD = { x: 400, y: 250, w: 360, h: 74 };
const NODE = { x: 1600, w: 280, h: 80, top: 120, gap: 112 };
const PILL_X = 875; // 报文标签左对齐在 BIND9 框右侧
const PILL_MAX = NODE.x - PILL_X - 20;

// 时间轴由 video_schedule.py 按旁白时长生成
type Timed = { arrive: number; leave: number; scene: number; step: number };
const SCHEDULE = schedule.SCHEDULE as Timed[];
const SCENES = schedule.SCENES as { start: number; end: number }[];
export const TOTAL_SEC: number = schedule.TOTAL;

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const ease = Easing.bezier(0.16, 1, 0.3, 1);

type Pt = { x: number; y: number };
const nodeY = (i: number) => NODE.top + i * NODE.gap;
const bindRight: Pt = { x: QCARD.x + QCARD.w, y: QCARD.y + QCARD.h / 2 };
const bindLeft: Pt = { x: QCARD.x, y: QCARD.y + QCARD.h / 2 };
const clientPt: Pt = { x: CLIENT.x + 40, y: CLIENT.y };

// 查询线和应答线沿法向错开，避免重叠
const offset = (a: Pt, b: Pt, d: number): [Pt, Pt] => {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  const nx = (-dy / len) * d;
  const ny = (dx / len) * d;
  return [
    { x: a.x + nx, y: a.y + ny },
    { x: b.x + nx, y: b.y + ny },
  ];
};

const endpoints = (s: Step, nodes: Node[]): { from: Pt; to: Pt } => {
  if (s.kind === "auth") {
    const i = nodes.findIndex((n) => n.id === s.node);
    return { from: bindRight, to: { x: NODE.x, y: nodeY(i) + NODE.h / 2 } };
  }
  return { from: clientPt, to: bindLeft };
};

const respColor = (s: Step) => C[s.rType];
// 带箭头、可逐步绘制的线段；progress 0→1
const Arrow: React.FC<{ a: Pt; b: Pt; color: string; progress: number; opacity: number }> = ({ a, b, color, progress, opacity }) => {
  if (progress <= 0) return null;
  const x = a.x + (b.x - a.x) * progress;
  const y = a.y + (b.y - a.y) * progress;
  const ang = Math.atan2(b.y - a.y, b.x - a.x);
  const h = 16;
  const p1 = `${x - h * Math.cos(ang - 0.4)},${y - h * Math.sin(ang - 0.4)}`;
  const p2 = `${x - h * Math.cos(ang + 0.4)},${y - h * Math.sin(ang + 0.4)}`;
  return (
    <g opacity={opacity}>
      <line x1={a.x} y1={a.y} x2={x} y2={y} stroke={color} strokeWidth={3} strokeLinecap="round" />
      <polygon points={`${x},${y} ${p1} ${p2}`} fill={color} />
    </g>
  );
};

// 线上的报文标签：白底圆角胶囊。anchor 决定它挂在锚点上方、下方还是居中
const Pill: React.FC<{ at: Pt; text: string; color: string; tag: string; opacity: number; anchor: "above" | "below" | "center" }> = ({
  at,
  text,
  color,
  tag,
  opacity,
  anchor,
}) => (
  <div
    style={{
      position: "absolute",
      left: at.x,
      top: at.y,
      translate: anchor === "center" ? "-50% -50%" : anchor === "above" ? "0 -100%" : "0 0",
      maxWidth: anchor === "center" ? 900 : PILL_MAX,
      opacity,
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "8px 16px",
      background: "white",
      border: `2.5px solid ${color}`,
      borderRadius: 12,
      boxShadow: "0 4px 14px rgba(15,23,42,0.10)",
    }}
  >
    <span style={{ flexShrink: 0, background: color, color: "white", fontFamily: FONT, fontSize: 20, fontWeight: 700, padding: "2px 10px", borderRadius: 6 }}>
      {tag}
    </span>
    <span style={{ fontFamily: MONO, fontSize: 22, color: C.ink, fontWeight: 600, lineHeight: 1.3 }}>{text}</span>
  </div>
);

const TAG: Record<Step["rType"], string> = {
  priming: "预热应答",
  referral: "转介",
  answer: "权威应答",
  final: "最终应答",
};

const along = (a: Pt, b: Pt, t: number): Pt => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });

// 单步交互：查询线 → 节点闪亮 → 应答线回程
const Exchange: React.FC<{ s: Step; nodes: Node[]; lf: number; S: number }> = ({ s, nodes, lf, S }) => {
  const { from, to } = endpoints(s, nodes);
  const past = lf >= S;
  const dim = past ? 0.35 : 1;
  const qProg = interpolate(lf, [0, 28], [0, 1], { ...clamp, easing: ease });
  const rProg = interpolate(lf, [48, 78], [0, 1], { ...clamp, easing: ease });
  const [qa, qb] = offset(from, to, -7);
  const [ra, rb] = offset(to, from, -7);
  if (s.kind === "reply") {
    return <Arrow a={to} b={from} color={past ? C.line : C.final} progress={qProg} opacity={dim} />;
  }
  return (
    <g>
      <Arrow a={qa} b={qb} color={past ? C.line : C.query} progress={qProg} opacity={dim} />
      {s.r && <Arrow a={ra} b={rb} color={past ? C.line : respColor(s)} progress={rProg} opacity={dim} />}
    </g>
  );
};

const Labels: React.FC<{ s: Step; nodes: Node[]; lf: number; S: number }> = ({ s, nodes, lf, S }) => {
  if (lf < 0 || lf >= S + 8) return null;
  const out = interpolate(lf, [S - 4, S + 8], [1, 0], clamp);
  const qOp = Math.min(interpolate(lf, [8, 20], [0, 1], clamp), out);
  const rOp = Math.min(interpolate(lf, [58, 72], [0, 1], clamp), out);
  if (s.kind !== "auth") {
    // 客户端这一段线太短，标签放在 BIND9 框上方
    const p = { x: 580, y: 140 };
    return s.kind === "client" ? (
      <Pill anchor="center" at={p} text={s.q} color={C.query} tag="查询" opacity={qOp} />
    ) : (
      <Pill anchor="center" at={p} text={s.r} color={C.final} tag={TAG.final} opacity={qOp} />
    );
  }
  const { from, to } = endpoints(s, nodes);
  const y = along(from, to, 0.55).y;
  return (
    <>
      <Pill anchor="above" at={{ x: PILL_X, y: y - 10 }} text={s.q} color={C.query} tag="查询" opacity={qOp} />
      <Pill anchor="below" at={{ x: PILL_X, y: y + 10 }} text={s.r} color={respColor(s)} tag={TAG[s.rType]} opacity={rOp} />
    </>
  );
};

const Cylinder: React.FC<{ x: number; y: number; label: string; glow: number }> = ({ x, y, label, glow }) => {
  const w = 120;
  const h = 86;
  const stroke = glow > 0 ? "#CA8A04" : C.line;
  return (
    <g>
      <path d={`M${x - w / 2},${y} v${h} a${w / 2},14 0 0 0 ${w},0 v${-h}`} fill={glow > 0 ? `rgba(234,179,8,${0.2 * glow})` : "white"} stroke={stroke} strokeWidth={2.5} />
      <ellipse cx={x} cy={y} rx={w / 2} ry={14} fill="white" stroke={stroke} strokeWidth={2.5} />
      <text x={x} y={y + 56} textAnchor="middle" fontFamily={MONO} fontSize={22} fill={C.ink} fontWeight={600}>
        {label}
      </text>
    </g>
  );
};
const Scene: React.FC<{ chain: Chain; k: number }> = ({ chain, k }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const steps = chain.steps;
  const timed = SCHEDULE.filter((t) => t.scene === k);
  const last = steps.length - 1;
  const lfOf = (j: number) => frame - Math.round(timed[j].arrive * fps);
  // 最后一步一直保持高亮到场景结束
  const durOf = (j: number) => (j === last ? 1e9 : Math.round((timed[j].leave - timed[j].arrive) * fps));
  const cur = Math.max(0, timed.reduce((c, t, j) => (lfOf(j) >= 0 ? j : c), 0));
  const { start, end } = SCENES[k];
  const fade = Math.min(
    interpolate(frame, [start * fps, start * fps + 15], [0, 1], clamp),
    interpolate(frame, [end * fps - 12, end * fps], [1, 0], clamp),
  );
  const visit = (id: string) => steps.findIndex((s) => s.node === id);
  const s = steps[cur];
  const rows = [
    ...chain.carry.map((text) => ({ text, j: -1, db: "cache" as const })),
    ...steps.map((st, j) => ({ text: st.cache, j, db: st.db ?? "cache" })).filter((r) => r.text),
  ] as { text: string; j: number; db: "cache" | "adb" }[];
  const hitOn = (text: string) =>
    steps.some((h, j) => h.hit && text.startsWith(h.hit) && interpolate(lfOf(j), [0, 6, 44, 54], [0, 1, 1, 0], clamp) > 0);
  const glowOf = (db: "cache" | "adb") =>
    Math.max(
      0,
      ...steps.map((st, j) => {
        const lf = lfOf(j);
        const w = st.cache && (st.db ?? "cache") === db ? interpolate(lf, [76, 84, 104, 116], [0, 1, 1, 0], clamp) : 0;
        const h = st.hit && db === "cache" ? interpolate(lf, [0, 8, 40, 50], [0, 1, 1, 0], clamp) : 0;
        return Math.max(w, h);
      }),
    );

  return (
    <AbsoluteFill style={{ background: C.bg, fontFamily: FONT, opacity: fade }}>
      <div style={{ position: "absolute", left: 60, top: 36, fontSize: 40, fontWeight: 800, color: C.ink }}>
        DNS 递归解析 {k + 1}/{CHAINS.length} · <span style={{ fontFamily: MONO }}>{chain.title}</span>
      </div>
      <div style={{ position: "absolute", left: 900, top: 50, display: "flex", gap: 22, fontSize: 22, color: C.sub }}>
        {([["查询", C.query], ["转介", C.referral], ["权威应答", C.answer], ["根预热", C.priming]] as const).map(([name, c]) => (
          <span key={name} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 28, height: 5, background: c, borderRadius: 3 }} />
            {name}
          </span>
        ))}
      </div>

      <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
        {/* BIND9 递归服务器外框 */}
        <rect x={BOX.x} y={BOX.y} width={BOX.w} height={BOX.h} rx={18} fill="white" stroke="#334155" strokeWidth={2.5} />
        <text x={BOX.x + 24} y={BOX.y + 40} fontFamily={FONT} fontSize={26} fontWeight={700} fill={C.ink}>
          BIND9 递归服务器
        </text>
        {[450, 580, 710].map((x) => (
          <line key={x} x1={580} y1={QCARD.y + QCARD.h} x2={x} y2={350} stroke={C.line} strokeWidth={2} strokeDasharray="6 6" />
        ))}
        <Cylinder x={450} y={350} label="zonedb" glow={0} />
        <Cylinder x={580} y={350} label="cachedb" glow={glowOf("cache")} />
        <Cylinder x={710} y={350} label="adb" glow={glowOf("adb")} />

        {/* client 小人 */}
        <g stroke={C.ink} strokeWidth={3} fill="none" strokeLinecap="round">
          <circle cx={CLIENT.x} cy={CLIENT.y - 58} r={20} fill="white" />
          <line x1={CLIENT.x} y1={CLIENT.y - 38} x2={CLIENT.x} y2={CLIENT.y + 20} />
          <line x1={CLIENT.x - 30} y1={CLIENT.y - 16} x2={CLIENT.x + 30} y2={CLIENT.y - 16} />
          <line x1={CLIENT.x} y1={CLIENT.y + 20} x2={CLIENT.x - 22} y2={CLIENT.y + 60} />
          <line x1={CLIENT.x} y1={CLIENT.y + 20} x2={CLIENT.x + 22} y2={CLIENT.y + 60} />
        </g>
        <text x={CLIENT.x} y={CLIENT.y + 96} textAnchor="middle" fontFamily={MONO} fontSize={24} fill={C.ink}>
          client
        </text>

        {/* 交互线：先画已完成的（灰），再画当前的 */}
        {steps.map((st, j) => (lfOf(j) >= 0 && j !== cur ? <Exchange key={j} s={st} nodes={chain.nodes} lf={lfOf(j)} S={durOf(j)} /> : null))}
        {lfOf(cur) >= 0 && <Exchange s={s} nodes={chain.nodes} lf={lfOf(cur)} S={durOf(cur)} />}
        {/* 右侧竖排权威节点 */}
        {chain.nodes.map((n, i) => {
          const v = visit(n.id);
          const lf = lfOf(v);
          const st = steps[v];
          const active = lf >= 22 && lf < durOf(v);
          const done = lf >= durOf(v);
          const col = active ? (lf >= 70 ? respColor(st) : C.query) : done ? respColor(st) : "#CBD5E1";
          const y = nodeY(i);
          const pop = active ? interpolate(lf, [22, 32, 44], [1, 1.06, 1], clamp) : 1;
          return (
            <g key={n.id} style={{ scale: pop, transformOrigin: `${NODE.x + NODE.w / 2}px ${y + NODE.h / 2}px` }}>
              <rect x={NODE.x} y={y} width={NODE.w} height={NODE.h} rx={14} fill={active ? "#EFF6FF" : "white"} stroke={col} strokeWidth={active ? 4 : 2.5} />
              <text x={NODE.x + NODE.w / 2} y={y + 38} textAnchor="middle" fontFamily={MONO} fontSize={n.zone.length > 12 ? 24 : 30} fontWeight={700} fill={C.ink}>
                {n.zone}
              </text>
              <text x={NODE.x + NODE.w / 2} y={y + 66} textAnchor="middle" fontFamily={MONO} fontSize={17} fill={C.sub}>
                {n.servers}
              </text>
              {(active || done) && (
                <g>
                  <circle cx={NODE.x + NODE.w} cy={y} r={17} fill={col} />
                  <text x={NODE.x + NODE.w} y={y + 7} textAnchor="middle" fontFamily={FONT} fontSize={20} fontWeight={700} fill="white">
                    {v + 1}
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>

      {/* 当前正在解析的名字；CNAME 后换名重启 */}
      <div
        style={{
          position: "absolute",
          left: QCARD.x,
          top: QCARD.y,
          width: QCARD.w,
          height: QCARD.h,
          borderRadius: 14,
          border: `3px solid ${s.kind === "reply" ? C.final : C.query}`,
          background: s.kind === "reply" ? "#F0FDF4" : "#EFF6FF",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: MONO,
          fontSize: s.qcard.length > 18 ? 22 : 26,
          fontWeight: 700,
          color: C.ink,
        }}
      >
        {s.qcard}
        {s.qtag && (
          <span style={{ position: "absolute", top: -16, right: -14, background: C.answer, color: "white", fontFamily: FONT, fontSize: 17, padding: "2px 8px", borderRadius: 6 }}>
            {s.qtag}
          </span>
        )}
      </div>

      {/* 缓存内容：每次应答后写入，命中时高亮 */}
      <div style={{ position: "absolute", left: BOX.x + 30, top: 476, width: BOX.w - 60 }}>
        <div style={{ fontSize: 22, color: C.sub, marginBottom: 8 }}>已缓存（cachedb / adb）</div>
        {rows.map(({ text, j, db }) => {
          const op = j < 0 ? 1 : interpolate(lfOf(j), [78, 92], [0, 1], clamp);
          const hit = hitOn(text);
          return (
            <div
              key={text}
              style={{
                opacity: op,
                translate: `${interpolate(op, [0, 1], [-16, 0])}px 0px`,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                height: 34,
                marginBottom: 3,
                padding: "0 14px",
                borderRadius: 8,
                background: hit ? "#FEF08A" : db === "adb" ? "#ECFDF5" : "#F1F5F9",
                border: hit ? "2px solid #CA8A04" : "2px solid transparent",
                fontFamily: MONO,
                fontSize: 19,
                color: C.ink,
              }}
            >
              <span>{text}</span>
              <span style={{ fontFamily: FONT, fontSize: 16, color: hit ? "#854D0E" : C.sub }}>
                {hit ? "命中缓存" : j < 0 ? "上一条留下" : `第 ${j + 1} 步写入${db === "adb" ? " adb" : ""}`}
              </span>
            </div>
          );
        })}
      </div>

      {steps.map((st, j) => (
        <Labels key={j} s={st} nodes={chain.nodes} lf={lfOf(j)} S={durOf(j)} />
      ))}

      {/* 底部字幕条 */}
      <div
        style={{
          position: "absolute",
          left: 60,
          right: 60,
          top: 922,
          height: 112,
          background: "white",
          borderRadius: 18,
          boxShadow: "0 6px 24px rgba(15,23,42,0.08)",
          display: "flex",
          alignItems: "center",
          gap: 26,
          padding: "0 32px",
        }}
      >
        <span style={{ flexShrink: 0, background: C.ink, color: "white", fontSize: 26, fontWeight: 700, padding: "8px 18px", borderRadius: 10 }}>
          {cur + 1} / {steps.length}
        </span>
        <span style={{ fontSize: 33, color: C.ink, lineHeight: 1.35, opacity: interpolate(lfOf(cur), [0, 12], [0, 1], clamp) }}>{s.note}</span>
      </div>
    </AbsoluteFill>
  );
};

// 整段视频：按时间轴切换场景，每次只画当前场景
export const DnsTrace: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const found = SCENES.findIndex((sc) => t < sc.end);
  const k = found < 0 ? SCENES.length - 1 : found;
  return <Scene key={k} chain={CHAINS[k]} k={k} />;
};