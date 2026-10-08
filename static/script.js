// Same-origin API: works on localhost and on the deployed domain alike
const API_BASE = window.location.origin;

// Match server hard limit (task_time_limit = 600s)
const MAX_TASK_POLL_MS = 600_000;
const MAX_POLL_ERRORS = 5;

// Below this width the path reads top-to-bottom instead of left-to-right
const VERTICAL_BREAKPOINT = 520;
const VERTICAL_SPACING = 92;
const SEED_PAD_X = 70;
const NODE_R = 14;

const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);

const formatSeconds = (seconds) =>
  Number.isFinite(seconds) ? `${seconds.toFixed(1)} s` : "—";

const wikipediaUrl = (page) =>
  `https://en.wikipedia.org/wiki/${encodeURIComponent(page)}`;

function openWikipediaPage(page) {
  if (page && page !== "-") window.open(wikipediaUrl(page), "_blank");
}

// localStorage can throw (private mode, blocked site data) or hold garbage
// from an older version; neither may stop the UI from starting.
const StateManager = {
  KEY: "iris_state",

  save(data) {
    try {
      localStorage.setItem(this.KEY, JSON.stringify(data));
    } catch {
      /* storage unavailable */
    }
  },

  load() {
    try {
      const stored = localStorage.getItem(this.KEY);
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  },

  clear() {
    try {
      localStorage.removeItem(this.KEY);
    } catch {
      /* storage unavailable */
    }
  },
};

// Inner width available to the graph, excluding border and padding.
function contentWidth(el) {
  const style = getComputedStyle(el);
  return (
    el.clientWidth -
    parseFloat(style.paddingLeft) -
    parseFloat(style.paddingRight)
  );
}

function graphHeight(vertical, nodeCount) {
  return vertical
    ? Math.max(380, Math.min(760, 112 + (nodeCount - 1) * VERTICAL_SPACING))
    : Math.max(400, Math.min(600, nodeCount * 60));
}

// Horizontal space each hop gets when the path is spread across the width
function spanLimit(width, nodeCount) {
  return nodeCount > 1 ? (width - SEED_PAD_X * 2) / (nodeCount - 1) : width;
}

// Seed x in path order so the chain renders in reading direction
function seedX(vertical, width, i, nodeCount) {
  if (vertical) return width / 2 + (i % 2 === 0 ? -1 : 1) * 12;
  const t = nodeCount === 1 ? 0.5 : i / (nodeCount - 1);
  return SEED_PAD_X + t * (width - SEED_PAD_X * 2);
}

function horizontalLinkDistance(maxBoxWidth, span) {
  return Math.max(100, Math.min(maxBoxWidth + 36, Math.max(100, span)));
}

// Simple debounce to avoid thrashing on mobile address bar show/hide
function debounce(fn, wait) {
  let t;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), wait);
  };
}

// Turn any failure into what the visitor needs: what happened, which field
// (if one is at fault), and what to try next. Server messages are shown only
// when they are written for people; internal ones stay in the server logs.
function describeFailure({ code, message, details }, route) {
  const quoted = (title) =>
    Boolean(title) &&
    (message || "").toLowerCase().includes(`'${title.toLowerCase()}'`);

  switch (code) {
    case "PAGE_NOT_FOUND": {
      const field = quoted(route.start) ? "start" : "end";
      return {
        title: field === "start" ? "Start page not found" : "End page not found",
        message: `English Wikipedia has no article titled “${route[field]}”.`,
        hint: "Check the spelling, or copy the title from the end of the article’s URL.",
        field,
      };
    }
    case "DISAMBIGUATION_PAGE":
      return {
        title: "End page is ambiguous",
        message: `“${route.end}” is a disambiguation page that lists several articles, so there is no single page to reach.`,
        hint: "Pick the specific article, for example “Mercury (planet)” rather than “Mercury”.",
        field: "end",
      };
    case "PATH_NOT_FOUND": {
      const depth = /within (\d+) steps/.exec(message || "")?.[1];
      return {
        title: "No route found",
        message: depth
          ? `The two searches went ${depth} links deep in total without meeting.`
          : "The two searches ran out of pages without meeting.",
        hint: "Try a more general end page. Pages with few incoming links are hard to reach.",
      };
    }
    case "INVALID_REQUEST": {
      const [field, problems] = Object.entries(details || {})[0] || [];
      return {
        title: "Check the two pages",
        message: (Array.isArray(problems) && problems[0]) || message,
        field: field === "start" || field === "end" ? field : undefined,
      };
    }
    case "WIKIPEDIA_API_ERROR":
      return {
        title: "Wikipedia isn’t responding",
        hint: "This is usually brief. Try again in a minute.",
        retry: true,
      };
    case "TIMEOUT":
      return {
        title: "The search took too long",
        hint: "Searches stop after ten minutes. Try two pages that are more closely related.",
        retry: true,
      };
    case "OFFLINE":
      return {
        title: "Can’t reach Iris",
        hint: "Check your connection, then try again.",
        retry: true,
      };
    case "STOPPED":
      return {
        title: "Search stopped",
        hint: "Nothing was found before it stopped. Run it again whenever you like.",
        retry: true,
        neutral: true,
      };
    default:
      return {
        title: "Something went wrong on our side",
        hint: "Try again. If it keeps happening, the search service may be down.",
        retry: true,
      };
  }
}

class PathFinderUI {
  constructor() {
    this.taskId = null;
    this.taskStartTime = null;
    this.pollTimeoutId = null;
    this.abortController = null;

    this.initializeGraph();
    this.setupEventListeners();
    this.restoreStateFromStorage();
  }

  initializeGraph() {
    const svg = d3.select("#graph");
    svg.selectAll("*").remove();

    // Reuse existing tooltip if present to avoid duplicates
    this.tooltip = d3.select(".tooltip");
    if (this.tooltip.empty()) {
      this.tooltip = d3.select("body").append("div").attr("class", "tooltip");
    }

    this.graph = {
      svg,
      width: 0, // Set in renderGraph
      height: 500,
      vertical: false,
      dynamicDistance: 0,
      simulation: null,
    };
  }

  setupEventListeners() {
    $("route").addEventListener("submit", (e) => {
      e.preventDefault();
      if (!$("findPathBtn").disabled) this.findPath();
    });
    $("cancelBtn").addEventListener("click", () => this.cancelSearch());
    $("clearBtn").addEventListener("click", () => this.clearVisualization());
    $("retryBtn").addEventListener("click", () => this.findPath());
    $("dismissBtn").addEventListener("click", () => this.hideNotice());

    for (const id of ["startPage", "endPage"]) {
      $(id).addEventListener("input", () => {
        $(id).removeAttribute("aria-invalid");
        this.saveCurrentState();
        this.updateButtonState();
      });
    }

    window.addEventListener(
      "resize",
      debounce(() => {
        if ($("visualizationSection").classList.contains("show")) {
          this.resizeGraph();
        }
      }, 120),
    );
  }

  restoreStateFromStorage() {
    const saved = StateManager.load();
    if (saved) {
      if (saved.startPage) $("startPage").value = saved.startPage;
      if (saved.endPage) $("endPage").value = saved.endPage;

      if (saved.taskId && saved.status === "IN_PROGRESS") {
        // Resume polling the search that was running before the reload
        this.taskId = saved.taskId;
        this.taskStartTime = saved.taskStartTime || Date.now();
        this.showVisualizationSection();
        this.showLoading();
        this.pollTaskStatus();
      } else if (saved.result?.path) {
        this.showVisualizationSection();
        this.handlePathFound(saved.result);
      }
    }

    this.updateButtonState();
  }

  saveCurrentState() {
    StateManager.save({
      startPage: $("startPage").value,
      endPage: $("endPage").value,
      taskId: this.taskId,
      taskStartTime: this.taskStartTime,
      status: this.taskId ? "IN_PROGRESS" : "IDLE",
      timestamp: Date.now(),
    });
  }

  route() {
    return {
      start: $("startPage").value.trim(),
      end: $("endPage").value.trim(),
    };
  }

  updateButtonState() {
    const { start, end } = this.route();
    // Disabled only while a search runs or a field is empty. Re-running the
    // route on screen is allowed: it is answered from the path cache.
    $("findPathBtn").disabled = Boolean(this.taskId) || !start || !end;
  }

  clearActiveTask() {
    if (this.pollTimeoutId) {
      clearTimeout(this.pollTimeoutId);
      this.pollTimeoutId = null;
    }
    // Abort any in-flight fetch requests
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    this.taskId = null;
    this.taskStartTime = null;
    this.updateButtonState();
  }

  showLoading() {
    // Swap find path → stop
    $("findPathBtn").classList.add("hidden");
    $("cancelBtn").classList.remove("hidden");
    this.hideNotice();
  }

  hideLoading() {
    $("cancelBtn").classList.add("hidden");
    $("findPathBtn").classList.remove("hidden");
    this.updateButtonState();
  }

  showNotice({ title, message, hint, field, retry, neutral }) {
    this.hideLoading();
    const notice = $("notice");
    notice.classList.toggle("is-neutral", Boolean(neutral));
    $("noticeTitle").textContent = title;
    $("noticeMessage").textContent = message || "";
    $("noticeHint").textContent = hint || "";
    $("retryBtn").classList.toggle("hidden", !retry);

    // Restart the entrance so a repeated error still reads as new
    notice.classList.add("hidden");
    void notice.offsetWidth;
    notice.classList.remove("hidden");

    for (const [name, id] of [
      ["start", "startPage"],
      ["end", "endPage"],
    ]) {
      if (name === field) $(id).setAttribute("aria-invalid", "true");
      else $(id).removeAttribute("aria-invalid");
    }
    if (field) $(field === "start" ? "startPage" : "endPage").focus();
  }

  hideNotice() {
    $("notice").classList.add("hidden");
  }

  // End the current search and explain why. Every failure path ends here.
  fail(failure) {
    const route = this.route();
    this.clearActiveTask();
    $("visualizationSection").classList.remove("show");
    StateManager.clear();
    this.saveCurrentState(); // keep what was typed
    this.showNotice(describeFailure(failure, route));
  }

  showVisualizationSection() {
    $("visualizationSection").classList.add("show");
    this.showProgressLoader();
    this.resetProgressUI();
  }

  setRoute(start, end) {
    $("routeFrom").textContent = start || "—";
    $("routeTo").textContent = end || "—";
    $("waitFrom").textContent = start || "—";
    $("waitTo").textContent = end || "—";
  }

  // Searching: readings and the two-ended wait scene; no graph yet
  showProgressLoader() {
    for (const id of ["activity", "searchProgress", "waitScene"]) {
      $(id).classList.remove("hidden");
    }
    $("graph").classList.add("hidden");
    $("runState").dataset.state = "searching";
    $("runState").textContent = "Searching";
  }

  resetProgressUI() {
    const { start, end } = this.route();
    this.setRoute(start, end);

    $("nodesExplored").textContent = "0";
    $("queueSize").textContent = "0";
    $("elapsedTime").textContent = formatSeconds(0);
    this.setLastNode(null);

    // Reset depth. The real budget arrives with the first progress update;
    // until then keep whatever count is already rendered.
    this.updateDepthIndicator(0, $("depthDots").children.length || 6);
  }

  setLastNode(title) {
    const el = $("lastNode");
    el.textContent = title || "—";
    el.classList.toggle("disabled", !title);
    el.setAttribute("aria-disabled", String(!title));
    el.setAttribute("tabindex", title ? "0" : "-1");
    if (title) el.href = wikipediaUrl(title);
    else el.removeAttribute("href");
  }

  updateProgressDisplay(progress) {
    const stats = progress?.search_stats;
    if (!stats) return;

    this.setRoute(stats.start_page, stats.end_page);
    this.updateDepthIndicator(stats.current_depth || 0, stats.max_depth || 6);
    $("nodesExplored").textContent =
      stats.nodes_explored?.toLocaleString() || "0";
    $("queueSize").textContent = stats.queue_size?.toLocaleString() || "0";
    $("elapsedTime").textContent = formatSeconds(
      progress.search_time_elapsed || 0,
    );
    this.setLastNode(stats.last_node);
  }

  updateDepthIndicator(currentDepth, maxDepth) {
    const container = $("depthDots");
    const total = Math.max(1, Math.round(maxDepth));

    // Reconcile the dot count with the depth budget the server reported.
    while (container.children.length > total) {
      container.lastElementChild.remove();
    }
    while (container.children.length < total) {
      const dot = document.createElement("span");
      dot.className = "depth-dot";
      container.appendChild(dot);
    }

    [...container.children].forEach((dot, index) => {
      dot.classList.toggle("completed", index < currentDepth);
      dot.classList.toggle("active", index === currentDepth);
    });
  }

  // Found: the readings give way to the graph
  showGraphVisualization() {
    for (const id of ["activity", "searchProgress", "waitScene"]) {
      $(id).classList.add("hidden");
    }
    $("graph").classList.remove("hidden");
  }

  hidePathDisplay() {
    $("pathStepsContainer").classList.add("hidden");
    this.graph.svg.selectAll("*").remove();
  }

  async findPath() {
    const { start, end } = this.route();

    if (!start || !end) {
      this.showNotice({
        title: "Enter both pages",
        hint: "Iris needs a page to start from and a page to reach.",
        field: start ? "end" : "start",
      });
      return;
    }

    // A new search replaces any running one
    if (this.taskId) this.clearActiveTask();

    try {
      this.showLoading();
      this.hidePathDisplay();
      this.showVisualizationSection();

      this.abortController = new AbortController();
      const response = await fetch(`${API_BASE}/getPath`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start, end }),
        signal: this.abortController.signal,
      });

      if (!response.ok) {
        let body = {};
        try {
          body = await response.json();
        } catch {
          /* non-JSON response */
        }
        this.fail({
          code: body.code,
          message: body.message,
          details: body.details,
        });
        return;
      }

      const data = await response.json();
      this.taskId = data.task_id;
      this.taskStartTime = Date.now();
      this.saveCurrentState();
      this.pollTaskStatus();
    } catch (error) {
      if (error.name === "AbortError") return;
      this.fail({ code: "OFFLINE" });
    }
  }

  async pollTaskStatus(pollErrors = 0) {
    // Capture the task this poll chain belongs to. After every await, a
    // mismatch means a cancel or new search happened and this chain must
    // die silently.
    const taskId = this.taskId;
    if (!taskId) return;

    if (
      this.taskStartTime &&
      Date.now() - this.taskStartTime > MAX_TASK_POLL_MS
    ) {
      this.fail({ code: "TIMEOUT" });
      return;
    }

    const pollAgain = (delay, errors = 0) => {
      this.pollTimeoutId = setTimeout(() => this.pollTaskStatus(errors), delay);
    };

    try {
      const response = await fetch(`${API_BASE}/tasks/status/${taskId}`, {
        signal: this.abortController?.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const data = await response.json();
      if (this.taskId !== taskId) return;

      switch (data.status) {
        case "PENDING":
          // Waiting for a worker; the route is already on screen
          this.showProgressLoader();
          pollAgain(1000);
          break;

        case "IN_PROGRESS":
          this.showProgressLoader();
          this.updateProgressDisplay(data.progress);
          pollAgain(1000);
          break;

        case "SUCCESS":
          this.handlePathFound(data.result);
          break;

        case "FAILURE":
          this.fail({ code: data.code, message: data.error });
          break;

        case "REVOKED":
          this.fail({ code: "STOPPED" });
          break;

        default:
          this.fail({});
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      // Stale chain — don't clobber the new search's UI
      if (this.taskId !== taskId) return;

      // Transient failure (timeout, network blip) — retry, then give up
      const errors = pollErrors + 1;
      if (errors < MAX_POLL_ERRORS) {
        pollAgain(2000, errors);
        return;
      }
      this.fail({ code: "OFFLINE" });
    }
  }

  handlePathFound(result) {
    this.hideLoading();

    if (!result?.path?.length) {
      this.fail({ code: "PATH_NOT_FOUND" });
      return;
    }

    const state = StateManager.load() || {};
    state.result = result;
    state.status = "COMPLETED";
    StateManager.save(state);

    const path = result.path;
    this.setRoute(path[0], path[path.length - 1]);
    $("runState").dataset.state = "found";
    $("runState").textContent = `Found in ${formatSeconds(result.search_time)}`;

    this.showGraphVisualization();
    this.visualizePath(path);
    this.displayPathList(path, result);

    // Completed: release the task without aborting anything
    this.taskId = null;
    this.taskStartTime = null;
    this.updateButtonState();
  }

  // Re-render graph responsively based on saved, completed result
  rerenderFromState() {
    const saved = StateManager.load();
    if (saved?.status === "COMPLETED" && saved.result?.path) {
      this.initializeGraph();
      this.visualizePath(saved.result.path);
      this.showGraphVisualization();
    }
  }

  visualizePath(path) {
    const nodes = path.map((page, index) => ({
      id: page,
      name: page,
      index,
      isStart: index === 0,
      isEnd: index === path.length - 1,
    }));

    const links = path.slice(1).map((page, i) => ({
      source: path[i],
      target: page,
      isPath: true,
    }));

    this.renderGraph(nodes, links);
  }

  renderGraph(nodes, links) {
    const graph = this.graph;
    const { svg } = graph;

    svg.selectAll("*").remove();

    // Content-box width. clientWidth excludes borders, so subtracting the
    // padding lands exactly on the box the SVG is stretched to fill; using
    // getBoundingClientRect here left the viewBox 2px wider than the element
    // and scaled every node position by a fraction of a percent.
    const width = contentWidth($("graphContainer"));
    const nodeCount = nodes.length;

    const vertical = width < VERTICAL_BREAKPOINT;
    const calculatedHeight = graphHeight(vertical, nodeCount);
    graph.vertical = vertical;
    graph.width = width;
    graph.height = calculatedHeight;

    // Set SVG dimensions and accessible label
    const pathDescription = nodes.map((n) => n.name).join(" → ");
    svg
      .attr("width", width)
      .style("height", calculatedHeight + "px")
      .attr("viewBox", `0 0 ${width} ${calculatedHeight}`)
      .attr("aria-label", `Path visualization: ${pathDescription}`)
      .style("display", "block");

    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    // Measure label text with the real font instead of estimating by character count
    const measureCtx = document.createElement("canvas").getContext("2d");
    measureCtx.font = "500 12px 'JetBrains Mono', monospace";

    const ELLIPSIS = "…";
    const measure = (s) => measureCtx.measureText(s).width;
    const trimToBudget = (s, budget) => {
      if (measure(s) <= budget) return s;
      let t = s;
      while (t.length > 1 && measure(t + ELLIPSIS) > budget) {
        t = t.slice(0, -1);
      }
      return t.trimEnd() + ELLIPSIS;
    };
    // Wrap a title onto at most two lines, trimming only when unavoidable
    const buildLabelLines = (name, budget) => {
      if (measure(name) <= budget) return [name];
      const words = name.split(" ");
      if (words.length === 1) return [trimToBudget(name, budget)];
      let line1 = "";
      let i = 0;
      while (i < words.length) {
        if (!line1) {
          line1 = words[i];
          i++;
        } else if (measure(line1 + " " + words[i]) <= budget) {
          line1 += " " + words[i];
          i++;
        } else {
          break;
        }
      }
      const rest = words.slice(i).join(" ");
      if (!rest) return [trimToBudget(line1, budget)];
      return [trimToBudget(line1, budget), trimToBudget(rest, budget)];
    };

    const span = spanLimit(width, nodeCount);
    const lineBudget = vertical
      ? width - 56
      : Math.min(170, Math.max(96, span - 24));

    nodes.forEach((d) => {
      d.lines = buildLabelLines(d.name, lineBudget);
      d.labelWidth = Math.max(...d.lines.map(measure));
      d.boxWidth = d.labelWidth + 24;
      d.boxHeight = 26 + (d.lines.length - 1) * 14;
      d.collideR = vertical ? 34 : Math.max(30, d.boxWidth / 2 + 8);
    });

    // Seed positions in path order so the chain renders in reading
    // direction instead of untangling from a random cluster
    nodes.forEach((d, i) => {
      const t = nodeCount === 1 ? 0.5 : i / (nodeCount - 1);
      d.seedX = seedX(vertical, width, i, nodeCount);
      d.seedY = vertical
        ? 56 + t * (calculatedHeight - 56 - 56)
        : calculatedHeight / 2 +
          (i % 2 === 0 ? -1 : 1) * Math.min(32, calculatedHeight * 0.07);
      d.x = d.seedX;
      d.y = d.seedY;
    });

    // Add arrow marker for directed edges — color read from CSS token at render time
    const accentBlue =
      getComputedStyle(document.documentElement)
        .getPropertyValue("--accent-blue")
        .trim() || "#58A6FF";
    svg
      .append("defs")
      .append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 9)
      .attr("refY", 0)
      .attr("markerWidth", 11)
      .attr("markerHeight", 11)
      .attr("markerUnits", "userSpaceOnUse")
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", accentBlue);

    const g = svg.append("g");

    // Create links; the arrowhead marker is attached when each edge
    // finishes its draw-in (immediately under reduced motion)
    const link = g
      .append("g")
      .attr("class", "links")
      .selectAll("path.link")
      .data(links)
      .enter()
      .append("path")
      .attr("class", (d) => (d.isPath ? "link path" : "link"));

    // Tooltips interfere with touch gestures, so mouse only
    const isTouch = (event) =>
      event?.pointerType === "touch" || event?.type?.startsWith("touch");

    let isDragging = false;
    const node = g
      .append("g")
      .attr("class", "nodes")
      .selectAll("circle")
      .data(nodes)
      .enter()
      .append("circle")
      .attr("class", (d) =>
        ["node", d.isStart && "start", d.isEnd && "end"]
          .filter(Boolean)
          .join(" "),
      )
      .attr("r", NODE_R)
      // Ensure touch devices dedicate gestures to drag
      .style("touch-action", "none")
      .on("mouseover", (event, d) => {
        if (isTouch(event)) return;
        this.tooltip
          .style("opacity", 1)
          .text(`${d.name} — Step ${d.index + 1} of ${nodes.length}`)
          .style("left", event.pageX + 10 + "px")
          .style("top", event.pageY - 10 + "px");
      })
      .on("mouseout", (event) => {
        if (isTouch(event)) return;
        this.tooltip.style("opacity", 0);
      })
      .on("click", (event, d) => {
        if (!isDragging) openWikipediaPage(d.name);
      })
      .on("dblclick", (event, d) => {
        // Double-click to release node from fixed position
        d.fx = null;
        d.fy = null;
        simulation.alphaTarget(0.08).restart();
        setTimeout(() => simulation.alphaTarget(0), 150);
      })
      .call(
        d3
          .drag()
          .on("start", (event, d) => {
            // Prevent native scrolling/gestures only for touch
            const se = event.sourceEvent;
            if (se && (se.pointerType === "touch" || se.type === "touchstart")) {
              se.preventDefault();
              se.stopPropagation();
            }
            isDragging = true;
            if (!event.active) simulation.alphaTarget(0.05).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            const padding = 30;
            const se = event.sourceEvent;
            if (se && (se.pointerType === "touch" || se.type === "touchmove")) {
              se.preventDefault();
            }
            // Constrain drag within bounds
            d.fx = Math.max(padding, Math.min(graph.width - padding, event.x));
            d.fy = Math.max(padding, Math.min(graph.height - padding, event.y));
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            // Release node to let physics take over for tight binding
            d.fx = null;
            d.fy = null;
            // Gentle restart to pull nodes back together without jolts
            simulation.alphaTarget(0.02).restart();
            setTimeout(() => {
              simulation.alphaTarget(0);
              isDragging = false;
            }, 200);
          }),
      );

    // Label backgrounds (opaque boxes)
    const labelBg = g
      .append("g")
      .attr("class", "label-backgrounds")
      .selectAll("rect")
      .data(nodes)
      .enter()
      .append("rect")
      .attr("class", "node-label-bg")
      .attr("rx", 4)
      .attr("ry", 4)
      .attr("width", (d) => d.boxWidth)
      .attr("height", (d) => d.boxHeight);

    // Labels (up to two lines, full title preserved when it fits)
    const label = g
      .append("g")
      .attr("class", "labels")
      .selectAll("text")
      .data(nodes)
      .enter()
      .append("text")
      .attr("class", "node-label");

    label.each(function (d) {
      const text = d3.select(this);
      d.lines.forEach((line, li) => {
        text
          .append("tspan")
          .attr("x", 0)
          .attr("dy", li === 0 ? 0 : 14)
          .text(line);
      });
    });

    // Link distance: span the available axis evenly
    const maxBoxWidth = Math.max(...nodes.map((d) => d.boxWidth));
    const maxBoxHeight = Math.max(...nodes.map((d) => d.boxHeight));
    graph.dynamicDistance = vertical
      ? Math.max(80, maxBoxHeight + 52)
      : horizontalLinkDistance(maxBoxWidth, span);

    // Positional forces hold the path in reading order; collision keeps
    // labels clear; the chain settles quickly instead of drifting
    const simulation = d3
      .forceSimulation(nodes)
      .force(
        "link",
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance(() => graph.dynamicDistance)
          .strength(1),
      )
      .force(
        "charge",
        d3
          .forceManyBody()
          .strength(vertical ? -160 : -240)
          .distanceMax(Math.max(220, graph.dynamicDistance * 1.5)),
      )
      .force("x", d3.forceX((d) => d.seedX).strength(vertical ? 0.16 : 0.22))
      .force("y", d3.forceY((d) => d.seedY).strength(vertical ? 0.22 : 0.1))
      .force(
        "collision",
        d3
          .forceCollide()
          .radius((d) => d.collideR)
          .strength(0.8),
      )
      .alphaDecay(0.03)
      .velocityDecay(0.45);

    // Straight edge from circle boundary to circle boundary so the
    // arrowhead lands exactly on the target's edge
    const edgePath = (d) => {
      const dx = d.target.x - d.source.x;
      const dy = d.target.y - d.source.y;
      const dist = Math.hypot(dx, dy) || 1;
      const ux = dx / dist;
      const uy = dy / dist;
      // Stacked vertically, each label chip hangs below its node; start the
      // edge under the chip so the arrow never runs beneath the text.
      const sOff =
        vertical && uy > 0.5
          ? Math.min((18 + d.source.boxHeight + 6) / uy, dist - NODE_R - 16)
          : NODE_R + 3;
      const tOff = NODE_R + 4;
      return `M${d.source.x + ux * sOff},${d.source.y + uy * sOff} L${d.target.x - ux * tOff},${d.target.y - uy * tOff}`;
    };

    const updatePositions = () => {
      const padX = Math.min(
        graph.width / 2 - 4,
        Math.max(28, maxBoxWidth / 2 + 6),
      );
      const padTop = 28;
      const padBottom = 34 + maxBoxHeight + 8; // room for labels below nodes

      nodes.forEach((d) => {
        d.x = Math.max(padX, Math.min(graph.width - padX, d.x));
        d.y = Math.max(padTop, Math.min(graph.height - padBottom, d.y));
      });

      link.attr("d", edgePath);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      label.attr("transform", (d) => `translate(${d.x},${d.y + 34})`);
      labelBg
        .attr("x", (d) => d.x - d.boxWidth / 2)
        .attr("y", (d) => d.y + 18);
    };

    simulation.on("tick", updatePositions);

    // Settle the layout synchronously before first paint so the graph
    // appears in its final arrangement; physics stays live for dragging.
    // simulation.tick(n) advances state without firing tick events,
    // so positions are applied once manually afterwards.
    simulation.stop();
    simulation.tick(220);
    updatePositions();

    if (prefersReducedMotion) {
      link.attr("marker-end", "url(#arrowhead)");
    } else {
      // Reveal in path order over the settled layout: each node pops,
      // then its outgoing edge draws toward the next node
      node
        .attr("r", 0)
        .transition()
        .delay((d) => d.index * 60)
        .duration(220)
        .ease(d3.easeCubicOut)
        .attr("r", NODE_R);

      for (const selection of [label, labelBg]) {
        selection
          .style("opacity", 0)
          .transition()
          .delay((d) => d.index * 60 + 40)
          .duration(180)
          .style("opacity", 1);
      }

      link.each(function (d) {
        const len = this.getTotalLength();
        d3.select(this)
          .attr("stroke-dasharray", len)
          .attr("stroke-dashoffset", len)
          .transition()
          .delay(d.index * 60 + 100)
          .duration(180)
          .ease(d3.easeCubicOut)
          .attr("stroke-dashoffset", 0)
          .on("end", function () {
            d3.select(this)
              .attr("stroke-dasharray", null)
              .attr("stroke-dashoffset", null)
              .attr("marker-end", "url(#arrowhead)");
          });
      });
    }

    graph.simulation = simulation;
  }

  // Re-layout fully when the orientation flips (phone ↔ desktop),
  // otherwise just update svg size, seeds, and forces in place
  resizeGraph() {
    const graph = this.graph;
    if (!graph.simulation) return;

    const width = contentWidth($("graphContainer"));
    if (width < VERTICAL_BREAKPOINT !== graph.vertical) {
      this.rerenderFromState();
      return;
    }

    const nodes = graph.simulation.nodes();
    const nodeCount = nodes.length;
    graph.width = width;
    graph.height = graphHeight(graph.vertical, nodeCount);

    // Update svg size without wiping elements
    graph.svg
      .attr("width", graph.width)
      .style("height", graph.height + "px")
      .attr("viewBox", `0 0 ${graph.width} ${graph.height}`)
      .style("display", "block");

    nodes.forEach((d, i) => {
      d.seedX = seedX(graph.vertical, width, i, nodeCount);
    });
    if (!graph.vertical) {
      const maxBoxWidth = Math.max(...nodes.map((d) => d.boxWidth || 0), 0);
      graph.dynamicDistance = horizontalLinkDistance(
        maxBoxWidth,
        spanLimit(width, nodeCount),
      );
      // forceLink caches distances; re-setting the accessor recomputes them
      graph.simulation.force("link").distance(() => graph.dynamicDistance);
    }

    graph.simulation
      .force(
        "x",
        d3.forceX((d) => d.seedX).strength(graph.vertical ? 0.16 : 0.22),
      )
      .alphaTarget(0.05)
      .restart();

    // Settle gently
    setTimeout(() => graph.simulation.alphaTarget(0), 300);
  }

  displayPathList(path, result) {
    const explored =
      result.search_stats?.nodes_explored || result.nodes_explored || 0;
    const hops = path.length - 1;

    $("routeSummary").replaceChildren(
      ...[
        `${hops} ${hops === 1 ? "link" : "links"}`,
        `${path.length} pages`,
        `${explored.toLocaleString()} explored`,
        formatSeconds(result.search_time),
      ].map((text) => {
        const span = document.createElement("span");
        span.textContent = text;
        return span;
      }),
    );

    $("pathSteps").replaceChildren(
      ...path.map((page, index) => {
        const item = document.createElement("li");
        const step = document.createElement("a");
        step.className = "step";
        step.href = wikipediaUrl(page);
        step.target = "_blank";
        step.rel = "noopener";
        step.setAttribute(
          "aria-label",
          `Step ${index + 1}: ${page}, opens on Wikipedia`,
        );

        const mark = document.createElement("span");
        mark.className = "step-mark";

        const number = document.createElement("span");
        number.className = "step-index";
        number.textContent = String(index + 1).padStart(2, "0");

        const title = document.createElement("span");
        title.className = "step-title";
        title.textContent = page;

        const open = document.createElementNS(SVG_NS, "svg");
        open.setAttribute("class", "step-open");
        open.setAttribute("viewBox", "0 0 16 16");
        open.setAttribute("aria-hidden", "true");
        const arrow = document.createElementNS(SVG_NS, "path");
        arrow.setAttribute("d", "M5 11l6-6M6 5h5v5");
        open.appendChild(arrow);

        step.append(mark, number, title, open);
        item.appendChild(step);
        return item;
      }),
    );

    $("pathStepsContainer").classList.remove("hidden");
  }

  async cancelSearch() {
    const taskId = this.taskId;
    if (!taskId) return;

    // Update the UI synchronously, before any await
    this.fail({ code: "STOPPED" });

    // Best-effort backend cancel; its resolution must not touch the UI in
    // case the user has already started a new search.
    try {
      await fetch(`${API_BASE}/tasks/${taskId}`, { method: "DELETE" });
    } catch {
      /* task may already be done */
    }
  }

  clearVisualization() {
    this.clearActiveTask();
    this.hideLoading();
    this.hideNotice();
    StateManager.clear();

    $("pathStepsContainer").classList.add("hidden");
    $("visualizationSection").classList.remove("show");
    this.graph.svg.selectAll("*").remove();
    this.graph.simulation?.stop();
    this.graph.simulation = null;

    for (const id of ["startPage", "endPage"]) {
      $(id).value = "";
      $(id).removeAttribute("aria-invalid");
    }
    this.updateButtonState();
    $("startPage").focus();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  new PathFinderUI();
});
