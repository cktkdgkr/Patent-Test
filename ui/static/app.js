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

let lastResponse = null;
let selectedIndex = 0;

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
  });
});

runExample.addEventListener("click", async () => {
  await runScreening("/api/example", {});
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
    renderReport(data.report);
    downloadJson.disabled = false;
    const failed = data.report.failed_candidates?.length || 0;
    setStatus(failed ? `완료 · 실패 ${failed}건` : "완료", failed ? "error" : "ready");
  } catch (error) {
    renderError(error.message);
    setStatus(error.message, "error");
  } finally {
    setControlsDisabled(false);
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
  return `
    <div class="option-item">
      <div class="option-type">Claim ${escapeHtml(String(comparison.claim_id))} · ${escapeHtml(comparison.seq_id || "")}</div>
      <div class="sequence-metrics">
        <span>Identity <strong>${identity}</strong></span>
        <span>Coverage <strong>${coverage}</strong></span>
        <span>Threshold <strong>${threshold}</strong></span>
      </div>
      <div class="option-text">${escapeHtml(comparison.reasoning || comparison.status || "")}</div>
      <div class="cell-sub">${changes.length ? escapeHtml(changes.join(", ")) : escapeHtml(comparison.status || "")}</div>
    </div>
  `;
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
  form.querySelectorAll("button, input").forEach((node) => {
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
