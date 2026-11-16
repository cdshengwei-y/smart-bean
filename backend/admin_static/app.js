(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = {
    dashboard: null,
    view: "overview",
    deviceQuery: "",
    accessQuery: "",
    accessStatus: "all",
    transcriptionRuns: [],
  };

  const viewMeta = {
    overview: ["运营总览", "快速了解账户、设备和转写任务的当前状态。"],
    devices: ["设备与用户", "查看设备归属、同步端连接和转写任务健康度。"],
    access: ["授权管理", "签发激活码，管理同步令牌和设备授权。"],
    models: ["模型回放", "查看历史转写模型，并按当时模型重新运行。"],
  };

  const accessStatusLabels = {
    active: "有效",
    redeemed: "已使用",
    revoked: "已撤销",
    expired: "已过期",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatTime(value, emptyLabel = "暂无") {
    if (!value) return emptyLabel;
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : date.toLocaleString("zh-CN", { hour12: false });
  }

  function displayDeviceId(value) {
    return value === "legacy" ? "历史录音" : value;
  }

  function setMessage(message, isError = false) {
    const element = $("#page-message");
    element.textContent = message || "";
    element.classList.toggle("error", isError);
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) showLogin();
      throw new Error(payload.error || `请求失败（${response.status}）`);
    }
    return payload;
  }

  function showLogin(message = "") {
    $("#login-view").classList.remove("hidden");
    $("#admin-view").classList.add("hidden");
    $("#login-error").textContent = message;
    $("#admin-password").focus();
  }

  function showAdmin() {
    $("#login-view").classList.add("hidden");
    $("#admin-view").classList.remove("hidden");
  }

  function setView(view) {
    state.view = view;
    const [title, subtitle] = viewMeta[view];
    $("#view-title").textContent = title;
    $("#view-subtitle").textContent = subtitle;
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === view);
    });
    document.querySelectorAll(".view-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.id !== `${view}-view`);
    });
  }

  function accessStatusTag(status) {
    return `<span class="status status-${escapeHtml(status)}">${escapeHtml(accessStatusLabels[status] || status)}</span>`;
  }

  function jobState(status) {
    if (status === "completed") return { label: "已完成", className: "completed" };
    if (status === "missing") return { label: "源文件缺失", className: "missing" };
    if (String(status).includes("failed")) return { label: "处理异常", className: "failed" };
    return { label: "处理中", className: "running" };
  }

  function transcriptionRunState(status) {
    if (status === "completed") return { label: "已完成", className: "completed" };
    if (status === "queued") return { label: "等待回放", className: "running" };
    if (status === "running") return { label: "回放中", className: "running" };
    if (status === "failed") return { label: "回放失败", className: "failed" };
    return { label: "历史未知", className: "neutral" };
  }

  function transcriptionRunKind(kind) {
    if (kind === "initial") return "首次转写";
    if (kind === "replay") return "历史回放";
    if (kind === "legacy") return "历史迁移";
    return kind || "未知";
  }

  function deviceState(device) {
    if (device.jobs_failed > 0) return { label: "需处理", className: "failed" };
    if (device.jobs_in_progress > 0) return { label: "处理中", className: "running" };
    if (device.jobs_total > 0 && device.jobs_completed > 0) {
      return { label: "正常", className: "completed" };
    }
    if (device.active_sync_clients > 0) return { label: "已同步", className: "active" };
    return { label: "待使用", className: "neutral" };
  }

  function asrState(model) {
    if (model.healthy === true) return { label: "可用", className: "ok" };
    if (model.healthy === false) return { label: "不可用", className: "failed" };
    return { label: "未检测", className: "neutral" };
  }

  function renderSummary(summary) {
    $("#metric-users").textContent = summary.users ?? 0;
    $("#metric-devices").textContent = summary.bound_devices ?? 0;
    $("#metric-sync-clients").textContent = summary.active_sync_clients ?? 0;
    $("#metric-in-progress").textContent = summary.jobs_in_progress ?? 0;
    $("#metric-failed").textContent = summary.jobs_failed ?? 0;
  }

  function renderAttention(devices) {
    const attention = devices.filter((device) => (
      device.jobs_failed > 0 || device.jobs_in_progress > 0
    ));
    $("#attention-count").textContent = attention.length ? `${attention.length} 台` : "正常";
    $("#attention-list").innerHTML = attention.length
      ? attention.slice(0, 5).map((device) => {
        const stateInfo = device.jobs_failed > 0
          ? { label: `${device.jobs_failed} 个异常`, className: "failed", rowClass: "" }
          : { label: `${device.jobs_in_progress} 个处理中`, className: "running", rowClass: "is-running" };
        return `
          <div class="attention-item ${stateInfo.rowClass}">
            <div>
              <strong>${escapeHtml(displayDeviceId(device.device_id))}</strong>
              <span>${escapeHtml(device.owner_email || "未绑定账户")} · 最近 ${escapeHtml(formatTime(device.last_job_at))}</span>
            </div>
            <span class="status status-${stateInfo.className}">${stateInfo.label}</span>
          </div>
        `;
      }).join("")
      : `
        <div class="attention-item is-ok">
          <div>
            <strong>运行正常</strong>
            <span>没有需要处理的转写任务。</span>
          </div>
          <span class="status status-ok">正常</span>
        </div>
      `;
  }

  function renderAsr(asr) {
    const models = asr?.models || [];
    const selectedId = asr?.selected_model_id || "";
    const selected = models.find((model) => model.id === selectedId);
    const selectedState = selected ? asrState(selected) : { label: "未选择", className: "neutral" };
    $("#asr-selected").textContent = selectedId ? `当前：${selectedId}` : selectedState.label;
    $("#asr-selected").className = `status status-${selectedState.className}`;
    $("#asr-list").innerHTML = models.length
      ? models.map((model) => {
        const health = asrState(model);
        const name = model.name || model.id;
        return `
          <div class="asr-item">
            <div>
              <strong>${escapeHtml(name)}</strong>
              <span>${model.id === selectedId ? "当前选中模型" : escapeHtml(model.id)}</span>
            </div>
            <span class="status status-${health.className}">${health.label}</span>
          </div>
        `;
      }).join("")
      : '<div class="asr-item"><div><strong>未读取到模型</strong><span>请检查转写服务配置。</span></div><span class="status status-neutral">未知</span></div>';
  }

  function renderRecentJobs(jobs) {
    $("#recent-job-count").textContent = `${jobs.length} 条`;
    $("#recent-jobs-table").innerHTML = jobs.length
      ? jobs.map((job) => {
        const status = jobState(job.status);
        const sync = job.note_synced ? "已同步" : "待同步";
        return `
          <tr>
            <td class="cell-primary">${escapeHtml(job.recording_name)}</td>
            <td>${escapeHtml(displayDeviceId(job.device_id))}</td>
            <td><span class="status status-${status.className}">${status.label}</span></td>
            <td>${escapeHtml(formatTime(job.updated_at))}</td>
            <td><span class="status status-${job.note_synced ? "active" : "neutral"}">${sync}</span></td>
            <td title="${escapeHtml(job.error || "")}" class="${job.error ? "cell-secondary" : "cell-secondary"}">${escapeHtml(job.error || "-")}</td>
          </tr>
        `;
      }).join("")
      : '<tr><td class="empty" colspan="6">尚无转写任务</td></tr>';
  }

  function renderDevices(devices) {
    const query = state.deviceQuery;
    const filtered = devices.filter((device) => (
      !query || `${device.device_id} ${device.owner_email}`.toLowerCase().includes(query)
    ));
    $("#device-count").textContent = `${filtered.length} 台`;
    $("#devices-table").innerHTML = filtered.length
      ? filtered.map((device) => {
        const status = deviceState(device);
        return `
          <tr>
            <td class="cell-primary">${escapeHtml(displayDeviceId(device.device_id))}</td>
            <td>${escapeHtml(device.owner_email || "未绑定")}</td>
            <td>${device.active_sync_clients} 个</td>
            <td>${device.jobs_completed}/${device.jobs_total}</td>
            <td>${device.jobs_failed || "-"}</td>
            <td>${escapeHtml(formatTime(device.last_job_at))}</td>
            <td><span class="status status-${status.className}">${status.label}</span></td>
          </tr>
        `;
      }).join("")
      : '<tr><td class="empty" colspan="7">没有匹配的设备</td></tr>';
  }

  function renderUsers(users) {
    const query = state.deviceQuery;
    const filtered = users.filter((user) => (
      !query || `${user.email} ${user.device_id}`.toLowerCase().includes(query)
    ));
    $("#user-count").textContent = `${filtered.length} 个`;
    $("#users-table").innerHTML = filtered.length
      ? filtered.map((user) => `
        <tr>
          <td class="cell-primary">${escapeHtml(user.email)}</td>
          <td>${escapeHtml(displayDeviceId(user.device_id || "未绑定"))}</td>
          <td>${escapeHtml(formatTime(user.bound_at, "未绑定"))}</td>
          <td>${escapeHtml(formatTime(user.created_at))}</td>
        </tr>
      `).join("")
      : '<tr><td class="empty" colspan="4">没有匹配的账户</td></tr>';
  }

  function renderDeviceSelector(devices) {
    const select = $("#device-id");
    if (!select) return;
    const current = select.value;
    select.innerHTML = devices.length
      ? devices.map((device) => `
        <option value="${escapeHtml(device.device_id)}">
          ${escapeHtml(device.device_id)}${device.label ? ` · ${escapeHtml(device.label)}` : ""}
        </option>
      `).join("")
      : '<option value="">暂无已登记设备</option>';
    if (devices.some((device) => device.device_id === current)) {
      select.value = current;
    }
  }

  function filteredAccessRecords(records) {
    return records.filter((item) => {
      const searchable = `${item.device_id} ${item.label}`.toLowerCase();
      return (
        (!state.accessQuery || searchable.includes(state.accessQuery))
        && (state.accessStatus === "all" || item.status === state.accessStatus)
      );
    });
  }

  function renderAccess(access) {
    const codes = filteredAccessRecords(access.activation_codes || []);
    const tokens = filteredAccessRecords(access.sync_tokens || []);
    $("#record-count").textContent = `显示 ${codes.length + tokens.length} 条`;
    $("#code-filter-count").textContent = `${codes.length} 条`;
    $("#token-filter-count").textContent = `${tokens.length} 条`;

    $("#codes-table").innerHTML = codes.length
      ? codes.map((item) => `
        <tr>
          <td class="cell-primary">${escapeHtml(item.device_id)}</td>
          <td>${escapeHtml(item.label || "未填写")}</td>
          <td>${accessStatusTag(item.status)}</td>
          <td>${escapeHtml(formatTime(item.expires_at, "永久"))}</td>
          <td>${escapeHtml(formatTime(item.created_at))}</td>
          <td>
            ${item.status === "active"
              ? `<button class="danger-button table-action" data-action="revoke-code" data-id="${escapeHtml(item.id)}">撤销</button>`
              : '<span class="cell-secondary">无操作</span>'}
          </td>
        </tr>
      `).join("")
      : `<tr><td class="empty" colspan="6">${state.accessQuery || state.accessStatus !== "all" ? "没有匹配的激活码" : "还没有激活码记录"}</td></tr>`;

    $("#tokens-table").innerHTML = tokens.length
      ? tokens.map((item) => `
        <tr>
          <td class="cell-primary">${escapeHtml(item.device_id)}</td>
          <td>${escapeHtml(item.label || "未填写")}</td>
          <td>${accessStatusTag(item.status)}</td>
          <td>${escapeHtml(formatTime(item.last_used_at, "尚未使用"))}</td>
          <td>${escapeHtml(formatTime(item.created_at))}</td>
          <td>
            ${item.status === "active"
              ? `<button class="danger-button table-action" data-action="revoke-token" data-id="${escapeHtml(item.id)}">撤销令牌</button>
                 <button class="danger-button table-action" data-action="revoke-device" data-device="${escapeHtml(item.device_id)}">撤销设备</button>`
              : '<span class="cell-secondary">无操作</span>'}
          </td>
        </tr>
      `).join("")
      : `<tr><td class="empty" colspan="6">${state.accessQuery || state.accessStatus !== "all" ? "没有匹配的同步令牌" : "还没有同步令牌记录"}</td></tr>`;
  }

  function renderTranscriptionRuns(runs) {
    state.transcriptionRuns = runs;
    $("#run-count").textContent = `${runs.length} 条`;
    $("#transcription-runs-table").innerHTML = runs.length
      ? runs.map((run) => {
        const status = transcriptionRunState(run.status);
        const model = run.model_name || run.model_id || "未记录";
        const completedAt = run.completed_at || run.started_at || run.created_at;
        const seconds = Number(run.asr_seconds || 0);
        const canReplay = run.status === "completed" && Boolean(run.model_id);
        return `
          <tr>
            <td class="cell-primary">${escapeHtml(run.recording_name)}</td>
            <td>${escapeHtml(displayDeviceId(run.device_id))}</td>
            <td>${escapeHtml(transcriptionRunKind(run.run_kind))}</td>
            <td title="${escapeHtml(run.model_id)}">${escapeHtml(model)}</td>
            <td><span class="status status-${status.className}">${status.label}</span></td>
            <td>${seconds ? `${seconds.toFixed(1)} 秒` : "-"}</td>
            <td>${escapeHtml(formatTime(completedAt))}</td>
            <td title="${escapeHtml(run.error || run.transcript_preview || "")}" class="run-preview ${run.error ? "cell-secondary" : ""}">${escapeHtml(run.error || run.transcript_preview || "-")}</td>
            <td>
              ${canReplay
                ? `<button class="secondary table-action" data-action="replay-run" data-id="${escapeHtml(run.run_id)}">按此模型回放</button>`
                : '<span class="cell-secondary">无操作</span>'}
            </td>
          </tr>
        `;
      }).join("")
      : '<tr><td class="empty" colspan="9">尚无模型运行记录</td></tr>';
  }

  function renderDashboard(payload) {
    state.dashboard = payload;
    renderSummary(payload.summary || {});
    renderAttention(payload.devices || []);
    renderAsr(payload.asr || {});
    renderRecentJobs(payload.recent_jobs || []);
    renderDevices(payload.devices || []);
    renderUsers(payload.users || []);
    renderDeviceSelector(payload.devices || []);
    renderAccess(payload.access || {});
    renderTranscriptionRuns(payload.transcription_runs || []);
  }

  async function refresh({ quiet = false } = {}) {
    try {
      const payload = await requestJson("/api/admin/dashboard", { method: "GET", headers: {} });
      renderDashboard(payload);
      $("#last-refresh").textContent = `更新于 ${formatTime(new Date().toISOString())}`;
      if (!quiet) setMessage("数据已刷新");
    } catch (error) {
      setMessage(error.message, true);
    }
  }

  async function revoke(action, payload, message) {
    if (!confirm(message)) return;
    try {
      await requestJson(`/api/admin/${action}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await refresh({ quiet: true });
      setMessage("操作已完成");
    } catch (error) {
      setMessage(error.message, true);
    }
  }

  async function replayTranscription(runId) {
    if (!confirm("确认按这条记录当时的模型重新转写？回放不会覆盖当前转写、AI 结果或 Obsidian 笔记。")) return;
    try {
      await requestJson(`/api/admin/transcription-runs/${runId}/replay`, {
        method: "POST",
        body: "{}",
      });
      await refresh({ quiet: true });
      setMessage("模型回放已排队，扫描器下次运行时会处理");
    } catch (error) {
      setMessage(error.message, true);
    }
  }

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#login-error").textContent = "";
    try {
      await requestJson("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ password: $("#admin-password").value }),
      });
      $("#admin-password").value = "";
      showAdmin();
      await refresh({ quiet: true });
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  });

  $("#logout-button").addEventListener("click", async () => {
    await requestJson("/api/admin/logout", { method: "POST", body: "{}" }).catch(() => {});
    showLogin();
  });

  $("#refresh-button").addEventListener("click", () => refresh());
  $("#sidebar-endpoint").textContent = window.location.host;

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });

  $("#device-search").addEventListener("input", (event) => {
    state.deviceQuery = event.target.value.trim().toLowerCase();
    renderDevices(state.dashboard?.devices || []);
    renderUsers(state.dashboard?.users || []);
  });

  $("#record-search").addEventListener("input", (event) => {
    state.accessQuery = event.target.value.trim().toLowerCase();
    renderAccess(state.dashboard?.access || {});
  });

  $("#status-filter").addEventListener("change", (event) => {
    state.accessStatus = event.target.value;
    renderAccess(state.dashboard?.access || {});
  });

  $("#permanent").addEventListener("change", (event) => {
    $("#expires-at").disabled = event.target.checked;
    if (event.target.checked) $("#expires-at").value = "";
  });

  $("#register-device-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await requestJson("/api/admin/devices", {
        method: "POST",
        body: JSON.stringify({
          device_id: $("#new-device-id").value.trim(),
          label: $("#new-device-label").value.trim(),
        }),
      });
      $("#register-device-form").reset();
      await refresh({ quiet: true });
      setMessage("设备已登记，现在可以为它签发激活码");
    } catch (error) {
      setMessage(error.message, true);
    }
  });

  $("#issue-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!$("#permanent").checked && !$("#expires-at").value) {
      setMessage("请选择激活码有效期，或勾选永久有效", true);
      return;
    }
    const expiresAt = $("#permanent").checked
      ? ""
      : new Date($("#expires-at").value).toISOString();
    try {
      const payload = await requestJson("/api/admin/activation-codes", {
        method: "POST",
        body: JSON.stringify({
          device_id: $("#device-id").value.trim(),
          label: $("#label").value.trim(),
          expires_at: expiresAt,
          replace_existing: $("#replace-existing").checked,
        }),
      });
      $("#issued-code").textContent = payload.activation_code;
      $("#issued-meta").textContent = [
        payload.device_id,
        payload.expires_at ? `到期：${formatTime(payload.expires_at)}` : "永久有效",
        payload.replaces_previous ? "激活后替换旧同步端" : "保留已有同步端",
      ].join(" · ");
      $("#issued-result").classList.remove("hidden");
      $("#issue-form").reset();
      $("#permanent").checked = true;
      $("#expires-at").disabled = true;
      await refresh({ quiet: true });
      setMessage("激活码已签发，请在离开页面前复制给客户");
    } catch (error) {
      setMessage(error.message, true);
    }
  });

  $("#copy-code").addEventListener("click", async () => {
    const code = $("#issued-code").textContent;
    try {
      await navigator.clipboard.writeText(code);
      setMessage("激活码已复制");
    } catch {
      setMessage("复制失败，请手动复制激活码", true);
    }
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    if (button.dataset.action === "revoke-code") {
      revoke("activation-codes/revoke", { code_id: button.dataset.id }, "确认撤销这枚尚未使用的激活码？");
    } else if (button.dataset.action === "revoke-token") {
      revoke("tokens/revoke", { token_id: button.dataset.id }, "确认撤销这个同步令牌？");
    } else if (button.dataset.action === "revoke-device") {
      revoke("devices/revoke", { device_id: button.dataset.device }, `确认撤销设备 ${button.dataset.device} 的全部同步令牌？`);
    } else if (button.dataset.action === "replay-run") {
      replayTranscription(button.dataset.id);
    }
  });

  requestJson("/api/admin/session", { method: "GET", headers: {} })
    .then((session) => {
      if (!session.configured) {
        showLogin("管理员端尚未配置密码，请先设置 SYNC_ADMIN_PASSWORD。");
      } else if (session.authenticated) {
        showAdmin();
        return refresh({ quiet: true });
      } else {
        showLogin();
      }
    })
    .catch((error) => showLogin(error.message));
})();
