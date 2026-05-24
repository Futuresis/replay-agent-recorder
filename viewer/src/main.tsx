import React, { useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import dagre from '@dagrejs/dagre';
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeMouseHandler,
  type Node,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './styles.css';

type GraphIR = {
  meta?: { title?: string };
  graph?: {
    nodes?: GraphNode[];
    edges?: GraphEdge[];
    edge_layers?: Record<string, unknown>;
    timeline?: { items?: TimelineItem[] };
    stats?: Record<string, unknown>;
  };
};

type NodeDisplay = {
  title?: string;
  summary?: string;
  kind_label?: string;
  stage?: string;
  tone?: string;
  tool_name?: string;
  ordinal?: number;
};

type GraphNode = {
  id: string;
  kind?: string;
  title?: string;
  summary?: string;
  run_id?: string;
  run_role?: string;
  record_uid?: string;
  branch_id?: string;
  path_id?: string;
  status?: string;
  provider?: string;
  api?: string;
  callsite?: Record<string, unknown>;
  display?: NodeDisplay;
  order?: { index?: number; run_index?: number };
  preview?: { input?: string; output?: string };
  degree?: { incoming?: number; outgoing?: number };
  record?: Record<string, unknown>;
};

type GraphEdge = {
  id?: string;
  source?: string;
  target?: string;
  from?: string;
  to?: string;
  edge_kind?: string;
  kind?: string;
  run_role?: string;
  cross_run?: boolean;
  summary?: string;
  metadata?: Record<string, unknown>;
};

type TimelineItem = {
  node_id?: string;
  title?: string;
  kind?: string;
  run_id?: string;
  path_id?: string;
  order?: { index?: number; run_index?: number };
};

type ReplayNodeData = {
  graphNode: GraphNode;
  label: string;
  subtitle: string;
  chip: string;
  meta: string;
};

type ReplayEdgeData = {
  graphEdge: GraphEdge;
  graphEdges: GraphEdge[];
  kinds: string[];
  source: string;
  target: string;
  layer: string;
};

type ReplayEdge = Edge<ReplayEdgeData>;

const NODE_WIDTH = 252;
const NODE_HEIGHT = 126;

const EDGE_KIND_ORDER = ['flow', 'data', 'control', 'fork'];
const EDGE_KIND_PRIORITY = ['fork', 'control', 'data', 'flow'];

const LAYER_LABELS: Record<string, string> = {
  default: '默认视图',
  reduced_provenance: '简化因果',
  full_provenance: '完整因果',
};

const EDGE_LEGEND = [
  {
    kind: 'flow',
    label: '流程',
    description: '同一 run/branch 内按调用顺序连接的主阅读路径',
  },
  {
    kind: 'data',
    label: '数据',
    description: '后续节点使用了前置节点的输出或数据',
  },
  {
    kind: 'control',
    label: '控制',
    description: '后续节点受前置节点的控制流或决策路径影响',
  },
  {
    kind: 'fork',
    label: '分叉',
    description: 'base run 到 fork run 的分叉边界',
  },
];

const KIND_LABELS: Record<string, string> = {
  llm: '模型',
  tool: '工具',
  node: '节点',
  flow: '流程',
  data: '数据',
  control: '控制',
  fork: '分叉',
  baseline: '基线',
  recorded: '已记录',
  replay: '重放',
  live: '实时',
  override: '复写',
  error: '错误',
};

function readGraphIR(): GraphIR {
  const el = document.getElementById('graph-ir-data');
  if (!el?.textContent) return {};
  try {
    return JSON.parse(el.textContent) as GraphIR;
  } catch (error) {
    console.error('Failed to parse graph IR JSON', error);
    return {};
  }
}

function clean(value: unknown): string {
  return String(value ?? '').trim();
}

function truncate(value: unknown, limit = 120): string {
  const text = clean(value).replace(/\s+/g, ' ');
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 3).trim()}...`;
}

function labelFor(value: unknown): string {
  const key = clean(value);
  return KIND_LABELS[key] || key || '未知';
}

function nodeTone(node: GraphNode): string {
  if (node.display?.tone) return node.display.tone;
  if (node.status === 'error') return 'error';
  if (node.kind === 'llm') return 'llm';
  if (node.kind === 'tool') return 'tool';
  return 'node';
}

function nodeColor(node: Node<ReplayNodeData>): string {
  const graphNode = node.data.graphNode;
  if (nodeTone(graphNode) === 'llm') return '#60a5fa';
  if (nodeTone(graphNode) === 'tool') return '#14b8a6';
  if (nodeTone(graphNode) === 'error') return '#fb7185';
  return '#cbd5e1';
}

function graphEdgesForLayer(ir: GraphIR, layer: string): GraphEdge[] {
  const graph = ir.graph || {};
  const edgeLayers = (graph.edge_layers || {}) as Record<string, unknown>;
  const layerEdges = edgeLayers[layer];
  if (Array.isArray(layerEdges)) return layerEdges as GraphEdge[];
  if (layer === 'default' && Array.isArray(edgeLayers.default)) return edgeLayers.default as GraphEdge[];
  return graph.edges || [];
}

function layerOptions(ir: GraphIR): string[] {
  const layers = (ir.graph?.edge_layers || {}) as Record<string, unknown>;
  const preferred = ['default', 'reduced_provenance', 'full_provenance'];
  const available = preferred.filter((key) => Array.isArray(layers[key]));
  return available.length ? available : ['default'];
}

function orderOf(node: GraphNode): number {
  return node.order?.index || node.order?.run_index || 0;
}

function layoutNodes(nodes: GraphNode[], edges: GraphEdge[]): Node<ReplayNodeData>[] {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: 'LR',
    align: 'UL',
    nodesep: 58,
    ranksep: 132,
    marginx: 48,
    marginy: 42,
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  for (const node of nodes) {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    const source = clean(edge.source || edge.from);
    const target = clean(edge.target || edge.to);
    if (nodeIds.has(source) && nodeIds.has(target)) graph.setEdge(source, target);
  }
  dagre.layout(graph);

  return [...nodes]
    .sort((a, b) => orderOf(a) - orderOf(b) || a.id.localeCompare(b.id))
    .map((node, fallbackIndex) => {
      const layout = graph.node(node.id) as { x?: number; y?: number } | undefined;
      const display = node.display || {};
      const label = truncate(display.title || node.title || node.id, 58);
      const subtitle = truncate(display.summary || node.summary || node.preview?.output || node.preview?.input || '', 112);
      const chip = display.kind_label || labelFor(node.kind || 'node');
      const meta = [
        display.ordinal ? `#${display.ordinal}` : node.order?.run_index ? `#${node.order.run_index}` : '',
        node.run_role === 'fork' ? 'fork' : '',
      ].filter(Boolean).join(' / ');
      return {
        id: node.id,
        type: 'replay',
        position: {
          x: (layout?.x ?? fallbackIndex * 320) - NODE_WIDTH / 2,
          y: (layout?.y ?? fallbackIndex * 160) - NODE_HEIGHT / 2,
        },
        data: { graphNode: node, label, subtitle, chip, meta },
        className: [
          'replay-node',
          nodeTone(node),
          display.stage || '',
          node.run_role === 'fork' ? 'fork' : '',
          node.status === 'error' ? 'error' : '',
        ].filter(Boolean).join(' '),
        style: { width: NODE_WIDTH, minHeight: NODE_HEIGHT },
      };
    });
}

function edgeKind(edge: GraphEdge): string {
  return clean(edge.edge_kind || edge.kind || 'edge');
}

function normalizeEdgeKind(kind: string): string {
  return EDGE_KIND_ORDER.includes(kind) ? kind : 'edge';
}

function primaryEdgeKind(kinds: string[]): string {
  for (const kind of EDGE_KIND_PRIORITY) {
    if (kinds.includes(kind)) return kind;
  }
  return kinds[0] || 'edge';
}

function orderedKinds(edges: GraphEdge[]): string[] {
  const kinds = [...new Set(edges.map((edge) => normalizeEdgeKind(edgeKind(edge))))];
  return kinds.sort((a, b) => {
    const left = EDGE_KIND_ORDER.indexOf(a);
    const right = EDGE_KIND_ORDER.indexOf(b);
    return (left === -1 ? EDGE_KIND_ORDER.length : left) - (right === -1 ? EDGE_KIND_ORDER.length : right);
  });
}

function edgeLabel(kinds: string[], edgeCount: number, layer: string): string {
  if (edgeCount > 1 || layer === 'full_provenance') return kinds.map(labelFor).join(' + ');
  const kind = primaryEdgeKind(kinds);
  return kind === 'fork' ? labelFor(kind) : '';
}

function edgeHandleId(kind: string, side: 'source' | 'target'): string {
  return `${side}-${normalizeEdgeKind(kind)}`;
}

function toFlowEdges(edges: GraphEdge[], layer: string): ReplayEdge[] {
  const grouped = new Map<string, { source: string; target: string; edges: GraphEdge[]; firstIndex: number }>();
  for (const edge of edges) {
    const source = clean(edge.source || edge.from);
    const target = clean(edge.target || edge.to);
    if (!source || !target) continue;
    const key = `${source}->${target}`;
    const group = grouped.get(key);
    if (group) {
      group.edges.push(edge);
    } else {
      grouped.set(key, { source, target, edges: [edge], firstIndex: grouped.size });
    }
  }

  return [...grouped.values()]
    .sort((a, b) => a.firstIndex - b.firstIndex)
    .map((group) => {
      const kinds = orderedKinds(group.edges);
      const kind = primaryEdgeKind(kinds);
      const edge = group.edges[0];
      const label = edgeLabel(kinds, group.edges.length, layer);
      const id = group.edges.length === 1 && edge.id
        ? edge.id
        : `edge:${group.source}->${group.target}:${kinds.join('+')}:${group.firstIndex}`;
      return {
        id,
        source: group.source,
        target: group.target,
        sourceHandle: edgeHandleId(kind, 'source'),
        targetHandle: edgeHandleId(kind, 'target'),
        type: kind === 'flow' ? 'step' : 'smoothstep',
        label,
        labelShowBg: true,
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 6,
        labelBgStyle: { fill: '#ffffff', fillOpacity: 0.95 },
        labelStyle: { fill: '#475569', fontSize: 11, fontWeight: 800 },
        className: [
          'replay-edge',
          kind,
          group.edges.length > 1 ? 'merged' : '',
          layer === 'full_provenance' ? 'audit-layer' : '',
          group.edges.some((item) => item.cross_run) ? 'cross-run' : '',
          group.edges.some((item) => item.run_role === 'fork') ? 'fork-run' : '',
        ].filter(Boolean).join(' '),
        animated: kind === 'fork',
        data: {
          graphEdge: edge,
          graphEdges: group.edges,
          kinds,
          source: group.source,
          target: group.target,
          layer,
        },
      } satisfies ReplayEdge;
    });
}

function NodeCard({ data }: { data: ReplayNodeData }) {
  const node = data.graphNode;
  const handleKinds = [...EDGE_KIND_ORDER, 'edge'];
  return (
    <article className="node-card" title={[data.label, data.subtitle].filter(Boolean).join('\n\n')}>
      {handleKinds.map((kind) => (
        <Handle
          className={`node-handle node-handle--${kind}`}
          id={edgeHandleId(kind, 'target')}
          key={`target-${kind}`}
          type="target"
          position={Position.Left}
        />
      ))}
      <div className="node-card__top">
        <span className={`node-pill tone-${nodeTone(node)}`}>{data.chip}</span>
      </div>
      <strong className="node-card__title">{data.label}</strong>
      <p className="node-card__summary">{data.subtitle || node.id}</p>
      <small className="node-card__meta">{data.meta}</small>
      {handleKinds.map((kind) => (
        <Handle
          className={`node-handle node-handle--${kind}`}
          id={edgeHandleId(kind, 'source')}
          key={`source-${kind}`}
          type="source"
          position={Position.Right}
        />
      ))}
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

function EdgeLegend({ layer }: { layer: string }) {
  return (
    <section className="edge-legend" aria-label="边图例">
      <div className="edge-legend__title">边图例</div>
      <div className="edge-legend__items">
        {EDGE_LEGEND.map((item) => (
          <div className="edge-legend__item" key={item.kind} title={item.description}>
            <span className={`edge-swatch ${item.kind}`} aria-hidden="true" />
            <span>{item.label}</span>
          </div>
        ))}
      </div>
      {layer === 'full_provenance' ? <p>完整因果层已降低边透明度以减少视觉噪音。</p> : null}
    </section>
  );
}

function EdgeInspector({ edge }: { edge: ReplayEdgeData }) {
  const [tab, setTab] = useState<'overview' | 'raw'>('overview');
  const first = edge.graphEdge;
  return (
    <aside className="inspector">
      <h2>{edge.kinds.map(labelFor).join(' + ')}边</h2>
      <div className="badge-row">
        {edge.kinds.map((kind) => (
          <span className={`badge edge-kind-${kind}`} key={kind}>{labelFor(kind)}</span>
        ))}
        {edge.graphEdges.length > 1 ? <span className="badge">{edge.graphEdges.length} 条已合并</span> : null}
        {edge.layer === 'full_provenance' ? <span className="badge">完整因果</span> : null}
      </div>
      <div className="tabs tabs--two" role="tablist" aria-label="边详情">
        {[
          ['overview', '概览'],
          ['raw', '原始边'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? 'is-active' : ''}
            onClick={() => setTab(key as typeof tab)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' ? (
        <>
          <Section title="端点">
            <dl className="meta-grid">
              <dt>Source</dt>
              <dd>{edge.source}</dd>
              <dt>Target</dt>
              <dd>{edge.target}</dd>
              <dt>类型</dt>
              <dd>{edge.kinds.map(labelFor).join(' + ')}</dd>
              <dt>数量</dt>
              <dd>{edge.graphEdges.length}</dd>
            </dl>
          </Section>
          <Section title="摘要">
            <p className="detail-copy">{first.summary || '无摘要'}</p>
          </Section>
          <Section title="Metadata">
            <JsonBlock value={first.metadata || {}} />
          </Section>
        </>
      ) : null}
      {tab === 'raw' ? (
        <Section title="合并前边列表">
          <JsonBlock
            value={edge.graphEdges.map((item) => ({
              id: item.id,
              kind: edgeKind(item),
              source: item.source || item.from,
              target: item.target || item.to,
              cross_run: item.cross_run,
              run_role: item.run_role,
              summary: item.summary,
              metadata: item.metadata,
            }))}
          />
        </Section>
      ) : null}
    </aside>
  );
}

function Inspector({ selectedNode, selectedEdge }: { selectedNode: GraphNode | null; selectedEdge: ReplayEdgeData | null }) {
  const [tab, setTab] = useState<'overview' | 'io' | 'causal' | 'raw'>('overview');
  if (selectedEdge) return <EdgeInspector edge={selectedEdge} />;
  if (!selectedNode) {
    return (
      <aside className="inspector">
        <h2>详情</h2>
        <p className="muted">选择一个节点查看输入、输出、因果关系和原始记录；选择一条边查看边类型、端点和 metadata。</p>
      </aside>
    );
  }
  const selected = selectedNode;
  const display = selected.display || {};
  const record = selected.record || {};
  const input = (record.input as unknown) || {};
  const output = (record.output as unknown) || {};
  const error = record.error;
  return (
    <aside className="inspector">
      <h2>{display.title || selected.title || selected.id}</h2>
      <div className="badge-row">
        <span className="badge">{display.kind_label || labelFor(selected.kind)}</span>
        <span className="badge">{selected.run_role === 'fork' ? '分叉运行' : '基线运行'}</span>
        <span className={`badge ${selected.status || ''}`}>{labelFor(selected.status || 'recorded')}</span>
      </div>
      <div className="tabs" role="tablist" aria-label="节点详情">
        {[
          ['overview', '概览'],
          ['io', '输入/输出'],
          ['causal', '因果'],
          ['raw', '技术信息'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? 'is-active' : ''}
            onClick={() => setTab(key as typeof tab)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' ? (
        <>
          <Section title="摘要">
            <p className="detail-copy">{display.summary || selected.summary || '无摘要'}</p>
          </Section>
          <dl className="meta-grid">
            <dt>节点</dt>
            <dd>{selected.id}</dd>
            <dt>Run</dt>
            <dd>{selected.run_id || ''}</dd>
            <dt>Record</dt>
            <dd>{selected.record_uid || ''}</dd>
            <dt>路径</dt>
            <dd>{selected.path_id || selected.branch_id || ''}</dd>
            <dt>耗时</dt>
            <dd>{selected.record?.metadata && typeof selected.record.metadata === 'object' ? clean((selected.record.metadata as Record<string, unknown>).latency_ms) : ''}</dd>
          </dl>
        </>
      ) : null}
      {tab === 'io' ? (
        <>
          <Section title="输入预览">
            <p className="detail-copy">{selected.preview?.input || '无输入预览'}</p>
          </Section>
          <Section title="输出预览">
            <p className="detail-copy">{selected.preview?.output || (error ? '执行失败' : '无输出预览')}</p>
          </Section>
          {error ? <Section title="错误"><JsonBlock value={error} /></Section> : null}
        </>
      ) : null}
      {tab === 'causal' ? (
        <>
          <dl className="meta-grid">
            <dt>入边</dt>
            <dd>{selected.degree?.incoming || 0}</dd>
            <dt>出边</dt>
            <dd>{selected.degree?.outgoing || 0}</dd>
            <dt>阶段</dt>
            <dd>{display.stage || ''}</dd>
          </dl>
          <Section title="Provenance">
            <JsonBlock value={(record.metadata as Record<string, unknown> | undefined)?.provenance || {}} />
          </Section>
        </>
      ) : null}
      {tab === 'raw' ? (
        <>
          <Section title="调用位置">
            <JsonBlock value={{ callsite: selected.callsite, provider: selected.provider, api: selected.api, semantic: selected.record?.metadata && typeof selected.record.metadata === 'object' ? (selected.record.metadata as Record<string, unknown>).semantic : undefined }} />
          </Section>
          <Section title="原始输入">
            <JsonBlock value={input} />
          </Section>
          <Section title="原始输出">
            <JsonBlock value={output} />
          </Section>
        </>
      ) : null}
    </aside>
  );
}

function Timeline({
  items,
  nodesById,
  selectedId,
  onSelect,
}: {
  items: TimelineItem[];
  nodesById: Map<string, GraphNode>;
  selectedId: string;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <section className="timeline">
      <div className="timeline__header">
        <h2>执行时间线</h2>
        <span>{items.length} 次调用</span>
      </div>
      <div className="timeline__list">
        {items.map((item) => {
          const nodeId = item.node_id || '';
          const node = nodesById.get(nodeId);
          const title = node?.display?.title || item.title || nodeId;
          return (
            <button
              key={`${nodeId}:${item.order?.index || ''}`}
              className={`timeline-item ${nodeId === selectedId ? 'is-active' : ''}`}
              type="button"
              onClick={() => onSelect(nodeId)}
            >
              <span>{item.order?.run_index || item.order?.index || '?'}. {title}</span>
              <small>{[node?.display?.kind_label || labelFor(item.kind), item.run_id, item.path_id].filter(Boolean).join(' / ')}</small>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function ReplayGraph({ ir }: { ir: GraphIR }) {
  const reactFlow = useReactFlow<ReplayNodeData>();
  const [layer, setLayer] = useState(() => layerOptions(ir)[0]);
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const [selectedEdgeId, setSelectedEdgeId] = useState('');
  const graphNodes = ir.graph?.nodes || [];
  const graphEdges = graphEdgesForLayer(ir, layer);
  const nodeById = useMemo(() => new Map(graphNodes.map((node) => [node.id, node])), [graphNodes]);
  const flowNodes = useMemo(() => layoutNodes(graphNodes, graphEdges), [graphNodes, graphEdges]);
  const flowEdges = useMemo(() => toFlowEdges(graphEdges, layer), [graphEdges, layer]);
  const displayedEdges = useMemo(
    () => flowEdges.map((edge) => ({ ...edge, selected: edge.id === selectedEdgeId })),
    [flowEdges, selectedEdgeId],
  );
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) || null : null;
  const selectedEdge = selectedEdgeId ? flowEdges.find((edge) => edge.id === selectedEdgeId)?.data || null : null;
  const layers = layerOptions(ir);
  const stats = ir.graph?.stats || {};
  const title = ir.meta?.title || 'Replay Graph';

  const onNodeClick: NodeMouseHandler<ReplayNodeData> = (_event, node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId('');
  };

  const onEdgeClick: EdgeMouseHandler<ReplayEdgeData> = (_event, edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId('');
  };

  function focusNode(nodeId: string) {
    if (!nodeId) return;
    setSelectedNodeId(nodeId);
    setSelectedEdgeId('');
    window.requestAnimationFrame(() => {
      const node = reactFlow.getNode(nodeId);
      if (!node) return;
      reactFlow.setCenter(node.position.x + NODE_WIDTH / 2, node.position.y + NODE_HEIGHT / 2, {
        zoom: 1.05,
        duration: 300,
      });
    });
  }

  return (
    <div className="xy-app">
      <header className="topbar">
        <div>
          <h1>{title}</h1>
          <p>
            {graphNodes.length} 个节点 / 当前显示 {flowEdges.length} 条显示边 / 原始 {graphEdges.length} 条边 / 完整因果 {stats.edge_count || 0} 条边
          </p>
        </div>
        <div className="toolbar">
          <label>
            边层
            <select
              value={layer}
              onChange={(event) => {
                setLayer(event.target.value);
                setSelectedNodeId('');
                setSelectedEdgeId('');
              }}
            >
              {layers.map((name) => (
                <option value={name} key={name}>
                  {LAYER_LABELS[name] || name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => reactFlow.fitView({ padding: 0.16, duration: 300 })}>
            适配视图
          </button>
        </div>
      </header>
      <main className="workspace">
        <div className="flow-shell">
          <EdgeLegend layer={layer} />
          <ReactFlow
            key={`${layer}:${flowNodes.length}:${flowEdges.length}`}
            nodes={flowNodes}
            edges={displayedEdges}
            nodeTypes={{ replay: NodeCard }}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            onPaneClick={() => {
              setSelectedNodeId('');
              setSelectedEdgeId('');
            }}
            fitView
            fitViewOptions={{ padding: 0.16 }}
            minZoom={0.18}
            maxZoom={1.8}
            nodesDraggable={false}
            nodesConnectable={false}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="rgba(100, 116, 139, 0.22)" gap={24} />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor={nodeColor}
              nodeStrokeWidth={3}
              pannable
              zoomable
              maskColor="rgba(148, 163, 184, 0.28)"
            />
          </ReactFlow>
        </div>
        <Timeline
          items={ir.graph?.timeline?.items || []}
          nodesById={nodeById}
          selectedId={selectedNodeId}
          onSelect={focusNode}
        />
        <Inspector selectedNode={selectedNode} selectedEdge={selectedEdge} />
      </main>
    </div>
  );
}

function App() {
  const ir = useMemo(() => readGraphIR(), []);
  return (
    <ReactFlowProvider>
      <ReplayGraph ir={ir} />
    </ReactFlowProvider>
  );
}

createRoot(document.getElementById('root') as HTMLElement).render(<App />);
