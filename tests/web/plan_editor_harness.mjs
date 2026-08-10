// A minimal DOM + fetch stub for exercising the plan editor's script in Node.
//
// The editor is one <script> in a self-contained page, so without this it would
// be the only untested surface in the plan layer. This is deliberately not a
// full DOM: it implements exactly what `plan.html` touches, so a change to the
// page that needs more of the DOM fails loudly here rather than silently in a
// browser.
//
// Usage:  node plan_editor_harness.mjs <plan.js> <view.json>
// Output: one JSON object of observations on stdout.

import fs from "node:fs";

const realTimeout = globalThis.setTimeout;
const tick = () => new Promise((r) => realTimeout(r, 0));

function makeEl(tag) {
  const el = {
    tagName: tag,
    children: [],
    attrs: {},
    style: {},
    _text: "",
    _listeners: {},
    value: "",
    checked: false,
    type: "",
    disabled: false,
    title: "",
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(name, fn) { (this._listeners[name] ||= []).push(fn); },
    removeEventListener() {},
    setPointerCapture() {},
    dispatch(name, ev) { for (const fn of this._listeners[name] || []) fn(ev || {}); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; this.children.length = 0; },
  };
  el.classList = {
    _s: new Set(),
    add(c) { this._s.add(c); },
    remove(c) { this._s.delete(c); },
    toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
    contains(c) { return this._s.has(c); },
  };
  return el;
}

const IDS = [
  "canvas", "title", "subtitle", "state", "add-node", "connect", "relayout",
  "save", "approve", "side", "findings", "toast", "canvas-wrap",
];
const byId = Object.fromEntries(IDS.map((id) => [id, makeEl("div")]));

globalThis.document = {
  getElementById: (id) => byId[id],
  createElement: makeEl,
  createElementNS: (_ns, tag) => makeEl(tag),
  createTextNode: (t) => ({ text: t, textContent: t }),
};
globalThis.window = { addEventListener() {} };
globalThis.location = { pathname: "/plan/zbb" };
globalThis.setTimeout = () => 0; // the page only uses it to hide the toast
globalThis.clearTimeout = () => {};

const [, , scriptPath, viewPath] = process.argv;
const VIEW = JSON.parse(fs.readFileSync(viewPath, "utf8"));
const requests = [];
let nextResponse = VIEW;

globalThis.fetch = async (url, init) => {
  const method = (init && init.method) || "GET";
  requests.push({ url, method, body: init && init.body ? JSON.parse(init.body) : null });
  return {
    ok: true,
    status: 200,
    json: async () => nextResponse,
    text: async () => "",
  };
};

const source = fs.readFileSync(scriptPath, "utf8");
const expose = `${source}
return { addNode, save, approve, relayout, position, select, onConnectClick,
         render, renderSide, renderFindings, plan: () => plan, dirty: () => dirty,
         connect: (v) => { connecting = v; } };`;
const api = new Function(expose)();

await tick();
await tick(); // let the page's own load() settle

const plan = api.plan();
const out = {
  title: byId.title.textContent,
  subtitle: byId.subtitle.textContent,
  state_after_load: byId.state.textContent,
  approve_disabled_after_load: byId.approve.disabled,
  nodes: plan.nodes.length,
  edges: plan.edges.length,
  // defs + one path and one hit-target per edge + one group per node
  svg_children: byId.canvas.children.length,
  svg_expected: 1 + plan.edges.length * 2 + plan.nodes.length,
  findings_rows: byId.findings.children.length,
};

// Position: the computed layout, then the stored one once a node is dragged.
out.layout_position = api.position(plan.nodes[0]);
plan.nodes[0].metadata = { x: 500, y: 12 };
out.dragged_position = api.position(plan.nodes[0]);

// Adding a node selects it, marks the document dirty and disables approval.
api.addNode();
const added = plan.nodes[plan.nodes.length - 1];
out.added = {
  id: added.id,
  artifact: added.artifact,
  reviewers: added.reviewers,
  has_prompt: Boolean(added.prompt && added.prompt.trim()),
};
out.state_after_edit = byId.state.textContent;
out.approve_disabled_after_edit = byId.approve.disabled;
out.side_heading = byId.side.children[0] ? byId.side.children[0].textContent : null;

// Connect mode: upstream click, then downstream click, makes one edge.
const edgesBefore = plan.edges.length;
api.connect("arm");
api.onConnectClick("strategy");
api.onConnectClick(added.id);
out.edge_added = plan.edges.length - edgesBefore;
out.new_edge = plan.edges[plan.edges.length - 1];

// A self-edge is refused.
api.connect("arm");
api.onConnectClick("strategy");
api.onConnectClick("strategy");
out.self_edge_refused = plan.edges.length - edgesBefore === 1;

// Relayout drops stored positions so the server layout applies again.
api.relayout();
out.position_after_relayout = api.position(plan.nodes[0]);

// Saving sends the whole document under a `plan` key.
await api.save();
const put = requests.find((r) => r.method === "PUT");
out.put = {
  url: put.url,
  wraps_plan: "plan" in put.body,
  carries_new_node: put.body.plan.nodes.some((n) => n.id === added.id),
  carries_new_edge: put.body.plan.edges.some((e) => e.downstream === added.id),
};

// Approving a clean document goes straight to approve.
nextResponse = { ...VIEW, approved: true };
requests.length = 0;
await api.approve();
out.approve_flow_clean = requests.map((r) => `${r.method} ${r.url}`);

// Approving a dirty document saves first. The edit has to go through a real
// mutation path — `addNode` — because that is what sets the dirty flag.
requests.length = 0;
api.addNode();
out.dirty_before_approve = api.dirty();
await api.approve();
out.approve_flow_dirty = requests.map((r) => `${r.method} ${r.url}`);

process.stdout.write(JSON.stringify(out, null, 1));
