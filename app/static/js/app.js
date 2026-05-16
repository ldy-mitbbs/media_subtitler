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

  // Settings UI elements
  const settingGpuBaseUrl = document.getElementById('setting-gpu-base-url');
  const settingRemoteWhisperUrl = document.getElementById('setting-remote-whisper-url');
  const settingOllamaUrl = document.getElementById('setting-ollama-url');
  const settingOpenrouterUrl = document.getElementById('setting-openrouter-url');
  const settingOpenrouterApiKey = document.getElementById('setting-openrouter-api-key');
  const settingOpenrouterReferer = document.getElementById('setting-openrouter-referer');
  const settingOpenrouterAppTitle = document.getElementById('setting-openrouter-app-title');
  const settingDeepseekUrl = document.getElementById('setting-deepseek-url');
  const settingDeepseekApiKey = document.getElementById('setting-deepseek-api-key');
  const settingWhisperBackend = document.getElementById('setting-whisper-backend');
  const settingWhisperModel = document.getElementById('setting-whisper-model');
  const settingTranslationBackend = document.getElementById('setting-translation-backend');
  const settingTranslationModel = document.getElementById('setting-translation-model');
  const settingTargetLanguage = document.getElementById('setting-target-language');
  const saveSettingsBtn = document.getElementById('save-settings');
  const settingsSaveStatusEl = document.getElementById('settings-save-status');

  let cachedConfig = null;
  let cachedPricing = null;
  let cachedSettings = null;

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
      const w = cfg.asr || cfg.whisper || {};
      const t = cfg.translation || {};
      if (whisperBackendSelect) whisperBackendSelect.value = '';
      if (translationBackendSelect) translationBackendSelect.value = '';
      if (gpuBaseUrlInput) gpuBaseUrlInput.value = cfg.gpu_base_url || '';
      if (whisperDefaultsEl) {
        whisperDefaultsEl.textContent =
          `（默认：${w.model || '?'} 通过 ${w.backend || '?'}）`;      }
      if (translationDefaultsEl) {
        translationDefaultsEl.textContent =
          `（默认：${t.model || '?'} 通过 ${t.backend || '?'}）`;      }
      if (whisperModelInput && !whisperModelInput.placeholder.includes(w.model || '')) {
        whisperModelInput.placeholder = `（使用默认：${w.model || ''}）`;      }
      if (translationModelInput) {
        translationModelInput.placeholder = `（使用默认：${t.model || ''}）`;      }
      updateGpuHint();
      updateAllForModel();
    } catch (err) {
      // non-fatal
    }
  }

  async function loadSettings() {
    try {
      const res = await fetch('/api/settings');
      const s = await res.json();
      cachedSettings = s;
      if (settingGpuBaseUrl) settingGpuBaseUrl.value = s.gpu_base_url || '';
      if (settingRemoteWhisperUrl) settingRemoteWhisperUrl.value = s.remote_whisper_base_url || '';
      if (settingOllamaUrl) settingOllamaUrl.value = s.ollama_base_url || '';
      if (settingOpenrouterUrl) settingOpenrouterUrl.value = s.openrouter_base_url || '';
      if (settingOpenrouterApiKey) settingOpenrouterApiKey.value = s.openrouter_api_key || '';
      if (settingOpenrouterReferer) settingOpenrouterReferer.value = s.openrouter_referer || '';
      if (settingOpenrouterAppTitle) settingOpenrouterAppTitle.value = s.openrouter_app_title || '';
      if (settingDeepseekUrl) settingDeepseekUrl.value = s.deepseek_base_url || '';
      if (settingDeepseekApiKey) settingDeepseekApiKey.value = s.deepseek_api_key || '';
      const savedAsrBackend = s.asr_backend || s.whisper_backend || '';
      const savedAsrModel = s.asr_model || s.whisper_model || '';
      if (settingWhisperBackend) settingWhisperBackend.value = savedAsrBackend;
      if (settingWhisperModel) settingWhisperModel.value = savedAsrModel;
      if (settingTranslationBackend) settingTranslationBackend.value = s.translation_backend || '';
      if (settingTranslationModel) settingTranslationModel.value = s.translation_model || '';
      if (settingTargetLanguage) settingTargetLanguage.value = s.target_language || '';

      // Also update the main job form defaults so they reflect saved settings.
      if (whisperBackendSelect && savedAsrBackend) {
        whisperBackendSelect.value = savedAsrBackend;
      }
      if (gpuBaseUrlInput && s.gpu_base_url) {
        gpuBaseUrlInput.value = s.gpu_base_url;
        updateGpuHint();
      }
      if (translationBackendSelect && s.translation_backend) {
        translationBackendSelect.value = s.translation_backend;
      }
      if (targetLangSelect && s.target_language) {
        targetLangSelect.value = s.target_language;
      }
      if (whisperModelInput && savedAsrModel) {
        whisperModelInput.placeholder = `（使用默认：${savedAsrModel}）`;
      }
      if (translationModelInput && s.translation_model) {
        translationModelInput.placeholder = `（使用默认：${s.translation_model}）`;
      }
      updateAllForModel();
    } catch (err) {
      // non-fatal
    }
  }

  async function saveSettings() {
    if (!saveSettingsBtn) return;
    saveSettingsBtn.disabled = true;
    const prevText = saveSettingsBtn.textContent;
    saveSettingsBtn.textContent = '保存中...';
    try {
      const payload = {
        GPU_BASE_URL: settingGpuBaseUrl ? settingGpuBaseUrl.value.trim() : '',
        REMOTE_WHISPER_BASE_URL: settingRemoteWhisperUrl ? settingRemoteWhisperUrl.value.trim() : '',
        OLLAMA_BASE_URL: settingOllamaUrl ? settingOllamaUrl.value.trim() : '',
        OPENROUTER_BASE_URL: settingOpenrouterUrl ? settingOpenrouterUrl.value.trim() : '',
        OPENROUTER_API_KEY: settingOpenrouterApiKey ? settingOpenrouterApiKey.value.trim() : '',
        OPENROUTER_REFERER: settingOpenrouterReferer ? settingOpenrouterReferer.value.trim() : '',
        OPENROUTER_APP_TITLE: settingOpenrouterAppTitle ? settingOpenrouterAppTitle.value.trim() : '',
        DEEPSEEK_BASE_URL: settingDeepseekUrl ? settingDeepseekUrl.value.trim() : '',
        DEEPSEEK_API_KEY: settingDeepseekApiKey ? settingDeepseekApiKey.value.trim() : '',
        ASR_BACKEND: settingWhisperBackend ? settingWhisperBackend.value.trim() : '',
        ASR_MODEL: settingWhisperModel ? settingWhisperModel.value.trim() : '',
        TRANSLATION_BACKEND: settingTranslationBackend ? settingTranslationBackend.value.trim() : '',
        TRANSLATION_MODEL: settingTranslationModel ? settingTranslationModel.value.trim() : '',
        TARGET_LANGUAGE: settingTargetLanguage ? settingTargetLanguage.value.trim() : '',
      };
      // Remove empty-string overrides so we don't overwrite .env defaults
      // with empty strings unless the user explicitly cleared a field.
      // However, for API keys and URLs, empty string means "clear it".
      // We'll send everything as-is; the backend will handle it.

      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        // Update local cachedSettings
        cachedSettings = { ...cachedSettings, ...payload };
        // Refresh defaults display
        if (whisperDefaultsEl && payload.ASR_MODEL) {
          whisperDefaultsEl.textContent = `（默认：${payload.ASR_MODEL} 通过 ${payload.ASR_BACKEND || '?'}）`;
        }
        if (translationDefaultsEl && payload.TRANSLATION_MODEL) {
          translationDefaultsEl.textContent = `（默认：${payload.TRANSLATION_MODEL} 通过 ${payload.TRANSLATION_BACKEND || '?'}）`;
        }
        if (settingsSaveStatusEl) {
          settingsSaveStatusEl.textContent = '保存成功';
          setTimeout(() => { settingsSaveStatusEl.textContent = ''; }, 3000);
        }
        // Refresh OpenRouter pricing since the API key may have changed.
        loadPricing();
      } else {
        alert(data.message || '保存设置失败');
      }
    } catch (err) {
      alert(`保存设置失败：${err}`);
    } finally {
      saveSettingsBtn.disabled = false;
      saveSettingsBtn.textContent = prevText;
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
        if (m.is_free) flags.push('免费');
        if (!m.supports_json_mode) flags.push('无 JSON');
        const flagStr = flags.length ? ` [${flags.join(', ')}]` : '';
        const date = fmtModelDate(m.created);
        opt.label = `${m.slug} — ${date} — 输入 ${fmtPricePer1M(m.prompt_per_token)} / 输出 ${fmtPricePer1M(m.completion_per_token)} 每百万${flagStr}`;
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
      list.innerHTML = '<div style="padding:12px;color:#9aa4b8;font-size:0.85rem;">无匹配模型。</div>';
      return;
    }
    for (const m of items) {
      const row = document.createElement('div');
      row.className = 'model-row';
      const badges = [];
      if (isRecentModel(m.created)) badges.push('<span class="badge new">新</span>');
      if (m.is_free) badges.push('<span class="badge free">免费</span>');
      if (!m.supports_json_mode) badges.push('<span class="badge no-json">无 JSON</span>');
      row.innerHTML = `
        <span class="slug">${m.slug}</span>
        <span class="date">${fmtModelDate(m.created) || '—'}</span>
        <span class="price">输入 ${fmtPricePer1M(m.prompt_per_token)} / 输出 ${fmtPricePer1M(m.completion_per_token)}</span>
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
    gpuUrlHintEl.textContent = base ? `Remote ASR: ${base}:5051 · Ollama: ${base}:11434` : '';
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
          `DeepSeek 定价：输入 ${fmtPricePerMillion(ds.prompt)} / 输出 ${fmtPricePerMillion(ds.completion)} 每百万 token`;
      } else {
        translationPricingEl.textContent = `（${model} 无定价数据）`;
      }
      return;
    }
    if (backend !== 'openrouter' || !model) {
      translationPricingEl.textContent = '';
      return;
    }
    if (!cachedPricing) {
      translationPricingEl.textContent = 'OpenRouter 定价加载中…';
      return;
    }
    const entry = cachedPricing[model];
    if (!entry) {
      translationPricingEl.textContent = `（${model} 无定价数据）`;
      return;
    }
    const inP = fmtPricePerMillion(entry.prompt);
    const outP = fmtPricePerMillion(entry.completion);
    translationPricingEl.textContent =
      `OpenRouter 定价：输入 ${inP} / 输出 ${outP} 每百万 token`;
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
    estimateHintEl.textContent = '估算中…';
    try {
      const res = await fetch(`/api/estimate?${params.toString()}`);
      const data = await res.json();
      if (seq !== estimateSeq) return;
      if (!data.success) {
        estimateHintEl.textContent = data.message || '无法估算';
        return;
      }
      const t = data.tokens || {};
      const cost = data.cost;
      const sourceLabel = {
        orig_srt: '来自已有语音识别',
        duration_heuristic: '根据时长估算',
        unknown: '大小未知',
      }[t.source] || t.source || '';
      const lineParts = [];
      if (t.segment_count) {
        lineParts.push(`约 ${t.segment_count} 行`);
      } else if (t.duration_seconds) {
        lineParts.push(`${(t.duration_seconds / 60).toFixed(1)} 分钟`);
      }
      lineParts.push(`约 ${t.input_tokens.toLocaleString()} 输入 / 约 ${t.output_tokens.toLocaleString()} 输出 token`);
      let line = lineParts.join(' · ');
      if (cost) {
        line += ` · 预估费用约 ${fmtUsd(cost.total_usd)}`;
        line += `（输入 ${fmtUsd(cost.prompt_usd)} + 输出 ${fmtUsd(cost.completion_usd)}）`;
      } else if (data.translation_backend === 'openrouter' && model) {
        line += ` · （${model} 无 OpenRouter 定价）`;
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
      fd.append('asr_backend', whisperBackendSelect.value);
    }
    if (whisperModelInput && whisperModelInput.value.trim()) {
      fd.append('asr_model', whisperModelInput.value.trim());
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
    mediaSelect.innerHTML = '<option value="">（加载中...）</option>';
    try {
      const res = await fetch('/api/media');
      const data = await res.json();
      mediaSelect.innerHTML = '';
      if (!data.files || !data.files.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '（MEDIA_DIR 中无媒体文件）';
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
      mediaSelect.innerHTML = `<option value="">（错误：${err}）</option>`;
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

  const STATUS_LABELS = {
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    awaiting_translation: '等待翻译',
  };

  function updateJobCard(el, status) {
    el.classList.remove('completed', 'failed', 'running', 'awaiting_translation');
    el.classList.add(status.status || 'running');

    el.querySelector('.meta').textContent =
      `${STATUS_LABELS[status.status] || status.status || '运行中'} · ${status.progress || 0}% · ${status.message || ''}`;
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
      cancelBtn.textContent = '取消';
      cancelBtn.className = 'danger';
      cancelBtn.onclick = async () => {
        if (!confirm('确认取消此任务？')) return;
        cancelBtn.disabled = true;
        cancelBtn.textContent = '取消中…';
        try {
          await fetch(`/api/jobs/${status.job_id}/cancel`, { method: 'POST' });
        } catch (err) {
          alert(`取消失败：${err}`);
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
      lines.push(`语音识别：${r.asr_model || r.whisper_model || '?'} (${r.asr_backend || r.whisper_backend || '?'})`);
      lines.push(
        `翻译：${r.translation_model || '?'} (${r.translation_backend || '?'})`
      );
      if (usage.total_tokens) {
        lines.push(
          `Token 用量：${usage.prompt_tokens || 0} 输入 / ${usage.completion_tokens || 0} 输出`
        );
      }
      if (status.cost) {
        lines.push(
          `预估费用：${fmtUsd(status.cost.total_usd)} ` +
          `（输入 ${fmtUsd(status.cost.prompt_usd)} + 输出 ${fmtUsd(status.cost.completion_usd)}）`
        );
      } else if (r.translation_backend === 'openrouter' && usage.total_tokens) {
        lines.push('预估费用：（此模型无定价数据）');
      }
      summary.innerHTML = lines.map((l) => `<div>${l}</div>`).join('');
      actions.appendChild(summary);

      const links = [
        ['original', '下载原始 SRT'],
        ['bilingual', '下载双语 SRT'],
      ];
      if (r.bilingual_ass) {
        links.push(['styled', '下载样式 ASS']);
      }
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

      if (status.media_file) {
        const playBtn = document.createElement('button');
        playBtn.textContent = '播放视频';
        playBtn.onclick = async () => {
          try {
            const res = await fetch(`/api/jobs/${status.job_id}/open`, { method: 'POST' });
            const data = await res.json();
            if (!data.success) {
              alert(data.message || '无法打开视频');
            }
          } catch (err) {
            alert(`打开视频失败：${err}`);
          }
        };
        actions.appendChild(playBtn);
      }
    }
  }

  function renderAwaitingTranslation(card, actions, status) {
    const r = status.result || {};
    const summary = document.createElement('div');
    summary.className = 'job-summary';
    const lines = [];
    lines.push(`语音识别：${r.asr_model || r.whisper_model || '?'} (${r.asr_backend || r.whisper_backend || '?'})`);
    lines.push(`检测到的源语言：${r.source_language || '未知'}`);
    if (r.segment_count) lines.push(`已识别 ${r.segment_count} 行字幕`);
    summary.innerHTML = lines.map((l) => `<div>${l}</div>`).join('');
    actions.appendChild(summary);

    // Allow downloading the orig SRT immediately.
    const origBtn = document.createElement('button');
    origBtn.textContent = '下载原始 SRT';
    origBtn.onclick = () => {
      window.location.href = `/api/jobs/${status.job_id}/download/original`;
    };
    actions.appendChild(origBtn);

    if (status.media_file) {
      const playBtn = document.createElement('button');
      playBtn.textContent = '播放视频';
      playBtn.onclick = async () => {
        try {
          const res = await fetch(`/api/jobs/${status.job_id}/open`, { method: 'POST' });
          const data = await res.json();
          if (!data.success) {
            alert(data.message || '无法打开视频');
          }
        } catch (err) {
          alert(`打开视频失败：${err}`);
        }
      };
      actions.appendChild(playBtn);
    }

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
        <strong>选择翻译模型</strong>
        <span class="hint job-pricing"></span>
      </div>
      <div class="row">
        <label class="inline">
          目标语言
          <input type="text" class="job-target" value="${currentTarget}" size="6">
        </label>
        <select class="job-backend">
          <option value="">默认后端</option>
          <option value="ollama">Ollama</option>
          <option value="openrouter">OpenRouter</option>
          <option value="deepseek">DeepSeek</option>
        </select>
        <input type="text" class="job-model" list="translation-model-list"
               placeholder="（默认：${defaultTranslationModel}）">
        <label class="inline">
          批次大小
          <input type="number" class="job-chunk" min="1" max="50" step="1" size="3">
        </label>
        <button type="button" class="primary job-translate-btn">翻译</button>
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
          ? `DeepSeek：输入 ${fmtPricePerMillion(ds.prompt)} / 输出 ${fmtPricePerMillion(ds.completion)} 每百万 token`
          : `（${model} 无 DeepSeek 定价）`;
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
        pricingEl.textContent = `（${model} 无 OpenRouter 定价）`;
        return;
      }
      pricingEl.textContent =
        `OpenRouter：输入 ${fmtPricePerMillion(entry.prompt)} / 输出 ${fmtPricePerMillion(entry.completion)} 每百万 token`;
    }

    let jobEstSeq = 0;
    async function refreshJobEstimate() {
      const model = modelInput.value.trim() || defaultTranslationModel;
      const seq = ++jobEstSeq;
      estimateEl.textContent = '估算中…';
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
        if (t.segment_count) parts.push(`约 ${t.segment_count} 行`);
        parts.push(`约 ${t.input_tokens.toLocaleString()} 输入 / 约 ${t.output_tokens.toLocaleString()} 输出 token`);
        if (cost) {
          parts.push(`预估费用约 ${fmtUsd(cost.total_usd)}`);
        } else if (data.translation_backend === 'openrouter' && model) {
          parts.push(`（${model} 无定价数据）`);
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
      translateBtn.textContent = '启动中…';
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
          alert(data.message || '翻译启动失败');
        }
      } catch (err) {
        alert(`翻译启动失败：${err}`);
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
        el.querySelector('.error').textContent = status.message || '任务不存在';
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
      alert('请选择一个媒体文件');
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
      alert('请选择要上传的文件');
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
      alert(`任务启动失败：${err}`);
      return;
    }
    if (!data.success) {
      alert(data.message || '任务启动失败');
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
      btn.textContent = '启动中...';
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
  if (saveSettingsBtn) {
    saveSettingsBtn.addEventListener('click', saveSettings);
  }

  loadConfig();
  loadSettings();
  loadPricing();
  loadModels();
  bindModelPicker();
  refreshMedia();
})();
