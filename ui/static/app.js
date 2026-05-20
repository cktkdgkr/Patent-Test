const form = document.getElementById("screenForm");
const fileInput = document.getElementById("candidateFile");
const fastaFileInput = document.getElementById("fastaFile");
const enableWebFetch = document.getElementById("enableWebFetch");
const runExample = document.getElementById("runExample");
const downloadJson = document.getElementById("downloadJson");
const statusText = document.getElementById("statusText");
const statusDot = document.querySelector(".dot");
const metricRow = document.getElementById("metricRow");
const resultRows = document.getElementById("resultRows");
const detailPane = document.getElementById("detailPane");
const userIdInput = document.getElementById("userId");
const loadedBanner = document.getElementById("loadedBanner");
const historyRows = document.getElementById("historyRows");
const historySummary = document.getElementById("historySummary");
const filterUserId = document.getElementById("filterUserId");
const filterProductId = document.getElementById("filterProductId");
const filterGrade = document.getElementById("filterGrade");
const filterFromDate = document.getElementById("filterFromDate");
const filterToDate = document.getElementById("filterToDate");
const tabButtons = document.querySelectorAll(".tab");
const tabPanels = document.querySelectorAll(".tab-panel");

const USER_ID_STORAGE_KEY = "patentScreening.userId";

let lastResponse = null;
let selectedIndex = 0;

initUserId();
initTabs();
initHistoryControls();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) {
    setStatus("CSV/XLSX 파일을 선택하세요", "error");
    return;
  }
  await runScreening("/api/screen", {
    product: await productPayload(new FormData(form)),
    file: {
      name: file.name,
      content_base64: await fileToBase64(file),
    },
    enable_web_fetch: enableWebFetch.checked,
    user_id: currentUserId(),
  });
});

runExample.addEventListener("click", async () => {
  await runScreening("/api/example", { user_id: currentUserId() });
});

downloadJson.addEventListener("click", () => {
  if (!lastResponse) {
    return;
  }
  const blob = new Blob([JSON.stringify(lastResponse, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `patent_screening_${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
});

async function runScreening(endpoint, payload) {
  setStatus("분석 중", "busy");
  setControlsDisabled(true);
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "분석 실패");
    }
    lastResponse = data;
    selectedIndex = 0;
    showLoadedBanner(null);
    renderReport(data.report);
    downloadJson.disabled = false;
    const failed = data.report.failed_candidates?.length || 0;
    setStatus(failed ? `완료 · 실패 ${failed}건` : "완료", failed ? "error" : "ready");
    switchTab("current");
  } catch (error) {
    renderError(error.message);
    setStatus(error.message, "error");
  } finally {
    setControlsDisabled(false);
  }
}

function initUserId() {
  if (!userIdInput) return;
  const stored = window.localStorage.getItem(USER_ID_STORAGE_KEY) || "";
  if (stored) userIdInput.value = stored;
  userIdInput.addEventListener("change", () => {
    const value = userIdInput.value.trim();
    if (value) window.localStorage.setItem(USER_ID_STORAGE_KEY, value);
    else window.localStorage.removeItem(USER_ID_STORAGE_KEY);
  });
}

function currentUserId() {
  const value = (userIdInput && userIdInput.value || "").trim();
  return value || null;
}

function initTabs() {
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(name) {
  tabButtons.forEach((btn) => {
    const active = btn.dataset.tab === name;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  tabPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.panel !== name);
  });
  if (name === "history") {
    fetchHistory();
  }
}

function initHistoryControls() {
  document.getElementById("applyFilter")?.addEventListener("click", () => fetchHistory());
  document.getElementById("refreshHistory")?.addEventListener("click", () => fetchHistory());
  document.getElementById("clearFilter")?.addEventListener("click", () => {
    filterUserId.value = "";
    filterProductId.value = "";
    filterGrade.value = "";
    filterFromDate.value = "";
    filterToDate.value = "";
    fetchHistory();
  });
  [filterUserId, filterProductId, filterFromDate, filterToDate].forEach((el) => {
    if (!el) return;
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        fetchHistory();
      }
    });
  });
}

async function fetchHistory() {
  if (!historyRows) return;
  historyRows.innerHTML = '<tr class="empty-row"><td colspan="7">불러오는 중...</td></tr>';
  historySummary.textContent = "불러오는 중...";
  const params = new URLSearchParams();
  if (filterUserId.value.trim()) params.set("user_id", filterUserId.value.trim());
  if (filterProductId.value.trim()) params.set("product_id", filterProductId.value.trim());
  if (filterGrade.value) params.set("grade", filterGrade.value);
  if (filterFromDate.value) params.set("from_date", filterFromDate.value);
  if (filterToDate.value) params.set("to_date", filterToDate.value);
  try {
    const response = await fetch(`/api/reports?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "목록 조회 실패");
    renderHistory(data);
  } catch (error) {
    historyRows.innerHTML = `<tr class="empty-row"><td colspan="7">오류: ${escapeHtml(error.message)}</td></tr>`;
    historySummary.textContent = `오류: ${error.message}`;
  }
}

function renderHistory(data) {
  const reports = data.reports || [];
  if (!reports.length) {
    historyRows.innerHTML = '<tr class="empty-row"><td colspan="7">일치하는 리포트가 없습니다.</td></tr>';
    historySummary.textContent = `총 ${data.total || 0}건`;
    return;
  }
  historyRows.innerHTML = reports
    .map((item) => {
      const date = formatTimestamp(item.generated_at);
      const grade = item.top_grade || "";
      const screened = item.screened_count ?? "-";
      const failed = item.failed_count || 0;
      const patentsCell = failed > 0 ? `${screened} <span class="cell-sub">(실패 ${failed})</span>` : String(screened);
      return `
        <tr data-run="${escapeHtml(item.run_id)}">
          <td>${escapeHtml(date)}</td>
          <td>${escapeHtml(item.user_id || "-")}</td>
          <td>${escapeHtml(item.product_id || "-")}</td>
          <td>${patentsCell}</td>
          <td>${grade ? `<span class="grade ${escapeHtml(grade)}">${escapeHtml(grade)}</span>` : "-"}</td>
          <td class="run-id">${escapeHtml(item.run_id)}</td>
          <td><button type="button" class="link-btn" data-action="load">보기</button> <a class="link-btn" href="/api/reports/${encodeURIComponent(item.run_id)}" download="${escapeHtml(item.run_id)}.json">JSON</a></td>
        </tr>
      `;
    })
    .join("");
  historySummary.textContent = data.truncated
    ? `${reports.length}건 표시 · 전체 ${data.total}건 (최대 ${data.limit}건까지 표시)`
    : `총 ${data.total}건`;

  historyRows.querySelectorAll('button[data-action="load"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      if (!row) return;
      loadReport(row.dataset.run);
    });
  });
}

async function loadReport(runId) {
  if (!runId) return;
  setStatus("리포트 불러오는 중", "busy");
  try {
    const response = await fetch(`/api/reports/${encodeURIComponent(runId)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "리포트 로드 실패");
    lastResponse = { report: data.report, run_id: data.run_id, user_id: data.user_id, generated_at: data.generated_at };
    selectedIndex = 0;
    renderReport(data.report);
    downloadJson.disabled = false;
    showLoadedBanner(data);
    setStatus(`히스토리 리포트 로드: ${data.run_id}`, "ready");
    switchTab("current");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function showLoadedBanner(data) {
  if (!loadedBanner) return;
  if (!data) {
    loadedBanner.classList.add("hidden");
    loadedBanner.textContent = "";
    return;
  }
  const date = formatTimestamp(data.generated_at);
  loadedBanner.classList.remove("hidden");
  loadedBanner.innerHTML = `히스토리 로드: <strong>${escapeHtml(data.run_id)}</strong> · ${escapeHtml(data.product_id || "-")} · ${escapeHtml(date)} · User <strong>${escapeHtml(data.user_id || "-")}</strong>`;
}

function formatTimestamp(value) {
  if (!value) return "-";
  try {
    const date = new Date(value);
    if (isNaN(date.getTime())) return value;
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  } catch {
    return value;
  }
}

async function productPayload(formData) {
  const fastaFile = fastaFileInput.files[0];
  const fastaFromFile = fastaFile ? await fastaFile.text() : "";
  const fastaText = [stringValue(formData, "fasta_text"), fastaFromFile].filter(Boolean).join("\n");
  return {
    product_id: stringValue(formData, "product_id"),
    enzyme_name: stringValue(formData, "enzyme_name"),
    amino_acid_sequence: stringValue(formData, "amino_acid_sequence"),
    fasta_text: fastaText || null,
    reference_sequence_id: stringValue(formData, "reference_sequence_id"),
    alignment_backend: stringValue(formData, "alignment_backend") || "auto",
    identity: numberValue(formData, "identity"),
    ph: numberValue(formData, "ph"),
    temperature_c: numberValue(formData, "temperature_c"),
    substrate: stringValue(formData, "substrate"),
    enzyme_class: stringValue(formData, "enzyme_class"),
    variant: stringValue(formData, "variant"),
    activity: stringValue(formData, "activity"),
    organism: stringValue(formData, "organism"),
    use_case: stringValue(formData, "use_case"),
    jurisdiction: stringValue(formData, "jurisdiction"),
    launch_date: stringValue(formData, "launch_date"),
  };
}

function stringValue(formData, name) {
  const value = String(formData.get(name) || "").trim();
  return value || null;
}

function numberValue(formData, name) {
  const value = String(formData.get(name) || "").trim();
  return value === "" ? null : Number(value);
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("파일을 읽을 수 없습니다"));
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      const chunkSize = 0x8000;
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
      }
      resolve(btoa(binary));
    };
    reader.readAsArrayBuffer(file);
  });
}

function renderReport(report) {
  renderMetrics(report);
  renderRows(report.reports || []);
  renderDetail(report);
}

function renderMetrics(report) {
  const summary = report.summary_by_grade || {};
  const values = [
    report.total_candidates || 0,
    report.screened_count || 0,
    (report.failed_candidates || []).length,
    summary.HIGH || 0,
    summary.MEDIUM || 0,
    summary.LOW || 0,
    summary.SAFE || 0,
  ];
  metricRow.querySelectorAll("strong").forEach((node, index) => {
    node.textContent = values[index];
  });
}

function renderRows(reports) {
  if (!reports.length) {
    resultRows.innerHTML = '<tr class="empty-row"><td colspan="6">결과 없음</td></tr>';
    return;
  }
  resultRows.innerHTML = reports
    .map((item, index) => {
      const selected = index === selectedIndex ? "selected" : "";
      return `
        <tr class="${selected}" data-index="${index}">
          <td><span class="grade ${escapeHtml(item.overall_grade)}">${escapeHtml(item.overall_grade)}</span></td>
          <td>
            <div class="cell-main">${escapeHtml(item.patent_id)}</div>
            <div class="cell-sub">${escapeHtml(item.source || "")}</div>
          </td>
          <td>${item.claim_results?.length || 0}</td>
          <td>${item.design_around_options?.length || 0}</td>
          <td>${percent(item.overall_confidence)}</td>
          <td><div class="reasoning">${escapeHtml(item.overall_reasoning || "")}</div></td>
        </tr>
      `;
    })
    .join("");
  resultRows.querySelectorAll("tr[data-index]").forEach((row) => {
    row.addEventListener("click", () => {
      selectedIndex = Number(row.dataset.index);
      renderRows(reports);
      renderDetail(lastResponse.report);
    });
  });
}

function renderDetail(report) {
  const reports = report.reports || [];
  const item = reports[selectedIndex];
  if (!item) {
    const failures = report.failed_candidates || [];
    detailPane.innerHTML = failures.length ? renderFailures(failures) : '<div class="detail-empty">특허 행을 선택하면 claim과 회피 후보가 표시됩니다.</div>';
    return;
  }
  const sortedClaims = [...(item.claim_results || [])].sort((a, b) => {
    return gradeRank(b.grade) - gradeRank(a.grade) || (b.confidence || 0) - (a.confidence || 0);
  });
  const claims = sortedClaims.slice(0, 8).map(renderClaim).join("");
  const options = (item.design_around_options || []).slice(0, 8).map(renderOption).join("");
  const sequences = sortedClaims
    .flatMap((claim) => (claim.sequence_comparisons || []).map((comparison) => ({ claim_id: claim.claim_id, ...comparison })))
    .slice(0, 8)
    .map(renderSequenceComparison)
    .join("");
  detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <h3>${escapeHtml(item.patent_id)}</h3>
        <p>${escapeHtml(item.overall_reasoning || "")}</p>
      </div>
      <span class="grade ${escapeHtml(item.overall_grade)}">${escapeHtml(item.overall_grade)}</span>
    </div>
    <div class="detail-grid">
      <div class="detail-section">
        <h4>주요 claim</h4>
        <div class="claim-list">${claims || '<div class="claim-item">claim 없음</div>'}</div>
      </div>
      <div class="detail-section">
        <h4>서열 비교</h4>
        <div class="sequence-list">${sequences || '<div class="option-item">비교 가능한 SEQ ID 없음</div>'}</div>
      </div>
      <div class="detail-section wide-detail">
        <h4>회피 후보</h4>
        <div class="option-list">${options || '<div class="option-item">회피 후보 없음</div>'}</div>
      </div>
    </div>
    ${renderFailures(report.failed_candidates || [])}
  `;
}

function renderSequenceComparison(comparison) {
  const identity = typeof comparison.identity === "number" ? `${comparison.identity}%` : "-";
  const coverage = typeof comparison.coverage === "number" ? `${comparison.coverage}%` : "-";
  const threshold = typeof comparison.threshold === "number" ? `${comparison.threshold}%` : "-";
  const changes = [
    ...(comparison.substitutions || []),
    ...(comparison.deletions || []),
    ...(comparison.insertions || []),
  ].slice(0, 8);
  const mappings = (comparison.residue_position_mappings || []).slice(0, 5).map(renderResidueMapping).join("<br>");
  return `
    <div class="option-item">
      <div class="option-type">Claim ${escapeHtml(String(comparison.claim_id))} · ${escapeHtml(comparison.seq_id || "")}</div>
      <div class="sequence-metrics">
        <span>Identity <strong>${identity}</strong></span>
        <span>Coverage <strong>${coverage}</strong></span>
        <span>Threshold <strong>${threshold}</strong></span>
      </div>
      <div class="option-text">${escapeHtml(comparison.reasoning || comparison.status || "")}</div>
      <div class="cell-sub">${escapeHtml(`${comparison.alignment_backend || "alignment"} · ${comparison.alignment_scope || ""}`)}</div>
      <div class="cell-sub">${changes.length ? escapeHtml(changes.join(", ")) : escapeHtml(comparison.status || "")}</div>
      ${mappings ? `<div class="cell-sub">${mappings}</div>` : ""}
    </div>
  `;
}

function renderResidueMapping(mapping) {
  const product = mapping.product_position ? `Product ${mapping.product_position}${mapping.product_residue || ""}` : mapping.status;
  const claim = mapping.claimed_residue ? `claimed ${mapping.claimed_residue}` : mapping.raw_claim || "";
  const match = mapping.claim_match === true ? "match" : mapping.claim_match === false ? "mismatch" : mapping.confidence;
  return escapeHtml(`Patent ${mapping.reference_position}${mapping.reference_residue || ""} -> ${product} (${claim}, ${match})`);
}

function renderClaim(claim) {
  return `
    <div class="claim-item">
      <div class="claim-meta">
        <strong>Claim ${claim.claim_id}</strong>
        <span class="grade ${escapeHtml(claim.grade)}">${escapeHtml(claim.grade)}</span>
      </div>
      <div class="claim-text">${escapeHtml(claim.claim_text || "")}</div>
      <div class="cell-sub">${escapeHtml(claim.reasoning || "")}</div>
    </div>
  `;
}

function renderOption(option) {
  return `
    <div class="option-item">
      <div class="option-type">${escapeHtml(option.strategy_type || "")} · Claim ${escapeHtml(String(option.claim_id || ""))}</div>
      <div class="option-text">${escapeHtml(option.proposed_direction || "")}</div>
      <div class="cell-sub">${escapeHtml(option.basis || "")}</div>
    </div>
  `;
}

function renderFailures(failures) {
  if (!failures.length) {
    return "";
  }
  return `
    <div class="failure-list">
      ${failures
        .map((failure) => `<div class="failure-item"><strong>${escapeHtml(failure.candidate_id)}</strong><br>${escapeHtml(failure.reason)}</div>`)
        .join("")}
    </div>
  `;
}

function renderError(message) {
  detailPane.innerHTML = `<div class="failure-item"><strong>오류</strong><br>${escapeHtml(message)}</div>`;
}

function setStatus(message, mode) {
  statusText.textContent = message;
  statusDot.className = `dot ${mode}`;
}

function setControlsDisabled(disabled) {
  form.querySelectorAll("button, input, select, textarea").forEach((node) => {
    node.disabled = disabled;
  });
  runExample.disabled = disabled;
  downloadJson.disabled = disabled || !lastResponse;
}

function percent(value) {
  if (typeof value !== "number") {
    return "";
  }
  return `${Math.round(value * 100)}%`;
}

function gradeRank(grade) {
  return { SAFE: 0, LOW: 1, MEDIUM: 2, HIGH: 3 }[grade] || 0;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
