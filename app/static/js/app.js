(() => {
  const mediaSelect = document.getElementById('media-file');
  const refreshBtn = document.getElementById('refresh-media');
  const startExistingBtn = document.getElementById('start-existing');
  const uploadInput = document.getElementById('upload-input');
  const startUploadBtn = document.getElementById('start-upload');
  const sourceLangSelect = document.getElementById('source-language');
  const targetLangSelect = document.getElementById('target-language');
  const whisperBackendSelect = document.getElementById('whisper-backend');
  const whisperModelInput = document.getElementById('whisper-model');
  const gpuBaseUrlInput = document.getElementById('gpu-base-url');
  const gpuUrlHintEl = document.getElementById('gpu-url-hint');
  const translationBackendSelect = document.getElementById('translation-backend');
  const translationModelInput = document.getElementById('translation-model');
  const whisperDefaultsEl = document.getElementById('whisper-defaults');
  const translationDefaultsEl = document.getElementById('translation-defaults');
  const translationPricingEl = document.getElementById('translation-pricing');
  const estimateHintEl = document.getElementById('estimate-hint');
  const jobsEl = document.getElementById('jobs');
  const chunkSizeInput = document.getElementById('translation-chunk-size');
  let chunkSizeUserOverride = false;
  if (chunkSizeInput) {
    chunkSizeInput.addEventListener('input', () => { chunkSizeUserOverride = true; });
  }

  let cachedConfig = null;
  let cachedPricing = null;

  // Static pricing for DeepSeek (USD per token, cache-miss). Mirror of
  // _DEEPSEEK_PRICING in app/routes.py.
  const DEEPSEEK_PRICING = {
    'deepseek-v4-flash':  { prompt: 0.14e-6, completion: 0.28e-6 },
    'deepseek-v4-pro':    { prompt: 1.74e-6, completion: 3.48e-6 },
    'deepseek-chat':      { prompt: 0.14e-6, completion: 0.28e-6 },
    'deepseek-reasoner':  { prompt: 0.14e-6, completion: 0.28e-6 },
  };

  const trackedJobs = new Map(); // job_id -> { el, polling }

  async function loadConfig() {
    try {
      const res = await fetch('/api/config');
      const cfg = await res.json();
      cachedConfig = cfg;
      const w = cfg.whisper || {};
      const t = cfg.translation || {};
      if (whisperBackendSelect) whisperBackendSelect.value = '';
      if (translationBackendSelect) translationBackendSelect.value = '';
      if (gpuBaseUrlInput) gpuBaseUrlInput.value = cfg.gpu_base_url || '';
      if (whisperDefaultsEl) {
        whisperDefaultsEl.textContent =
          `(default: ${w.model || '?'} via ${w.backend || '?'})`;
      }
      if (translationDefaultsEl) {
        translationDefaultsEl.textContent =
          `(default: ${t.model || '?'} via ${t.backend || '?'})`;
      }
      if (whisperModelInput && !whisperModelInput.placeholder.includes(w.model || '')) {
        whisperModelInput.placeholder = `(use default: ${w.model || ''})`;
      }
      if (translationModelInput) {
        translationModelInput.placeholder = `(use default: ${t.model || ''})`;
      }
      updateGpuHint();
      updateAllForModel();
    } catch (err) {
      // non-fatal
    }
  }

  async function loadPricing() {
    try {
      const res = await fetch('/api/openrouter/pricing');
      const data = await res.json();
      cachedPricing = data.pricing || {};
      updateAllForModel();
      refreshEstimate();
    } catch (err) {
      // non-fatal
    }
  }

  let cachedModels = [];
  const modelPickerState = { sort: 'newest', search: '', freeOnly: false, jsonOnly: false, ageMonths: 0 };

  function fmtPricePer1M(perToken) {
    const m = Number(perToken) * 1e6;
    if (!isFinite(m)) return '?';
    if (m === 0) return 'free';
    if (m < 0.01) return `$${m.toFixed(4)}`;
    if (m < 1) return `$${m.toFixed(3)}`;
    return `$${m.toFixed(2)}`;
  }

  function fmtModelDate(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  }

  function isRecentModel(ts) {
    if (!ts) return false;
    return (Date.now() / 1000 - ts) < 60 * 60 * 24 * 90; // last 90 days
  }

  async function loadModels() {
    const datalist = document.getElementById('translation-model-list');
    try {
      const res = await fetch('/api/openrouter/models');
      const data = await res.json();
      cachedModels = data.models || [];
    } catch (err) {
      cachedModels = [];
      return;
    }
    if (datalist) {
      const ollamaOptions = Array.from(datalist.querySelectorAll('option'))
        .filter(o => o.value && !o.value.includes('/'));
      datalist.innerHTML = '';
      for (const m of cachedModels) {
        const opt = document.createElement('option');
        opt.value = m.slug;
        const flags = [];
        if (m.is_free) flags.push('free');
        if (!m.supports_json_mode) flags.push('no JSON');
        const flagStr = flags.length ? ` [${flags.join(', ')}]` : '';
        const date = fmtModelDate(m.created);
        opt.label = `${m.slug} — ${date} — in ${fmtPricePer1M(m.prompt_per_token)} / out ${fmtPricePer1M(m.completion_per_token)} per 1M${flagStr}`;
        datalist.appendChild(opt);
      }
      for (const o of ollamaOptions) datalist.appendChild(o);
    }
    renderModelPicker();
  }

  function renderModelPicker() {
    const list = document.getElementById('model-picker-list');
    if (!list) return;
    let items = cachedModels.slice();
    if (modelPickerState.freeOnly) items = items.filter(m => m.is_free);
    if (modelPickerState.jsonOnly) items = items.filter(m => m.supports_json_mode);
    if (modelPickerState.ageMonths > 0) {
      const cutoff = Date.now() / 1000 - modelPickerState.ageMonths * 30 * 24 * 3600;
      items = items.filter(m => (m.created || 0) >= cutoff);
    }
    if (modelPickerState.search) {
      const q = modelPickerState.search.toLowerCase();
      items = items.filter(m => m.slug.toLowerCase().includes(q));
    }
    const sort = modelPickerState.sort;
    if (sort === 'newest') {
      items.sort((a, b) => (b.created || 0) - (a.created || 0) || a.slug.localeCompare(b.slug));
    } else if (sort === 'cheapest') {
      const cost = m => (Number(m.prompt_per_token) + Number(m.completion_per_token)) / 2;
      items.sort((a, b) => cost(a) - cost(b) || a.slug.localeCompare(b.slug));
    } else {
      items.sort((a, b) => a.slug.localeCompare(b.slug));
    }
    list.innerHTML = '';
    if (!items.length) {
      list.innerHTML = '<div style="padding:12px;color:#9aa4b8;font-size:0.85rem;">No models match.</div>';
      return;
    }
    for (const m of items) {
      const row = document.createElement('div');
      row.className = 'model-row';
      const badges = [];
      if (isRecentModel(m.created)) badges.push('<span class="badge new">new</span>');
      if (m.is_free) badges.push('<span class="badge free">free</span>');
      if (!m.supports_json_mode) badges.push('<span class="badge no-json">no JSON</span>');
      row.innerHTML = `
        <span class="slug">${m.slug}</span>
        <span class="date">${fmtModelDate(m.created) || '—'}</span>
        <span class="price">in ${fmtPricePer1M(m.prompt_per_token)} / out ${fmtPricePer1M(m.completion_per_token)}</span>
        <span class="badges">${badges.join('')}</span>
      `;
      row.addEventListener('click', () => {
        if (translationModelInput) {
          translationModelInput.value = m.slug;
          translationModelInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
      list.appendChild(row);
    }
  }

  function bindModelPicker() {
    const search = document.getElementById('model-picker-search');
    if (search) search.addEventListener('input', e => {
      modelPickerState.search = e.target.value;
      renderModelPicker();
    });
    const sortBtns = document.querySelectorAll('.model-picker-sort button');
    sortBtns.forEach(b => b.addEventListener('click', () => {
      modelPickerState.sort = b.dataset.sort;
      sortBtns.forEach(x => x.classList.toggle('active', x === b));
      renderModelPicker();
    }));
    const freeOnly = document.getElementById('model-picker-free-only');
    if (freeOnly) freeOnly.addEventListener('change', e => {
      modelPickerState.freeOnly = e.target.checked;
      renderModelPicker();
    });
    const jsonOnly = document.getElementById('model-picker-json-only');
    if (jsonOnly) jsonOnly.addEventListener('change', e => {
      modelPickerState.jsonOnly = e.target.checked;
      renderModelPicker();
    });
    const ageSel = document.getElementById('model-picker-age');
    if (ageSel) ageSel.addEventListener('change', e => {
      modelPickerState.ageMonths = parseInt(e.target.value, 10) || 0;
      renderModelPicker();
    });
  }

  function fmtPricePerMillion(perToken) {
    if (perToken == null) return '?';
    const perM = Number(perToken) * 1e6;
    if (!isFinite(perM)) return '?';
    if (perM === 0) return 'free';
    if (perM < 0.01) return `$${perM.toFixed(4)}`;
    if (perM < 1) return `$${perM.toFixed(3)}`;
    return `$${perM.toFixed(2)}`;
  }

  function fmtUsd(n) {
    if (n == null || !isFinite(n)) return '?';
    if (n === 0) return '$0';
    if (n < 0.0001) return '<$0.0001';
    if (n < 0.01) return `$${n.toFixed(5)}`;
    if (n < 1) return `$${n.toFixed(3)}`;
    return `$${n.toFixed(2)}`;
  }

  function activeTranslationModel() {
    const override = translationModelInput && translationModelInput.value.trim();
    if (override) return override;
    return (cachedConfig && cachedConfig.translation && cachedConfig.translation.model) || '';
  }

  function activeTranslationBackend() {
    if (translationBackendSelect && translationBackendSelect.value) {
      return translationBackendSelect.value;
    }
    return (cachedConfig && cachedConfig.translation && cachedConfig.translation.backend) || '';
  }

  function updateGpuHint() {
    if (!gpuUrlHintEl || !gpuBaseUrlInput) return;
    const base = gpuBaseUrlInput.value.trim().replace(/\/+$/, '').replace(/:+$/, '');
    gpuUrlHintEl.textContent = base ? `Whisper: ${base}:5051 · Ollama: ${base}:11434` : '';
  }

  function computeAdaptiveChunkSize() {
    const backend = (activeTranslationBackend() || '').toLowerCase();
    const model = activeTranslationModel();
    const modelLc = (model || '').toLowerCase();
    // DeepSeek V4 family handles large chunks reliably regardless of backend.
    if (modelLc.includes('deepseek-v4')) return 20;
    if (backend === 'ollama') return 8;
    if (backend === 'deepseek') return 20;
    if (backend !== 'openrouter' || !model) return 10;
    if (model.toLowerCase().includes(':free')) return 5;
    if (!cachedPricing) return null; // not yet loaded
    const entry = cachedPricing[model];
    if (!entry) return 10;
    const avgPerM = ((Number(entry.prompt) || 0) + (Number(entry.completion) || 0)) / 2 * 1e6;
    if (avgPerM <= 0) return 5;
    if (avgPerM <= 0.30) return 8;
    if (avgPerM <= 1.50) return 15;
    return 20;
  }

  function updateChunkSizeAuto() {
    if (!chunkSizeInput || chunkSizeUserOverride) return;
    const n = computeAdaptiveChunkSize();
    if (n != null) chunkSizeInput.value = String(n);
  }

  function updateTranslationPricing() {
    if (!translationPricingEl) return;
    const backend = activeTranslationBackend();
    const model = activeTranslationModel();
    if ((backend || '').toLowerCase() === 'deepseek' && model) {
      const ds = DEEPSEEK_PRICING[model];
      if (ds) {
        translationPricingEl.textContent =
          `DeepSeek price: input ${fmtPricePerMillion(ds.prompt)} / output ${fmtPricePerMillion(ds.completion)} per 1M tokens`;
      } else {
        translationPricingEl.textContent = `(no pricing data for ${model})`;
      }
      return;
    }
    if (backend !== 'openrouter' || !model) {
      translationPricingEl.textContent = '';
      return;
    }
    if (!cachedPricing) {
      translationPricingEl.textContent = 'Loading pricing…';
      return;
    }
    const entry = cachedPricing[model];
    if (!entry) {
      translationPricingEl.textContent = `(no pricing data for ${model})`;
      return;
    }
    const inP = fmtPricePerMillion(entry.prompt);
    const outP = fmtPricePerMillion(entry.completion);
    translationPricingEl.textContent =
      `OpenRouter price: input ${inP} / output ${outP} per 1M tokens`;
  }

  function updateAllForModel() {
    updateTranslationPricing();
    updateChunkSizeAuto();
  }

  let estimateSeq = 0;
  async function refreshEstimate() {
    if (!estimateHintEl) return;
    const selected = mediaSelect.value;
    if (!selected) {
      estimateHintEl.textContent = '';
      return;
    }
    const params = new URLSearchParams({ selected_file: selected });
    const model = activeTranslationModel();
    const backend = activeTranslationBackend();
    if (model) params.set('translation_model', model);
    if (backend) params.set('translation_backend', backend);

    const seq = ++estimateSeq;
    estimateHintEl.textContent = 'Estimating…';
    try {
      const res = await fetch(`/api/estimate?${params.toString()}`);
      const data = await res.json();
      if (seq !== estimateSeq) return;
      if (!data.success) {
        estimateHintEl.textContent = data.message || 'Could not estimate';
        return;
      }
      const t = data.tokens || {};
      const cost = data.cost;
      const sourceLabel = {
        orig_srt: 'from existing transcript',
        duration_heuristic: 'estimated from duration',
        unknown: 'unknown size',
      }[t.source] || t.source || '';
      const lineParts = [];
      if (t.segment_count) {
        lineParts.push(`~${t.segment_count} lines`);
      } else if (t.duration_seconds) {
        lineParts.push(`${(t.duration_seconds / 60).toFixed(1)} min`);
      }
      lineParts.push(`~${t.input_tokens.toLocaleString()} in / ~${t.output_tokens.toLocaleString()} out tokens`);
      let line = lineParts.join(' · ');
      if (cost) {
        line += ` · estimated cost ~${fmtUsd(cost.total_usd)}`;
        line += ` (in ${fmtUsd(cost.prompt_usd)} + out ${fmtUsd(cost.completion_usd)})`;
      } else if (data.translation_backend === 'openrouter' && model) {
        line += ` · (no OpenRouter pricing for ${model})`;
      }
      if (sourceLabel) line += ` — ${sourceLabel}`;
      estimateHintEl.textContent = line;
    } catch (err) {
      if (seq !== estimateSeq) return;
      estimateHintEl.textContent = '';
    }
  }

  function appendModelOverrides(fd) {
    if (whisperBackendSelect && whisperBackendSelect.value) {
      fd.append('whisper_backend', whisperBackendSelect.value);
    }
    if (whisperModelInput && whisperModelInput.value.trim()) {
      fd.append('whisper_model', whisperModelInput.value.trim());
    }
    if (gpuBaseUrlInput && gpuBaseUrlInput.value.trim()) {
      fd.append('gpu_base_url', gpuBaseUrlInput.value.trim());
    }
    if (translationBackendSelect && translationBackendSelect.value) {
      fd.append('translation_backend', translationBackendSelect.value);
    }
    if (translationModelInput && translationModelInput.value.trim()) {
      fd.append('translation_model', translationModelInput.value.trim());
    }
    if (chunkSizeInput && chunkSizeInput.value.trim()) {
      fd.append('translation_chunk_size', chunkSizeInput.value.trim());
    }
  }

  function selectedRunMode() {
    const checked = document.querySelector('input[name="run-mode"]:checked');
    return checked ? checked.value : 'full';
  }

  function appendRunMode(fd) {
    fd.append('mode', selectedRunMode());
  }

  async function refreshMedia() {
    mediaSelect.innerHTML = '<option value="">(loading...)</option>';
    try {
      const res = await fetch('/api/media');
      const data = await res.json();
      mediaSelect.innerHTML = '';
      if (!data.files || !data.files.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '(no media files in MEDIA_DIR)';
        mediaSelect.appendChild(opt);
        return;
      }
      for (const name of data.files) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        mediaSelect.appendChild(opt);
      }
      refreshEstimate();
    } catch (err) {
      mediaSelect.innerHTML = `<option value="">(error: ${err})</option>`;
    }
  }

  function createJobCard(jobId, label) {
    const el = document.createElement('div');
    el.className = 'job';
    el.dataset.jobId = jobId;
    el.innerHTML = `
      <div class="name"></div>
      <div class="meta"></div>
      <div class="bar"><div style="width:0%"></div></div>
      <pre class="job-log" hidden></pre>
      <div class="actions"></div>
      <div class="error"></div>
    `;
    el.querySelector('.name').textContent = label;
    jobsEl.prepend(el);
    return el;
  }

  function updateJobCard(el, status) {
    el.classList.remove('completed', 'failed', 'running', 'awaiting_translation');
    el.classList.add(status.status || 'running');

    el.querySelector('.meta').textContent =
      `${status.status || 'running'} · ${status.progress || 0}% · ${status.message || ''}`;
    el.querySelector('.bar > div').style.width = `${status.progress || 0}%`;

    const errEl = el.querySelector('.error');
    errEl.textContent = status.error || '';

    // Live translation log preview.
    const logEl = el.querySelector('.job-log');
    if (logEl) {
      const lines = Array.isArray(status.log) ? status.log : [];
      if (lines.length) {
        logEl.hidden = false;
        const wasNearBottom =
          logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 8;
        logEl.textContent = lines.join('\n');
        if (wasNearBottom) logEl.scrollTop = logEl.scrollHeight;
      } else if (status.status !== 'running') {
        // Keep the log around when finished/failed; only hide if nothing was logged.
        logEl.hidden = true;
      }
    }

    const actions = el.querySelector('.actions');
    actions.innerHTML = '';

    // Cancel button while running or awaiting translation.
    if (status.status === 'running') {
      const cancelBtn = document.createElement('button');
      cancelBtn.textContent = 'Cancel';
      cancelBtn.className = 'danger';
      cancelBtn.onclick = async () => {
        if (!confirm('Cancel this job?')) return;
        cancelBtn.disabled = true;
        cancelBtn.textContent = 'Cancelling…';
        try {
          await fetch(`/api/jobs/${status.job_id}/cancel`, { method: 'POST' });
        } catch (err) {
          alert(`Cancel failed: ${err}`);
        }
      };
      actions.appendChild(cancelBtn);
    }

    if (status.status === 'awaiting_translation' && status.result) {
      // Don't rebuild the panel on every poll; that would wipe the user's
      // model/target inputs mid-typing.
      if (el.dataset.awaitingRendered === '1') {
        return;
      }
      el.dataset.awaitingRendered = '1';
      renderAwaitingTranslation(el, actions, status);
      return;
    }
    el.dataset.awaitingRendered = '';
    if (status.status === 'completed' && status.result) {
      const summary = document.createElement('div');
      summary.className = 'job-summary';
      const r = status.result;
      const usage = r.usage || {};
      const lines = [];
      lines.push(`Whisper: ${r.whisper_model || '?'} (${r.whisper_backend || '?'})`);
      lines.push(
        `Translation: ${r.translation_model || '?'} (${r.translation_backend || '?'})`
      );
      if (usage.total_tokens) {
        lines.push(
          `Tokens: ${usage.prompt_tokens || 0} in / ${usage.completion_tokens || 0} out`
        );
      }
      if (status.cost) {
        lines.push(
          `Estimated cost: ${fmtUsd(status.cost.total_usd)} ` +
          `(in ${fmtUsd(status.cost.prompt_usd)} + out ${fmtUsd(status.cost.completion_usd)})`
        );
      } else if (r.translation_backend === 'openrouter' && usage.total_tokens) {
        lines.push('Estimated cost: (no pricing data for this model)');
      }
      summary.innerHTML = lines.map((l) => `<div>${l}</div>`).join('');
      actions.appendChild(summary);

      const links = [
        ['original', 'Download original SRT'],
        ['bilingual', 'Download bilingual SRT'],
      ];
      for (const [kind, label] of links) {
        const a = document.createElement('a');
        a.href = `/api/jobs/${status.job_id}/download/${kind}`;
        a.textContent = label;
        a.className = 'btn-link';
        const btn = document.createElement('button');
        btn.textContent = label;
        btn.onclick = () => { window.location.href = a.href; };
        actions.appendChild(btn);
      }
    }
  }

  function renderAwaitingTranslation(card, actions, status) {
    const r = status.result || {};
    const summary = document.createElement('div');
    summary.className = 'job-summary';
    const lines = [];
    lines.push(`Whisper: ${r.whisper_model || '?'} (${r.whisper_backend || '?'})`);
    lines.push(`Detected source language: ${r.source_language || 'unknown'}`);
    if (r.segment_count) lines.push(`${r.segment_count} subtitle lines transcribed`);
    summary.innerHTML = lines.map((l) => `<div>${l}</div>`).join('');
    actions.appendChild(summary);

    // Allow downloading the orig SRT immediately.
    const origBtn = document.createElement('button');
    origBtn.textContent = 'Download original SRT';
    origBtn.onclick = () => {
      window.location.href = `/api/jobs/${status.job_id}/download/original`;
    };
    actions.appendChild(origBtn);

    // Translate panel.
    const panel = document.createElement('div');
    panel.className = 'translate-panel';

    const defaultTranslationModel =
      (cachedConfig && cachedConfig.translation && cachedConfig.translation.model) || '';
    const defaultTarget =
      (cachedConfig && cachedConfig.target_language) || 'zh';
    const currentTarget = r.target_language || defaultTarget;

    panel.innerHTML = `
      <div class="row">
        <strong>Pick translation model</strong>
        <span class="hint job-pricing"></span>
      </div>
      <div class="row">
        <label class="inline">
          Target lang
          <input type="text" class="job-target" value="${currentTarget}" size="6">
        </label>
        <select class="job-backend">
          <option value="">Default backend</option>
          <option value="ollama">Ollama</option>
          <option value="openrouter">OpenRouter</option>
          <option value="deepseek">DeepSeek</option>
        </select>
        <input type="text" class="job-model" list="translation-model-list"
               placeholder="(default: ${defaultTranslationModel})">
        <label class="inline">
          Chunk size
          <input type="number" class="job-chunk" min="1" max="50" step="1" size="3">
        </label>
        <button type="button" class="primary job-translate-btn">Translate</button>
      </div>
      <div class="hint job-estimate"></div>
    `;
    actions.appendChild(panel);

    const modelInput = panel.querySelector('.job-model');
    const backendInput = panel.querySelector('.job-backend');
    const targetInput = panel.querySelector('.job-target');
    const chunkInput = panel.querySelector('.job-chunk');
    const pricingEl = panel.querySelector('.job-pricing');
    const estimateEl = panel.querySelector('.job-estimate');
    const translateBtn = panel.querySelector('.job-translate-btn');
    let jobChunkUserOverride = false;
    chunkInput.addEventListener('input', () => { jobChunkUserOverride = true; });

    function autoChunkForJob() {
      if (jobChunkUserOverride) return;
      const backend = (backendInput.value || activeTranslationBackend()).toLowerCase();
      const model = (modelInput.value.trim() || defaultTranslationModel || '').toLowerCase();
      // Reuse the global computeAdaptiveChunkSize logic by temporarily mirroring
      // the model selection. Compute inline:
      let n;
      if (backend === 'ollama') n = 8;
      else if (backend !== 'openrouter' || !model) n = 10;
      else if (model.includes(':free')) n = 5;
      else if (!cachedPricing) n = null;
      else {
        const entry = cachedPricing[modelInput.value.trim() || defaultTranslationModel];
        if (!entry) n = 10;
        else {
          const avg = ((Number(entry.prompt) || 0) + (Number(entry.completion) || 0)) / 2 * 1e6;
          if (avg <= 0) n = 5;
          else if (avg <= 0.30) n = 8;
          else if (avg <= 1.50) n = 15;
          else n = 20;
        }
      }
      if (n != null) chunkInput.value = String(n);
    }

    function updatePricingForJob() {
      const backend = (backendInput.value || activeTranslationBackend() || '').toLowerCase();
      const model = modelInput.value.trim() || defaultTranslationModel;
      if (backend === 'deepseek' && model) {
        const ds = DEEPSEEK_PRICING[model];
        pricingEl.textContent = ds
          ? `DeepSeek: input ${fmtPricePerMillion(ds.prompt)} / output ${fmtPricePerMillion(ds.completion)} per 1M tokens`
          : `(no DeepSeek pricing for ${model})`;
        return;
      }
      if (backend !== 'openrouter') {
        pricingEl.textContent = '';
        return;
      }
      if (!cachedPricing || !model) {
        pricingEl.textContent = '';
        return;
      }
      const entry = cachedPricing[model];
      if (!entry) {
        pricingEl.textContent = `(no OpenRouter pricing for ${model})`;
        return;
      }
      pricingEl.textContent =
        `OpenRouter: input ${fmtPricePerMillion(entry.prompt)} / output ${fmtPricePerMillion(entry.completion)} per 1M tokens`;
    }

    let jobEstSeq = 0;
    async function refreshJobEstimate() {
      const model = modelInput.value.trim() || defaultTranslationModel;
      const seq = ++jobEstSeq;
      estimateEl.textContent = 'Estimating…';
      const params = new URLSearchParams({ job_id: status.job_id });
      if (model) params.set('translation_model', model);
      if (backendInput.value) params.set('translation_backend', backendInput.value);
      try {
        const res = await fetch(`/api/estimate?${params.toString()}`);
        const data = await res.json();
        if (seq !== jobEstSeq) return;
        if (!data.success) {
          estimateEl.textContent = data.message || '';
          return;
        }
        const t = data.tokens || {};
        const cost = data.cost;
        const parts = [];
        if (t.segment_count) parts.push(`~${t.segment_count} lines`);
        parts.push(`~${t.input_tokens.toLocaleString()} in / ~${t.output_tokens.toLocaleString()} out tokens`);
        if (cost) {
          parts.push(`estimated cost ~${fmtUsd(cost.total_usd)}`);
        } else if (data.translation_backend === 'openrouter' && model) {
          parts.push(`(no pricing for ${model})`);
        }
        estimateEl.textContent = parts.join(' · ');
      } catch (err) {
        if (seq !== jobEstSeq) return;
        estimateEl.textContent = '';
      }
    }

    modelInput.addEventListener('input', () => {
      updatePricingForJob();
      autoChunkForJob();
      refreshJobEstimate();
    });
    backendInput.addEventListener('change', () => {
      updatePricingForJob();
      autoChunkForJob();
      refreshJobEstimate();
    });

    translateBtn.addEventListener('click', async () => {
      if (translateBtn.disabled) return;
      translateBtn.disabled = true;
      const prevText = translateBtn.textContent;
      translateBtn.textContent = 'Starting…';
      try {
        const fd = new FormData();
        const model = modelInput.value.trim();
        const backend = backendInput.value.trim();
        const target = targetInput.value.trim();
        const chunk = chunkInput.value.trim();
        if (model) fd.append('translation_model', model);
        if (backend) fd.append('translation_backend', backend);
        if (target) fd.append('target_language', target);
        if (chunk) fd.append('translation_chunk_size', chunk);
        const res = await fetch(`/api/jobs/${status.job_id}/translate`, {
          method: 'POST',
          body: fd,
        });
        const data = await res.json();
        if (!data.success) {
          alert(data.message || 'Failed to start translation');
        }
      } catch (err) {
        alert(`Failed to start translation: ${err}`);
      } finally {
        translateBtn.disabled = false;
        translateBtn.textContent = prevText;
      }
    });

    updatePricingForJob();
    autoChunkForJob();
    refreshJobEstimate();
  }

  async function pollJob(jobId, el) {
    while (true) {
      let status;
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        status = await res.json();
      } catch (err) {
        await sleep(2000);
        continue;
      }
      if (!status.success) {
        el.querySelector('.error').textContent = status.message || 'Job not found';
        return;
      }
      updateJobCard(el, status);
      if (status.status === 'completed' || status.status === 'failed') return;
      // For awaiting_translation, keep polling but slowly: the user takes
      // action via the translate panel which transitions us back to running.
      const delay = status.status === 'awaiting_translation' ? 4000 : 1500;
      await sleep(delay);
    }
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async function startExistingJob() {
    const selected = mediaSelect.value;
    if (!selected) {
      alert('Please choose a media file');
      return;
    }
    const fd = new FormData();
    fd.append('selected_file', selected);
    if (sourceLangSelect.value) fd.append('source_language', sourceLangSelect.value);
    if (targetLangSelect && targetLangSelect.value) fd.append('target_language', targetLangSelect.value);
    appendModelOverrides(fd);
    appendRunMode(fd);
    await submitJob(fd, selected);
  }

  async function startUploadJob() {
    const file = uploadInput.files && uploadInput.files[0];
    if (!file) {
      alert('Please choose a file to upload');
      return;
    }
    const fd = new FormData();
    fd.append('media_file', file);
    if (sourceLangSelect.value) fd.append('source_language', sourceLangSelect.value);
    if (targetLangSelect && targetLangSelect.value) fd.append('target_language', targetLangSelect.value);
    appendModelOverrides(fd);
    appendRunMode(fd);
    await submitJob(fd, file.name);
  }

  async function submitJob(formData, label) {
    let res, data;
    try {
      res = await fetch('/api/jobs', { method: 'POST', body: formData });
      data = await res.json();
    } catch (err) {
      alert(`Failed to start job: ${err}`);
      return;
    }
    if (!data.success) {
      alert(data.message || 'Failed to start job');
      return;
    }
    const card = createJobCard(data.job_id, label);
    pollJob(data.job_id, card);
    refreshMedia();
  }

  function withSubmitGuard(btn, fn) {
    return async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      const prevText = btn.textContent;
      btn.textContent = 'Starting...';
      try {
        await fn();
      } finally {
        btn.disabled = false;
        btn.textContent = prevText;
      }
    };
  }

  refreshBtn.addEventListener('click', refreshMedia);
  startExistingBtn.addEventListener('click', withSubmitGuard(startExistingBtn, startExistingJob));
  startUploadBtn.addEventListener('click', withSubmitGuard(startUploadBtn, startUploadJob));
  if (translationModelInput) {
    translationModelInput.addEventListener('input', () => {
      updateAllForModel();
      refreshEstimate();
    });
    translationModelInput.addEventListener('change', () => {
      updateAllForModel();
      refreshEstimate();
    });
  }
  if (translationBackendSelect) {
    translationBackendSelect.addEventListener('change', () => {
      updateAllForModel();
      refreshEstimate();
    });
  }
  if (gpuBaseUrlInput) {
    gpuBaseUrlInput.addEventListener('input', updateGpuHint);
  }
  if (mediaSelect) {
    mediaSelect.addEventListener('change', refreshEstimate);
  }

  loadConfig();
  loadPricing();
  loadModels();
  bindModelPicker();
  refreshMedia();
})();
