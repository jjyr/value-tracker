const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2
});

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

function drawEquityChart(svg, range = "all") {
  const raw = svg.dataset.points || "[]";
  let points = [];
  try {
    points = JSON.parse(raw);
  } catch {
    points = [];
  }
  if (!points.length) return;

  const now = new Date(points[points.length - 1].date);
  const ranges = { "1m": 31, "3m": 93, ytd: 180, all: Infinity };
  const days = ranges[range] || Infinity;
  const visible = points.filter((point) => {
    if (!Number.isFinite(days)) return true;
    const date = new Date(point.date);
    return (now - date) / 86400000 <= days;
  });
  const chartPoints = visible.length > 1 ? visible : points.slice(-2);
  const series = [
    { key: "value", className: "line-portfolio" },
    { key: "spy_value", className: "line-spy" },
    { key: "qqq_value", className: "line-qqq" }
  ];
  const values = chartPoints.flatMap((point) => series.map((item) => Number(point[item.key])));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.14, 1);
  const low = min - pad;
  const high = max + pad;
  const width = 720;
  const height = 260;
  const left = 28;
  const right = 18;
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
    const line = document.createElementNS(namespace, "line");
    const yy = top + (innerHeight * tick) / 3;
    line.setAttribute("x1", left);
    line.setAttribute("x2", width - right);
    line.setAttribute("y1", yy);
    line.setAttribute("y2", yy);
    line.setAttribute("class", "chart-grid-line");
    svg.appendChild(line);
  });

  series.forEach((item) => {
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
}

function setupChartControls() {
  const chart = document.querySelector(".equity-chart");
  if (!chart) return;
  drawEquityChart(chart);
  document.querySelectorAll(".range-button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".range-button").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      drawEquityChart(chart, button.dataset.range);
    });
  });
}

function setupRankingToggles() {
  document.querySelectorAll(".ranking-panel").forEach((panel) => {
    const button = panel.querySelector(".ranking-toggle");
    const extraRows = panel.querySelectorAll(".extra-row");
    if (!button || !extraRows.length) {
      if (button) button.hidden = true;
      return;
    }
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      button.querySelector("span").textContent = expanded ? "展开" : "收起";
      extraRows.forEach((row) => row.classList.toggle("is-hidden", expanded));
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

document.addEventListener("DOMContentLoaded", () => {
  formatMoneyElements();
  setupChartControls();
  setupRankingToggles();
  setupRebalancePagination();
});
