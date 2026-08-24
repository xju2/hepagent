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
    // The dock's height is a CSS custom property, so the stub has to be able to
    // hold one — a plain object would silently swallow setProperty.
    style: {
      _props: {},
      setProperty(k, v) { this._props[k] = v; },
      getPropertyValue(k) { return this._props[k] || ""; },
    },
    _text: "",
    _listeners: {},
    value: "",
    checked: false,
    type: "",
    disabled: false,
    title: "",
    // The canvas pane: a viewport big enough that nothing needs scrolling into
    // view, so a test that sees a scroll saw the page decide to scroll.
    clientWidth: 1400,
    clientHeight: 900,
    scrollLeft: 0,
    scrollTop: 0,
    // The run log measures itself to decide whether to stay pinned to the
    // bottom. A stub that is exactly scrolled to its end means "the reader is
    // at the bottom", which is the state a live run starts in.
    scrollHeight: 900,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) {
      const at = this.children.indexOf(c);
      if (at >= 0) this.children.splice(at, 1);
      return c;
    },
    get childElementCount() { return this.children.length; },
    get firstElementChild() { return this.children[0] || null; },
    addEventListener(name, fn) { (this._listeners[name] ||= []).push(fn); },
    removeEventListener(name, fn) {
      const fns = this._listeners[name] || [];
      const at = fns.indexOf(fn);
      if (at >= 0) fns.splice(at, 1);
    },
    setPointerCapture() {},
    releasePointerCapture() {},
    // The page maps pointer coordinates through this; the drawing sits at the
    // window origin here, so client coordinates are user units.
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
    dispatch(name, ev) { for (const fn of (this._listeners[name] || []).slice()) fn(ev || {}); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; this.children.length = 0; },
  };
  el.classList = {
    _s: new Set(),
    add(...cs) { for (const c of cs) this._s.add(c); },
    remove(...cs) { for (const c of cs) this._s.delete(c); },
    toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
    contains(c) { return this._s.has(c); },
  };
  return el;
}

const IDS = [
  "canvas", "title", "subtitle", "state", "add-node", "add-condition", "connect",
  // The predefined-node library: the button, its dialog, and the picker inside.
  "add-predefined", "library", "library-search", "library-list", "library-close",
  "relayout", "save", "approve", "side", "findings", "toast", "canvas-wrap",
  "show-prompt",
  // The run supervisor: progress log, and the dialog a running analysis asks
  // its questions in.
  "run", "run-state", "run-detail", "run-log", "cancel-run",
  // Run settings: what the next launch is configured with.
  "run-settings", "run-unattended", "run-model", "run-iterations", "run-turns",
  // The bottom dock: the run log is one tab, what the analysis has established
  // is the other.
  "dock", "dock-grip", "tab-progress", "tab-log", "progress", "log-filter",
  "ask", "ask-title", "ask-text", "ask-cmd", "ask-thought", "ask-input", "ask-actions",
];
const byId = Object.fromEntries(IDS.map((id) => [id, makeEl("div")]));

globalThis.document = {
  getElementById: (id) => byId[id],
  createElement: makeEl,
  createElementNS: (_ns, tag) => makeEl(tag),
  createTextNode: (t) => ({ text: t, textContent: t }),
};
// Panning listens on the window for the duration of one drag, so the stub has
// to be able to forget a listener as well as remember it.
const windowListeners = {};
globalThis.window = {
  addEventListener(name, fn) { (windowListeners[name] ||= []).push(fn); },
  removeEventListener(name, fn) {
    const fns = windowListeners[name] || [];
    const at = fns.indexOf(fn);
    if (at >= 0) fns.splice(at, 1);
  },
  dispatch(name, ev) { for (const fn of (windowListeners[name] || []).slice()) fn(ev || {}); },
};
globalThis.location = { pathname: "/plan/zbb" };
globalThis.setTimeout = () => 0; // the page only uses it to hide the toast
globalThis.clearTimeout = () => {};

const [, , scriptPath, viewPath] = process.argv;
const VIEW = JSON.parse(fs.readFileSync(viewPath, "utf8"));
const requests = [];
let nextResponse = VIEW;
// What `POST .../run` answers, when set. A run snapshot is not a plan view, and
// the per-node Run button saves before it launches — so the two calls it makes
// cannot share one canned response.
let runResponse = null;

// What `GET /api/plan/{name}/state` returns: the strategy has recorded its
// processes, and one plan node has written its artifact. This is the panel's
// whole input, so the observations below prove the page renders the server's
// answer rather than re-deriving one.
const STATE = {
  name: "zbb",
  analysis_type: "measurement",
  nodes: [
    { id: "strategy", label: "Strategy", kind: "work",
      artifact: "phase1_strategy/outputs/STRATEGY.md", produced: true, figures: 0 },
    { id: "selection", label: "Selection", kind: "work",
      artifact: "phase3_selection/outputs/SELECTION.md", produced: false, figures: 2 },
  ],
  processes: {
    recorded: true, node_id: "strategy", updated_at: "2026-08-19T00:00:00+00:00",
    count: 3, notes: "",
    path: "phase1_strategy/outputs/processes.json",
    datasets: [{ name: "GluGluToHToTauTau.root" }, { name: "DYJetsToLL.root" },
               { name: "W1JetsToLNu.root" }],
    sections: [
      { name: "signal", kind: "signal", processes: [
        { id: "ggH", label: "gg->H->tautau", role: "signal", category: "",
          importance: "unknown", rationale: "", estimation: "MC",
          datasets: [{ name: "GluGluToHToTauTau.root", path: "/data/ggH.root",
                       kind: "mc", source: "prompt", events: 0, metadata: {} }] }] },
      { name: "irreducible", kind: "background", processes: [
        { id: "dy", label: "Z/gamma* -> ll", role: "background",
          category: "irreducible", importance: "dominant",
          rationale: "same final state", estimation: "MC, normalised in a CR",
          datasets: [{ name: "DYJetsToLL.root", path: "/data/dy.root",
                       kind: "mc", source: "prompt", events: 0, metadata: {} }] }] },
      { name: "reducible", kind: "background", processes: [] },
      { name: "instrumental", kind: "background", processes: [
        { id: "wjets", label: "W+jets", role: "background",
          category: "instrumental", importance: "major",
          rationale: "jet faking tau_h", estimation: "data-driven, high-mT region",
          datasets: [{ name: "W1JetsToLNu.root", path: "/data/w1.root",
                       kind: "mc", source: "prompt", events: 0, metadata: {} }] }] },
      { name: "data", kind: "data", processes: [] },
    ],
  },
};

// What `GET /api/plan/{name}/predefined` returns: two pipelines' worth of
// nodes, one of which collides with a node the plan already has — which is the
// case that proves the page renames rather than duplicates.
const PREDEFINED = {
  nodes: [
    { key: "jfc-measurement:strategy", source: "jfc-measurement",
      source_description: "Seven-phase measurement", analysis_type: "measurement",
      summary: "Phase 1: Strategy",
      node: { id: "strategy", label: "Strategy", directory: "phase1_strategy",
              artifact: "STRATEGY.md", note_artifact: "", prompt: "Choose a technique.",
              kind: "work", role: "executor", context_paths: [],
              reviewers: ["physics"], arbiter: true, produces_note: false,
              gates: [], condition: null,
              contract: { node_types: ["commitment"], edge_types: ["commits_to"] },
              max_iterations: 3, max_turns: null, model: null, tools: null,
              skills: [], mcp_servers: [], metadata: {} } },
    { key: "jfc-search:limits", source: "jfc-search",
      source_description: "Search pipeline", analysis_type: "search",
      summary: "Set the limits",
      node: { id: "limits", label: "Limits", directory: "phase6_limits",
              artifact: "LIMITS.md", note_artifact: "", prompt: "Set CLs limits.",
              kind: "work", role: "executor", context_paths: [],
              reviewers: ["critical"], arbiter: false, produces_note: true,
              gates: [], condition: null,
              contract: { node_types: ["evidence"], edge_types: ["supports"] },
              max_iterations: 3, max_turns: null, model: null, tools: null,
              skills: [], mcp_servers: [], metadata: {} } },
  ],
};

// What `GET /api/plan/{name}/problem` returns: the question as it stands, and
// the one it superseded. The panel's history list is drawn from this alone.
const PROBLEM = {
  revisions: [
    { revision: 4, updated_at: "2026-08-20T00:00:00+00:00", current: true,
      problem: "Measure the Z->bb cross section." },
    { revision: 1, updated_at: "2026-08-01T00:00:00+00:00", current: false,
      problem: "Measure something with b jets." },
  ],
};

function layoutResponse(request) {
  // A grid nothing else could have produced, so a position matching it proves
  // the page applied the server's answer rather than the layout that arrived
  // with the view.
  const grid = {};
  request.plan.nodes.forEach((node, index) => {
    grid[node.id] = index === 0 ? [2, 1] : [0, index];
  });
  return { layout: grid };
}

globalThis.fetch = async (url, init) => {
  const method = (init && init.method) || "GET";
  const body = init && init.body ? JSON.parse(init.body) : null;
  requests.push({ url, method, body });
  const models = /\/api\/platforms\/([^/]+)\/models$/.exec(url);
  const payload = url.endsWith("/layout") ? layoutResponse(body)
    : models ? { platform: models[1], default: "default-model",
                 models: ["big-model", "small-model"] }
    : url.endsWith("/predefined") ? PREDEFINED
    : url.endsWith("/state") ? STATE
    : url.endsWith("/problem") ? PROBLEM
    : url.endsWith("/run") && method === "POST" && runResponse ? runResponse
    : nextResponse;
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => "",
  };
};

const source = fs.readFileSync(scriptPath, "utf8");
const expose = `${source}
return { addNode, addCondition, save, approve, relayout, position, select,
         onConnectClick, render, renderSide, renderFindings, overlaps,
         backBranches, applyRun, runNode, openLibrary, closeLibrary,
         entryNodes, promptPosition, PROMPT_ID,
         plan: () => plan,
         dirty: () => dirty, connect: (v) => { connecting = v; } };`;
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
  // defs + one path per prompt-to-entry edge + the prompt's own group
  // + one path and one hit-target per plan edge + one group per plan node
  svg_expected: 1 + api.entryNodes().length + 1 + plan.edges.length * 2 + plan.nodes.length,
  findings_rows: byId.findings.children.length,
};

// Position: the computed layout, then the stored one once a node is dragged.
out.layout_position = api.position(plan.nodes[0]);

// Dragging. The node is grabbed at (100,100) — 60px right and below its own
// corner — and the pointer is moved to (143,178); the node keeps the offset and
// lands on the 10px grid. The group must survive the whole drag: re-rendering
// mid-move is what used to destroy the element holding the pointer capture.
const groupOf = (id) => byId.canvas.children.find((c) => c.attrs["data-id"] === id);
const dragged = plan.nodes[0];
api.select("node", dragged.id);
const group = groupOf(dragged.id);
const edgeIndex = plan.edges.findIndex((e) => e.upstream === dragged.id);
group.dispatch("pointerdown", {
  clientX: 100, clientY: 100, pointerId: 1, button: 0,
  stopPropagation() {}, preventDefault() {},
});
group.dispatch("pointermove", { clientX: 143, clientY: 178, pointerId: 1 });
out.drag = {
  position: api.position(dragged),
  transform: group.attrs.transform,
  group_survived_the_move: groupOf(dragged.id) === group,
  // defs, the prompt's seed edges and the prompt group, then a path and a hit
  // target per plan edge: the line for edge i is at `edgesAt + 2i`.
  edge_path: edgeIndex < 0 ? null
    : byId.canvas.children[1 + api.entryNodes().length + 1 + 2 * edgeIndex].attrs.d,
  canvas_width: Number(byId.canvas.attrs.width),
};
group.dispatch("pointerup", { pointerId: 1 });
out.drag.position_after_release = api.position(dragged);
out.drag.rerendered_on_release = groupOf(dragged.id) !== group;
out.drag.dirty_after_release = api.dirty();

// The physics prompt: drawn as the head of the graph, and edited in the panel.
api.select("node", plan.nodes[1].id);
out.side_heading_while_selecting = byId.side.children[0].textContent;

const promptGroup = groupOf(api.PROMPT_ID);
out.prompt_node = {
  drawn: Boolean(promptGroup),
  position: api.promptPosition(),
  // Every node with nothing blocking it hangs off the prompt.
  seeds: api.entryNodes().map((n) => n.id),
  label: promptGroup ? promptGroup.children[1].textContent : null,
};

// Clicking the drawn prompt opens the same panel the toolbar button does.
promptGroup.dispatch("click", { stopPropagation() {} });
out.prompt_panel_from_canvas = byId.side.children[0].textContent;

byId["show-prompt"].dispatch("click");
await tick();   // the panel fetches its history the first time it opens
const promptFields = byId.side.children;
const promptBox = promptFields.find((c) => c.tagName === "textarea");
out.prompt_panel = {
  heading: promptFields[0].textContent,
  editable: Boolean(promptBox),
  body: promptBox ? promptBox.value : null,
  history: promptFields.filter((c) => c.className === "revision")
    .map((c) => c.children[1].textContent),
};

// Typing in it rewrites the plan's own question and marks the page unsaved.
promptBox.value = "Measure the Z->bb cross section at 91 GeV.";
promptBox.dispatch("input");
out.prompt_edit = { problem: plan.problem, dirty: api.dirty() };
plan.problem = "Measure the Z->bb cross section.";

// Dragging it moves the prompt, not a node: its position lives on the plan.
promptGroup.dispatch("pointerdown", {
  clientX: 60, clientY: 60, pointerId: 3, button: 0,
  stopPropagation() {}, preventDefault() {},
});
promptGroup.dispatch("pointermove", { clientX: 260, clientY: 160, pointerId: 3 });
promptGroup.dispatch("pointerup", { pointerId: 3 });
out.prompt_drag = {
  position: api.promptPosition(),
  metadata: { x: plan.metadata.prompt_x, y: plan.metadata.prompt_y },
};
delete plan.metadata.prompt_x; delete plan.metadata.prompt_y;
api.render();

plan.nodes[0].metadata = { x: 500, y: 12 };
out.dragged_position = api.position(plan.nodes[0]);
// Back to the computed layout, so the placement below has to find room among a
// full template rather than in the hole a moved node left behind.
plan.nodes[0].metadata = {};

// Adding a node selects it, marks the document dirty and disables approval.
api.addNode();
const added = plan.nodes[plan.nodes.length - 1];
out.added = {
  id: added.id,
  artifact: added.artifact,
  reviewers: added.reviewers,
  has_prompt: Boolean(added.prompt && added.prompt.trim()),
  position: api.position(added),
  // The point of placing it: it must not land under an existing node.
  overlaps_an_existing_node: plan.nodes
    .filter((n) => n.id !== added.id)
    .some((n) => api.overlaps(api.position(added), api.position(n))),
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

// Auto-layout sends what is on screen — including the node just added, which
// the server has never seen — and applies the grid that comes back.
requests.length = 0;
await api.relayout();
const laidOut = requests.find((r) => r.url.endsWith("/layout"));
out.relayout = {
  method: laidOut.method,
  url: laidOut.url,
  carries_the_unsaved_node: laidOut.body.plan.nodes.some((n) => n.id === added.id),
  position: api.position(plan.nodes[0]),
  every_node_placed: plan.nodes.every((n) => typeof (n.metadata || {}).x === "number"),
  overlaps: plan.nodes.some((a) =>
    plan.nodes.some((b) => a.id !== b.id && api.overlaps(api.position(a), api.position(b)))),
};

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

// Conditions. A routing node is drawn as a diamond and its branches as
// labelled, coloured edges — a loop the user cannot see is a loop they cannot fix.
api.addCondition();
const check = plan.nodes[plan.nodes.length - 1];
out.condition_added = {
  id: check.id,
  kind: check.kind,
  budget: (check.condition || {}).max_iterations,
  on_exhaustion: (check.condition || {}).on_exhaustion,
  reviewers: check.reviewers,
};
out.condition_panel = byId.side.children.map((c) => c.textContent || "").join(" | ");

// Wire it into a real loop: exploration feeds it, "no" rewinds to exploration,
// "yes" moves on to documentation.
const branch = (upstream, downstream, kind) =>
  plan.edges.push({ upstream, downstream, kind, inject: "full", metadata: {} });
branch("exploration", check.id, "requires");
branch(check.id, "exploration", "on_false");
branch(check.id, "documentation", "on_true");
api.render();

const back = api.backBranches();
out.classified = {
  back: [...back].map((e) => `${e.upstream}->${e.downstream}`),
  total_branches: plan.edges.filter((e) => e.kind.startsWith("on_")).length,
};

const groupFor = (id) => byId.canvas.children.find((c) => c.attrs["data-id"] === id);
const checkGroup = groupFor(check.id);
out.condition_render = {
  classes: checkGroup.attrs.class,
  shapes: checkGroup.children.map((c) => c.tagName),
};

const paths = byId.canvas.children.filter(
  (c) => c.tagName === "path" && (c.attrs.class || "").startsWith("edge"));
out.edge_render = {
  loops: paths.filter((c) => c.attrs.class.includes("loop")).length,
  branches: paths.filter((c) => c.attrs.class.includes("branch")).length,
  labels: byId.canvas.children
    .filter((c) => c.tagName === "text" && c.attrs.class === "edge-label")
    .map((c) => c.textContent),
};

// The condition panel is what a physicist edits the loop through.
api.select("node", check.id);
out.condition_side = byId.side.children.map((c) => c.textContent || "").filter(Boolean);

// Selecting the loop edge explains that it loops.
const loopIndex = plan.edges.findIndex((e) => e.kind === "on_false");
api.select("edge", loopIndex);
out.loop_edge_side = byId.side.children.map((c) => c.textContent || "").join(" | ");

// The node panel is a flat run of label/control pairs, so a control is found by
// the label above it. A `toggle` builds its label out of text nodes instead,
// which is why it needs its own lookup.
const controlAfter = (label, tag) => {
  const kids = byId.side.children;
  const at = kids.findIndex((c) => c.tagName === "label" && c.textContent === label);
  return at < 0 ? null : kids.slice(at + 1).find((c) => c.tagName === tag);
};
// Some controls sit in a two-column row rather than directly under the panel,
// so this one walks the whole subtree in document order.
const controlAnywhere = (label, tag) => {
  const flat = [];
  const walk = (el) => { for (const c of el.children || []) { flat.push(c); walk(c); } };
  walk(byId.side);
  const at = flat.findIndex((c) => c.tagName === "label" && c.textContent === label);
  return at < 0 ? null : flat.slice(at + 1).find((c) => c.tagName === tag);
};
const toggleLabelled = (text) => byId.side.children.find(
  (c) => c.tagName === "label" && c.children.some((k) => k.text === text));

// --- Node kind is editable, and the condition payload follows it.
api.select("node", added.id);
const kindSelect = controlAfter("Kind", "select");
out.kind = {
  options: kindSelect.children.map((o) => o.value),
  initial: kindSelect.value,
};
kindSelect.value = "condition";
kindSelect.dispatch("change");
out.kind.after_switch = {
  kind: added.kind,
  has_condition: Boolean(added.condition),
  budget: (added.condition || {}).max_iterations,
  heading: byId.side.children[0].textContent,
  shape: groupFor(added.id).children[0].tagName,
  dirty: api.dirty(),
};
const kindSelectBack = controlAfter("Kind", "select");
kindSelectBack.value = "gate";
kindSelectBack.dispatch("change");
out.kind.after_switch_back = {
  kind: added.kind,
  // P10 refuses a condition payload on a node that is not a condition.
  condition: added.condition,
  shape: groupFor(added.id).children[0].tagName,
  heading: byId.side.children[0].textContent,
};

// --- Capabilities: tools, skills and MCP servers, beside gates and reviewers.
api.select("node", added.id);
out.capabilities = {
  labels: byId.side.children
    .filter((c) => c.tagName === "label")
    .map((c) => c.textContent)
    .filter((t) => ["Reviewers", "Gates", "Function tools", "Skills", "MCP servers"].includes(t)),
  tools_before: added.tools,
  // With no allowlist there is nothing to pick from — the toggle owns that.
  picker_before: Boolean(controlAfter("Function tools", "select")),
};

const restrict = toggleLabelled("Restrict function tools");
out.capabilities.has_restrict_toggle = Boolean(restrict);
restrict.children[0].checked = true;
restrict.children[0].dispatch("change");
out.capabilities.tools_after_restrict = added.tools;

const toolPicker = controlAfter("Function tools", "select");
out.capabilities.tool_options = toolPicker.children.map((o) => o.value).filter(Boolean);
toolPicker.value = toolPicker.children[1].value;
toolPicker.dispatch("change");
out.capabilities.tools_after_pick = added.tools;

out.capabilities.labels_after_restrict = byId.side.children
  .filter((c) => c.tagName === "label")
  .map((c) => c.textContent)
  .filter((t) => ["Reviewers", "Gates", "Function tools", "Skills", "MCP servers"].includes(t));

// The chosen name becomes a removable pill and leaves the dropdown. A pill is
// built out of a text node and its remove button, so the name is the first.
out.capabilities.tags_after_pick = controlAfter("Function tools", "div")
  .children.map((c) => c.children[0].text);
out.capabilities.options_after_pick =
  controlAfter("Function tools", "select").children.map((o) => o.value).filter(Boolean);
controlAfter("Function tools", "div").children[0].children[1].dispatch("click");
out.capabilities.tools_after_remove = added.tools;

// Turning the restriction off restores the default set, which is `null` rather
// than an empty list.
toggleLabelled("Restrict function tools").children[0].checked = false;
toggleLabelled("Restrict function tools").children[0].dispatch("change");
out.capabilities.tools_after_unrestrict = added.tools;

const skillPicker = controlAfter("Skills", "select");
out.capabilities.skill_options = skillPicker.children.map((o) => o.value).filter(Boolean);
skillPicker.value = skillPicker.children[1].value;
skillPicker.dispatch("change");
out.capabilities.skills_after_pick = added.skills;

const mcpPicker = controlAfter("MCP servers", "select");
out.capabilities.mcp_options = mcpPicker.children.map((o) => o.value).filter(Boolean);
out.capabilities.mcp_before = added.mcp_servers;

// Reviewers come from the served catalog rather than a list baked into the page.
out.capabilities.reviewer_options = controlAfter("Reviewers", "div")
  .children.map((c) => c.children[1].text);

// --- Platform and model: the two halves of a node's `model` string, and the
// models listed on demand from the platform the user picks.
api.select("node", added.id);
out.model = { before: added.model };
const platformSelect = controlAnywhere("Platform", "select");
out.model.platform_options = platformSelect.children.map((o) => o.value);
out.model.no_request_before_a_platform_is_picked =
  requests.filter((r) => r.url.includes("/models")).length === 0;
platformSelect.value = "cborg";
platformSelect.dispatch("change");
// Picking a platform alone means "that platform, at its own default model".
out.model.after_platform = added.model;
await tick();
await tick();
out.model.model_requests = requests.filter((r) => r.url.includes("/models")).map((r) => r.url);
const modelSelect = controlAfter("Model", "select");
out.model.model_options = modelSelect.children.map((o) => o.value);
out.model.default_option_text = modelSelect.children[0].textContent;
modelSelect.value = "big-model";
modelSelect.dispatch("change");
out.model.after_model = added.model;
// A model belongs to the platform it was listed from, so switching platform
// drops it rather than carrying it across.
const switched = controlAnywhere("Platform", "select");
switched.value = "openai";
switched.dispatch("change");
out.model.after_switch = added.model;
// Clearing the platform hands the node back to the run's own model.
const cleared = controlAnywhere("Platform", "select");
cleared.value = "";
cleared.dispatch("change");
out.model.after_clear = added.model;
out.model.free_text_when_no_platform = Boolean(controlAfter("Model override", "input"));

// The node panel leads with what can be done to the node: run just it, delete
// it, save the edit. Save is dead until there is something to save, so the
// document is put back in a saved state before it is asked.
await api.save();
api.select("node", "strategy");
const actionRow = byId.side.children[1];
out.node_actions = {
  class: actionRow.className,
  labels: actionRow.children.map((b) => b.textContent),
  run_disabled_when_clean: actionRow.children[0].disabled,
  save_disabled_when_clean: actionRow.children[2].disabled,
};

// Editing a field enables Save without rebuilding the form under the cursor.
const labelField = controlAfter("Label", "input");
labelField.value = "Strategy and commitments";
labelField.dispatch("input");
out.node_actions.save_disabled_after_edit = actionRow.children[2].disabled;
out.node_actions.same_row_after_edit = byId.side.children[1] === actionRow;

// Run saves the edit first — what runs is the plan on disk — and then launches
// this node alone. It never touches /approve: running one node is a statement
// about that node, not about the pipeline.
requests.length = 0;
runResponse = {
  name: "zbb", status: "running", active: true, unattended: false,
  nodes: { strategy: "running" }, latest_seq: 0, prompt: null, events: [],
};
await api.runNode("strategy");
out.node_run = {
  calls: requests.map((r) => `${r.method} ${r.url}`),
  body: requests.find((r) => r.method === "POST" && r.url.endsWith("/run")).body,
};

// A second Run while that one is in flight is refused, by the button and by the
// function behind it.
api.select("node", "strategy");
out.node_run.run_disabled_while_running = byId.side.children[1].children[0].disabled;
requests.length = 0;
await api.runNode("strategy");
out.node_run.calls_while_running = requests.length;
runResponse = null;

// Delete removes the node and every edge that touched it.
api.select("node", added.id);
const edgesTouching = plan.edges.filter(
  (e) => e.upstream === added.id || e.downstream === added.id).length;
byId.side.children[1].children[1].dispatch("click");
out.node_delete = {
  edges_it_had: edgesTouching,
  still_present: plan.nodes.some((n) => n.id === added.id),
  edges_left: plan.edges.filter(
    (e) => e.upstream === added.id || e.downstream === added.id).length,
  panel_heading: byId.side.children[0].textContent,
};

// Put the run state back to idle and let the state fetch it triggered settle,
// so the sections below observe a page that is not mid-run.
api.applyRun({
  name: "zbb", status: "idle", active: false, unattended: false,
  nodes: {}, latest_seq: 0, prompt: null, events: [],
});
await tick();
await tick();

// A run in flight paints itself onto the plan and logs its progress. The
// snapshots below are what `GET /api/plan/{name}/run` returns.
const nodeGroup = (id) => byId.canvas.children.find((c) => c.attrs["data-id"] === id);
api.applyRun({
  name: "zbb", status: "running", active: true, unattended: false,
  nodes: { strategy: "done", selection: "running" }, latest_seq: 2, prompt: null,
  events: [
    { seq: 1, node: "strategy", message: "PASS", level: "info" },
    { seq: 2, node: "selection", message: "executor starting", level: "info" },
  ],
});
out.run = {
  panel_hidden: byId.run.hidden,
  state: byId["run-state"].textContent,
  detail: byId["run-detail"].textContent,
  log_rows: byId["run-log"].children.length,
  done_node_class: nodeGroup("strategy").attrs.class,
  running_node_class: nodeGroup("selection").attrs.class,
  approve_disabled_while_running: byId.approve.disabled,
};

// The Progress tab renders what the analysis has established. It is populated
// by the /state fetch the page makes on load, independently of any run.
out.progress = {
  steps: byId.progress.children[0].children.map((p) => ({
    label: p.children[1].textContent,
    classes: p.className,
  })),
  heading: byId.progress.children[1].textContent,
  groups: byId.progress.children[2].children.map((g) => ({
    classes: g.className,
    title: g.children[0].children[0].textContent,
    count: g.children[0].children[1].textContent,
    // A process row nests its name a level down; the "none identified"
    // placeholder is a bare div, and the panel has to show one or the other.
    rows: g.children.slice(1).map((r) =>
      r.className === "empty" ? r.textContent : r.children[0].children[0].textContent),
  })),
  log_hidden_by_default: byId["run-log"].hidden,
};
byId["tab-log"].dispatch("click");
out.progress.log_shown_after_click = !byId["run-log"].hidden;
out.progress.progress_hidden_after_click = byId.progress.hidden;
byId["tab-progress"].dispatch("click");

// The run log is the main stage: everything the agents narrate lands here, one
// scannable line each, with the body folded behind the line that names it.
byId["run-log"].children.length = 0;
api.applyRun({
  name: "zbb", status: "running", active: true, unattended: false,
  nodes: { selection: "running" }, latest_seq: 20, prompt: null,
  events: [
    { seq: 11, node: "selection", message: "executor starting", level: "info",
      kind: "progress", agent: "", detail: "" },
    { seq: 12, node: "selection", message: "root -l -q fit.C", level: "info",
      kind: "tool", agent: "Phase Executor",
      detail: "thought: fit the mass peak\n\n{\n  \"cmd\": \"root -l -q fit.C\"\n}" },
    { seq: 13, node: "selection", message: "bash returned", level: "warning",
      kind: "result", agent: "Phase Executor", detail: "segmentation violation" },
    { seq: 14, node: "selection", message: "I will retry with a wider range",
      level: "info", kind: "message", agent: "Phase Executor",
      detail: "I will retry with a wider range" },
  ],
});
const logRows = byId["run-log"].children;
out.run_log = {
  rows: logRows.length,
  tags: logRows.map((r) => r.tagName),
  classes: logRows.map((r) => r.className),
  // A folded row is <details><summary>node · agent · headline</summary><pre>…</pre>.
  tool_summary: logRows[1].children[0].children.map((c) => c.textContent),
  tool_detail: logRows[1].children[1].textContent,
  warning_class: logRows[2].className,
};

// Narration alone must not re-read the analysis: a run narrates every turn, and
// re-fetching the graph and the process inventory once per poll for the length
// of a run is a cost the Progress tab does not earn.
requests.length = 0;
api.applyRun({
  name: "zbb", status: "running", active: true, unattended: false,
  nodes: { selection: "running" }, latest_seq: 21, prompt: null,
  events: [{ seq: 21, node: "selection", message: "ls -la", level: "info",
             kind: "tool", agent: "Phase Executor", detail: "" }],
});
await tick();
out.run_log.state_calls_after_narration = requests.filter(
  (r) => r.url.endsWith("/state")).length;

// A node boundary is a different matter: something the analysis knows may have
// moved with it.
requests.length = 0;
api.applyRun({
  name: "zbb", status: "running", active: true, unattended: false,
  nodes: { selection: "running" }, latest_seq: 22, prompt: null,
  events: [{ seq: 22, node: "selection", message: "PASS", level: "info",
             kind: "progress", agent: "", detail: "" }],
});
await tick();
out.run_log.state_calls_after_boundary = requests.filter(
  (r) => r.url.endsWith("/state")).length;

// Filtering is a class on the container, so thousands of rows stay put.
byId["tab-log"].dispatch("click");
out.run_log.filter_visible = !byId["log-filter"].hidden;
out.run_log.rows_before_filter = byId["run-log"].children.length;
byId["log-filter"].value = "only-phases";
byId["log-filter"].dispatch("change");
out.run_log.filter_class = byId["run-log"].classList.contains("only-phases");
out.run_log.rows_after_filter = byId["run-log"].children.length;
byId["log-filter"].value = "all";
byId["log-filter"].dispatch("change");
out.run_log.filter_class_after_all = byId["run-log"].classList.contains("only-phases");
byId["tab-progress"].dispatch("click");

// The dock is resizable: dragging its grip upward makes both tab bodies taller,
// and the height it settles on is what gets remembered.
const grip = byId["dock-grip"];
grip.dispatch("pointerdown", { clientY: 500, pointerId: 7, button: 0, preventDefault() {} });
grip.dispatch("pointermove", { clientY: 380, pointerId: 7 });
out.dock = {
  height_while_dragging: byId.dock.style.getPropertyValue("--dock-h"),
  resizing_class: byId.dock.classList.contains("resizing"),
};
grip.dispatch("pointerup", { pointerId: 7 });
out.dock.resizing_class_after_release = byId.dock.classList.contains("resizing");
grip.dispatch("dblclick", {});
out.dock.height_after_reset = byId.dock.style.getPropertyValue("--dock-h");

// A blocked run asks its question here; approving it answers the prompt by id.
api.applyRun({
  name: "zbb", status: "blocked", active: true, unattended: false,
  nodes: { strategy: "done", selection: "running" }, latest_seq: 3, prompt: {
    id: "1", kind: "approval", text: "Run this command?", cmd: "root -l -q fit.C",
    cwd: "/analyses/zbb/selection", thought: "Fit the mass peak.", choices: [],
  },
  events: [{ seq: 3, node: "human", message: "root -l -q fit.C", level: "info" }],
});
out.ask = {
  hidden: byId.ask.hidden,
  title: byId["ask-title"].textContent,
  cmd: byId["ask-cmd"].textContent,
  cmd_hidden: byId["ask-cmd"].hidden,
  input_hidden: byId["ask-input"].hidden,
  actions: byId["ask-actions"].children.map((b) => b.textContent),
};

requests.length = 0;
nextResponse = {
  name: "zbb", status: "running", active: true, unattended: false,
  nodes: { strategy: "done", selection: "running" }, latest_seq: 4, prompt: null, events: [],
};
byId["ask-actions"].children[0].dispatch("click");
await tick();
await tick();
out.ask_answer = {
  calls: requests.map((r) => `${r.method} ${r.url}`),
  body: requests[0] ? requests[0].body : null,
  hidden_after: byId.ask.hidden,
};

// --- The predefined-node library. Opening it fetches the catalog once and
// groups it by the pipeline each node came from; picking one inserts a copy.
requests.length = 0;
await api.openLibrary();
const rows = () => byId["library-list"].children;
out.library = {
  hidden_before: false,
  fetches: requests.map((r) => `${r.method} ${r.url}`),
  open: byId.library.hidden === false,
  entries: rows().map((c) => `${c.className}:${c.textContent
    || (c.children[0] ? c.children[0].textContent : "")}`),
};

// Filtering narrows the list without going back to the server.
requests.length = 0;
byId["library-search"].value = "limits";
byId["library-search"].dispatch("input", { target: byId["library-search"] });
out.library.filtered = rows()
  .filter((c) => c.className === "row")
  .map((c) => c.children[0].textContent);
out.library.refetched_on_filter = requests.length;

byId["library-search"].value = "";
byId["library-search"].dispatch("input", { target: byId["library-search"] });

const nodesBefore = plan.nodes.length;
const strategyRow = rows().find(
  (c) => c.className === "row" && c.children[0].textContent === "Strategy");
strategyRow.dispatch("click");
const inserted = plan.nodes[plan.nodes.length - 1];
out.library.inserted = {
  added: plan.nodes.length - nodesBefore,
  // The plan already has a "strategy"; the copy must not collide with it.
  id: inserted.id,
  directory: inserted.directory,
  prompt: inserted.prompt,
  reviewers: inserted.reviewers,
  contract: inserted.contract,
  placed: typeof (inserted.metadata || {}).x === "number",
  overlaps_an_existing_node: plan.nodes
    .filter((n) => n.id !== inserted.id)
    .some((n) => api.overlaps(api.position(inserted), api.position(n))),
  closed_after_pick: byId.library.hidden,
  selected_heading: byId.side.children[0].textContent,
  dirty: api.dirty(),
};

// Re-opening does not re-fetch: the catalog is a property of the installation,
// not of the plan on screen.
requests.length = 0;
await api.openLibrary();
out.library.refetched_on_reopen = requests.length;
api.closeLibrary();
out.library.hidden_after_close = byId.library.hidden;

process.stdout.write(JSON.stringify(out, null, 1));
