const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2
});

const percentFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
  minimumFractionDigits: 1,
  signDisplay: "always"
});

const chartSeries = [
  { key: "value", label: "StockHunt", className: "line-portfolio", pointClass: "point-portfolio" },
  { key: "spy_value", label: "SPY", className: "line-spy", pointClass: "point-spy" },
  { key: "qqq_value", label: "QQQ", className: "line-qqq", pointClass: "point-qqq" }
];

function formatMoneyElements() {
  document.querySelectorAll("[data-money]").forEach((element) => {
    const value = Number(element.dataset.money);
    if (!Number.isFinite(value)) {
      element.textContent = "--";
      return;
    }
    element.textContent = moneyFormatter.format(value);
  });
}

function formatPercent(value) {
  return `${percentFormatter.format(value)}%`;
}

function chartTooltipElement() {
  let tooltip = document.querySelector(".chart-hover-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-hover-tooltip";
    document.body.appendChild(tooltip);
  }
  return tooltip;
}

function activeChartSeries(svg) {
  const activeKeys = (svg.dataset.activeSeries || chartSeries.map((item) => item.key).join(",")).split(",");
  const active = chartSeries.filter((item) => activeKeys.includes(item.key));
  return active.length ? active : chartSeries;
}

function setActiveChartSeries(svg, keys) {
  svg.dataset.activeSeries = keys.join(",");
}

function drawEquityChart(svg) {
  const raw = svg.dataset.points || "[]";
  let points = [];
  try {
    points = JSON.parse(raw);
  } catch {
    points = [];
  }
  if (!points.length) return;

  const range = svg.dataset.range || "all";
  const activeSeries = activeChartSeries(svg);
  const now = new Date(points[points.length - 1].date);
  const ranges = { "1m": 31, "3m": 93, ytd: 180, all: Infinity };
  const days = ranges[range] || Infinity;
  const visible = points.filter((point) => {
    if (!Number.isFinite(days)) return true;
    const date = new Date(point.date);
    return (now - date) / 86400000 <= days;
  });
  const chartPoints = visible.length > 1 ? visible : points.slice(-2);
  const values = chartPoints.flatMap((point) => activeSeries.map((item) => Number(point[item.key]))).filter(Number.isFinite);
  if (!values.length) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.14, 1);
  const low = min - pad;
  const high = max + pad;
  const baseline = Number(points[0].value) || 100000;
  const width = 760;
  const height = 260;
  const left = 58;
  const right = 22;
  const top = 18;
  const bottom = 34;
  const innerWidth = width - left - right;
  const innerHeight = height - top - bottom;

  const x = (index) => left + (innerWidth * index) / Math.max(chartPoints.length - 1, 1);
  const y = (value) => top + innerHeight - ((value - low) / (high - low)) * innerHeight;
  const pathFor = (key) =>
    chartPoints
      .map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(Number(point[key])).toFixed(1)}`)
      .join(" ");

  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";
  const namespace = "http://www.w3.org/2000/svg";

  [0, 1, 2, 3].forEach((tick) => {
    const tickValue = high - ((high - low) * tick) / 3;
    const line = document.createElementNS(namespace, "line");
    const yy = top + (innerHeight * tick) / 3;
    line.setAttribute("x1", left);
    line.setAttribute("x2", width - right);
    line.setAttribute("y1", yy);
    line.setAttribute("y2", yy);
    line.setAttribute("class", "chart-grid-line");
    svg.appendChild(line);

    const label = document.createElementNS(namespace, "text");
    label.setAttribute("x", left - 10);
    label.setAttribute("y", yy + 4);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("class", "chart-axis-label");
    label.textContent = formatPercent((tickValue / baseline - 1) * 100);
    svg.appendChild(label);
  });

  activeSeries.forEach((item) => {
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", pathFor(item.key));
    path.setAttribute("class", `chart-line ${item.className}`);
    svg.appendChild(path);
  });

  const firstLabel = document.createElementNS(namespace, "text");
  firstLabel.setAttribute("x", left);
  firstLabel.setAttribute("y", height - 8);
  firstLabel.setAttribute("class", "chart-label");
  firstLabel.textContent = chartPoints[0].date.slice(5);
  svg.appendChild(firstLabel);

  const lastLabel = document.createElementNS(namespace, "text");
  lastLabel.setAttribute("x", width - right);
  lastLabel.setAttribute("y", height - 8);
  lastLabel.setAttribute("text-anchor", "end");
  lastLabel.setAttribute("class", "chart-label");
  lastLabel.textContent = chartPoints[chartPoints.length - 1].date.slice(5);
  svg.appendChild(lastLabel);

  const hoverGroup = document.createElementNS(namespace, "g");
  hoverGroup.setAttribute("class", "chart-hover-layer");
  hoverGroup.setAttribute("visibility", "hidden");
  const hoverLine = document.createElementNS(namespace, "line");
  hoverLine.setAttribute("class", "chart-hover-line");
  hoverLine.setAttribute("y1", top);
  hoverLine.setAttribute("y2", height - bottom);
  hoverGroup.appendChild(hoverLine);
  const hoverDots = activeSeries.map((item) => {
    const circle = document.createElementNS(namespace, "circle");
    circle.setAttribute("r", "4.2");
    circle.setAttribute("class", `chart-point ${item.pointClass}`);
    hoverGroup.appendChild(circle);
    return { item, circle };
  });
  svg.appendChild(hoverGroup);

  const overlay = document.createElementNS(namespace, "rect");
  overlay.setAttribute("x", left);
  overlay.setAttribute("y", top);
  overlay.setAttribute("width", innerWidth);
  overlay.setAttribute("height", innerHeight);
  overlay.setAttribute("class", "chart-hover-capture");
  svg.appendChild(overlay);

  const tooltip = chartTooltipElement();
  const renderHover = (event) => {
    const rect = svg.getBoundingClientRect();
    const scaledX = ((event.clientX - rect.left) * width) / rect.width;
    const ratio = Math.max(0, Math.min(1, (scaledX - left) / innerWidth));
    const index = Math.max(0, Math.min(chartPoints.length - 1, Math.round(ratio * (chartPoints.length - 1))));
    const point = chartPoints[index];
    const xx = x(index);

    hoverLine.setAttribute("x1", xx);
    hoverLine.setAttribute("x2", xx);
    hoverDots.forEach(({ item, circle }) => {
      const value = Number(point[item.key]);
      circle.setAttribute("cx", xx);
      circle.setAttribute("cy", y(value));
    });
    hoverGroup.setAttribute("visibility", "visible");

    const rows = activeSeries
      .map((item) => {
        const value = Number(point[item.key]);
        const pct = (value / baseline - 1) * 100;
        return `<div><span class="chart-tooltip-name ${item.pointClass}">${item.label}</span><strong>${formatPercent(pct)}</strong><span>${moneyFormatter.format(value)}</span></div>`;
      })
      .join("");
    tooltip.innerHTML = `<time>${point.date}</time>${rows}`;
    tooltip.classList.add("is-visible");

    const tooltipRect = tooltip.getBoundingClientRect();
    const margin = 12;
    let leftPx = event.clientX + 14;
    if (leftPx + tooltipRect.width > window.innerWidth - margin) {
      leftPx = event.clientX - tooltipRect.width - 14;
    }
    let topPx = event.clientY - tooltipRect.height / 2;
    topPx = Math.max(margin, Math.min(topPx, window.innerHeight - tooltipRect.height - margin));
    tooltip.style.left = `${Math.round(leftPx)}px`;
    tooltip.style.top = `${Math.round(topPx)}px`;
  };

  overlay.addEventListener("pointermove", renderHover);
  overlay.addEventListener("pointerleave", () => {
    hoverGroup.setAttribute("visibility", "hidden");
    tooltip.classList.remove("is-visible");
  });
}

function setupChartControls() {
  const chart = document.querySelector(".equity-chart");
  if (!chart) return;
  chart.dataset.range = chart.dataset.range || "all";
  setActiveChartSeries(chart, chartSeries.map((item) => item.key));
  drawEquityChart(chart);
  document.querySelectorAll(".chart-panel .range-button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".chart-panel .range-button").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      chart.dataset.range = button.dataset.range;
      drawEquityChart(chart);
    });
  });
  document.querySelectorAll(".legend-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const current = new Set((chart.dataset.activeSeries || "").split(",").filter(Boolean));
      const key = button.dataset.series;
      if (current.has(key) && current.size > 1) {
        current.delete(key);
      } else {
        current.add(key);
      }
      setActiveChartSeries(chart, Array.from(current));
      document.querySelectorAll(".legend-toggle").forEach((item) => {
        const active = current.has(item.dataset.series);
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", String(active));
      });
      drawEquityChart(chart);
    });
  });
}

function activeRankingBody(panel) {
  return panel.querySelector("tbody[data-ranking-scope]:not([hidden])") || panel.querySelector("tbody");
}

function renderRankingExpansion(panel, expanded) {
  const button = panel.querySelector(".ranking-toggle");
  const body = activeRankingBody(panel);
  if (!button || !body) return;
  const extraRows = body.querySelectorAll(".extra-row");
  button.hidden = !extraRows.length;
  button.setAttribute("aria-expanded", String(expanded));
  button.querySelector("span").textContent = expanded ? "收起" : "展开";
  extraRows.forEach((row) => row.classList.toggle("is-hidden", !expanded));
}

function setupRankingFilters() {
  document.querySelectorAll(".ranking-panel").forEach((panel) => {
    panel.querySelectorAll("[data-ranking-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        const scope = button.dataset.rankingFilter;
        panel.querySelectorAll("[data-ranking-filter]").forEach((item) => {
          const active = item.dataset.rankingFilter === scope;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        panel.querySelectorAll("[data-ranking-scope]").forEach((body) => {
          body.hidden = body.dataset.rankingScope !== scope;
        });
        renderRankingExpansion(panel, false);
        formatMoneyElements();
      });
    });
    renderRankingExpansion(panel, false);
  });
}

function setupRankingToggles() {
  document.querySelectorAll(".ranking-panel").forEach((panel) => {
    const button = panel.querySelector(".ranking-toggle");
    if (!button) return;
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      renderRankingExpansion(panel, !expanded);
    });
  });
}

function setupRebalancePagination() {
  const list = document.querySelector("[data-rebalance-list]");
  if (!list) return;
  const items = Array.from(list.querySelectorAll("[data-rebalance-item]"));
  const pageSize = Math.max(Number(list.dataset.pageSize) || 5, 1);
  const totalPages = Math.max(Math.ceil(items.length / pageSize), 1);
  const prev = document.querySelector("[data-rebalance-prev]");
  const next = document.querySelector("[data-rebalance-next]");
  const indicator = document.querySelector("[data-rebalance-page]");
  let page = 0;

  const render = () => {
    items.forEach((item, index) => {
      const visible = index >= page * pageSize && index < (page + 1) * pageSize;
      item.classList.toggle("is-hidden", !visible);
    });
    if (indicator) indicator.textContent = `${page + 1} / ${totalPages}`;
    if (prev) prev.disabled = page === 0;
    if (next) next.disabled = page >= totalPages - 1;
  };

  if (prev) {
    prev.addEventListener("click", () => {
      page = Math.max(page - 1, 0);
      render();
    });
  }
  if (next) {
    next.addEventListener("click", () => {
      page = Math.min(page + 1, totalPages - 1);
      render();
    });
  }
  render();
}

function setupTooltips() {
  const triggers = Array.from(document.querySelectorAll("[data-tooltip]"));
  if (!triggers.length) return;

  const tooltip = document.createElement("div");
  tooltip.className = "floating-tooltip";
  tooltip.setAttribute("role", "tooltip");
  document.body.appendChild(tooltip);

  let activeTrigger = null;

  const positionTooltip = () => {
    if (!activeTrigger) return;
    const rect = activeTrigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const gap = 10;
    const margin = 12;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = rect.left + rect.width / 2 - tooltipRect.width / 2;
    left = Math.max(margin, Math.min(left, viewportWidth - tooltipRect.width - margin));

    let placement = "top";
    let top = rect.top - tooltipRect.height - gap;
    if (top < margin) {
      placement = "bottom";
      top = rect.bottom + gap;
    }
    top = Math.max(margin, Math.min(top, viewportHeight - tooltipRect.height - margin));

    const arrowLeft = rect.left + rect.width / 2 - left;
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
    tooltip.style.setProperty("--arrow-left", `${Math.round(arrowLeft)}px`);
    tooltip.dataset.placement = placement;
  };

  const showTooltip = (trigger) => {
    const text = trigger.dataset.tooltip;
    if (!text) return;
    activeTrigger = trigger;
    tooltip.textContent = text;
    tooltip.classList.add("is-visible");
    requestAnimationFrame(positionTooltip);
  };

  const hideTooltip = () => {
    activeTrigger = null;
    tooltip.classList.remove("is-visible");
  };

  triggers.forEach((trigger) => {
    trigger.addEventListener("mouseenter", () => showTooltip(trigger));
    trigger.addEventListener("focus", () => showTooltip(trigger));
    trigger.addEventListener("mouseleave", hideTooltip);
    trigger.addEventListener("blur", hideTooltip);
  });

  window.addEventListener("scroll", positionTooltip, true);
  window.addEventListener("resize", positionTooltip);
}

document.addEventListener("DOMContentLoaded", () => {
  formatMoneyElements();
  setupChartControls();
  setupRankingFilters();
  setupRankingToggles();
  setupRebalancePagination();
  setupTooltips();
});
