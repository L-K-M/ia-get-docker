<script>
  import { onDestroy, onMount, tick } from 'svelte';
  import {
    Button,
    Checkbox,
    ErrorBanner,
    MovableDialog,
    ProgressBar,
    TitleBar
  } from '@lkmc/system7-ui';

  const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled']);
  const UI_SETTINGS_KEY = 'ia-get-ui-settings-v1';
  const DEFAULT_POLL_INTERVAL_MS = 1200;
  const DEFAULT_RECENT_JOBS_LIMIT = 8;

  let archiveUrl = '';
  let subdir = '';
  let authUsername = '';
  let authPassword = '';

  let downloadRoot = 'loading...';
  let defaultAuthHint = 'Loading container defaults...';

  let activeJobId = null;
  let currentJob = null;
  let recentJobs = [];

  let flashMessage = '';
  let flashError = false;

  let logLines = ['Waiting for a job...'];
  let logOffset = 0;

  let isStarting = false;
  let pollTimer = null;
  let logOutput;

  let uiSettings = {
    pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
    autoScrollLogs: true,
    recentJobsLimit: DEFAULT_RECENT_JOBS_LIMIT,
    defaultSubdir: '',
    defaultUsername: ''
  };

  let settingsOpen = false;
  let draftPollIntervalMs = DEFAULT_POLL_INTERVAL_MS;
  let draftRecentJobsLimit = DEFAULT_RECENT_JOBS_LIMIT;
  let draftAutoScrollLogs = true;
  let draftDefaultSubdir = '';
  let draftDefaultUsername = '';

  $: progressValue = currentJob
    ? Math.max(0, Math.min(100, Number(currentJob.progress_percent || 0)))
    : 0;
  $: filesLabel = currentJob ? `${currentJob.completed_files} / ${currentJob.total_files}` : '0 / 0';
  $: statusLabel = currentJob ? currentJob.status : 'idle';
  $: jobIdLabel = currentJob ? currentJob.id : '-';
  $: progressLabel = currentJob?.message || 'No active download';
  $: hasRunningJob = Boolean(activeJobId && currentJob && currentJob.status === 'running');

  function clampInteger(value, min, max, fallback) {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(min, Math.min(max, parsed));
  }

  function loadStoredUiSettings() {
    try {
      const raw = window.localStorage.getItem(UI_SETTINGS_KEY);
      if (!raw) {
        return;
      }

      const parsed = JSON.parse(raw);
      uiSettings = {
        pollIntervalMs: clampInteger(parsed.pollIntervalMs, 500, 10000, DEFAULT_POLL_INTERVAL_MS),
        autoScrollLogs: Boolean(parsed.autoScrollLogs ?? true),
        recentJobsLimit: clampInteger(parsed.recentJobsLimit, 3, 25, DEFAULT_RECENT_JOBS_LIMIT),
        defaultSubdir: String(parsed.defaultSubdir || '').trim(),
        defaultUsername: String(parsed.defaultUsername || '').trim()
      };
    } catch {
      uiSettings = {
        pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
        autoScrollLogs: true,
        recentJobsLimit: DEFAULT_RECENT_JOBS_LIMIT,
        defaultSubdir: '',
        defaultUsername: ''
      };
    }
  }

  function persistUiSettings() {
    window.localStorage.setItem(UI_SETTINGS_KEY, JSON.stringify(uiSettings));
  }

  function openSettings() {
    draftPollIntervalMs = uiSettings.pollIntervalMs;
    draftRecentJobsLimit = uiSettings.recentJobsLimit;
    draftAutoScrollLogs = uiSettings.autoScrollLogs;
    draftDefaultSubdir = uiSettings.defaultSubdir;
    draftDefaultUsername = uiSettings.defaultUsername;
    settingsOpen = true;
  }

  function saveSettings() {
    uiSettings = {
      pollIntervalMs: clampInteger(draftPollIntervalMs, 500, 10000, DEFAULT_POLL_INTERVAL_MS),
      autoScrollLogs: Boolean(draftAutoScrollLogs),
      recentJobsLimit: clampInteger(draftRecentJobsLimit, 3, 25, DEFAULT_RECENT_JOBS_LIMIT),
      defaultSubdir: String(draftDefaultSubdir || '').trim(),
      defaultUsername: String(draftDefaultUsername || '').trim()
    };

    persistUiSettings();
    settingsOpen = false;
    setFlash('UI settings saved.');

    if (!subdir.trim() && uiSettings.defaultSubdir) {
      subdir = uiSettings.defaultSubdir;
    }
    if (!authUsername.trim() && uiSettings.defaultUsername) {
      authUsername = uiSettings.defaultUsername;
    }

    if (activeJobId && currentJob?.status === 'running') {
      startPolling();
    }
  }

  function resetSettings() {
    uiSettings = {
      pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
      autoScrollLogs: true,
      recentJobsLimit: DEFAULT_RECENT_JOBS_LIMIT,
      defaultSubdir: '',
      defaultUsername: ''
    };
    persistUiSettings();
    draftPollIntervalMs = uiSettings.pollIntervalMs;
    draftRecentJobsLimit = uiSettings.recentJobsLimit;
    draftAutoScrollLogs = uiSettings.autoScrollLogs;
    draftDefaultSubdir = '';
    draftDefaultUsername = '';
  }

  function setFlash(message, isError = false) {
    flashMessage = message;
    flashError = isError;
  }

  function resetLogs(message = 'Waiting for logs...') {
    logOffset = 0;
    logLines = [message];
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const body = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(body.error || `Request failed (${response.status})`);
    }

    return body;
  }

  async function refreshJobsList() {
    const payload = await api('/api/jobs');
    activeJobId = payload.active_job_id || null;
    recentJobs = payload.jobs || [];

    if (!currentJob && recentJobs.length > 0) {
      currentJob = recentJobs[0];
    }

    return payload;
  }

  async function loadJob(jobId) {
    const payload = await api(`/api/jobs/${jobId}`);
    currentJob = payload.job;
    return payload.job;
  }

  async function loadLogs(jobId) {
    const payload = await api(`/api/jobs/${jobId}/logs?offset=${logOffset}`);
    const lines = payload.lines || [];

    if (lines.length > 0) {
      if (logLines.length === 1 && logLines[0].startsWith('Waiting for')) {
        logLines = [];
      }

      logLines = [...logLines, ...lines];
      await tick();

      if (uiSettings.autoScrollLogs && logOutput) {
        logOutput.scrollTop = logOutput.scrollHeight;
      }
    }

    logOffset = payload.next_offset || logOffset;
  }

  async function pollActiveJob() {
    if (!activeJobId) {
      return;
    }

    try {
      const [job] = await Promise.all([loadJob(activeJobId), loadLogs(activeJobId)]);
      await refreshJobsList();

      if (TERMINAL_STATES.has(job.status)) {
        setFlash(`Job ${job.id} finished with status: ${job.status}`);
        stopPolling();
        activeJobId = null;
      }
    } catch (error) {
      setFlash(error.message, true);
      stopPolling();
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(pollActiveJob, uiSettings.pollIntervalMs);
    void pollActiveJob();
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function startDownload() {
    const url = archiveUrl.trim();
    if (!url) {
      setFlash('Archive URL is required.', true);
      return;
    }

    if (hasRunningJob) {
      setFlash('A job is already running.', true);
      return;
    }

    isStarting = true;
    setFlash('Starting download...');

    const requestBody = {
      url,
      subdir: subdir.trim()
    };

    if (authUsername.trim().length > 0 || authPassword.length > 0) {
      requestBody.username = authUsername.trim();
      requestBody.password = authPassword;
    }

    try {
      const payload = await api('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });

      const job = payload.job;
      activeJobId = job.id;
      currentJob = job;
      resetLogs();
      setFlash(`Started job ${job.id}`);
      authPassword = '';

      startPolling();
      await refreshJobsList();
    } catch (error) {
      setFlash(error.message, true);
    } finally {
      isStarting = false;
    }
  }

  async function cancelDownload() {
    if (!activeJobId) {
      return;
    }

    try {
      await api(`/api/jobs/${activeJobId}/cancel`, { method: 'POST' });
      setFlash('Cancellation signal sent.');
      await pollActiveJob();
    } catch (error) {
      setFlash(error.message, true);
    }
  }

  function applyConfig(config) {
    downloadRoot = config.download_dir || '/downloads';
    const hasDefaultUser = Boolean(config.default_username);
    const hasDefaultPass = Boolean(config.has_default_password);

    if (!hasDefaultUser && !hasDefaultPass) {
      defaultAuthHint = 'No default credentials configured. Public archives only unless credentials are entered.';
      return;
    }

    const userState = hasDefaultUser ? `set (${config.default_username})` : 'not set';
    const passState = hasDefaultPass ? 'set' : 'not set';
    defaultAuthHint = `Container defaults: username ${userState}, password ${passState}.`;
  }

  onMount(async () => {
    loadStoredUiSettings();

    if (uiSettings.defaultSubdir) {
      subdir = uiSettings.defaultSubdir;
    }
    if (uiSettings.defaultUsername) {
      authUsername = uiSettings.defaultUsername;
    }

    try {
      const config = await api('/api/config');
      applyConfig(config);

      const jobsPayload = await refreshJobsList();
      if (jobsPayload.active_job_id) {
        activeJobId = jobsPayload.active_job_id;
        resetLogs();
        setFlash(`Resuming active job ${activeJobId}`);
        startPolling();
      }
    } catch (error) {
      setFlash(error.message, true);
      defaultAuthHint = 'Unable to load container defaults.';
    }
  });

  onDestroy(() => {
    stopPolling();
  });
</script>

<div class="desktop">
  <div class="window s7-root app-window">
    <TitleBar title="ia-get" />

    <main class="content">
      {#if flashMessage && flashError}
        <ErrorBanner message={flashMessage} onclose={() => setFlash('', false)} />
      {:else if flashMessage}
        <p class="status-message">{flashMessage}</p>
      {/if}

      <section class="panel form-panel">
        <h2>Start download</h2>

        <label>
          Archive URL
          <input
            class="s7-input"
            type="url"
            placeholder="https://archive.org/details/En-ROMs"
            bind:value={archiveUrl}
          />
        </label>

        <label>
          Optional subdirectory
          <input class="s7-input" type="text" placeholder="Defaults to archive identifier" bind:value={subdir} />
        </label>

        <label>
          Archive.org username (optional)
          <input class="s7-input" type="text" placeholder="Needed for restricted items" bind:value={authUsername} />
        </label>

        <label>
          Archive.org password (optional)
          <input
            class="s7-input"
            type="password"
            placeholder="Used only for this job"
            bind:value={authPassword}
          />
        </label>

        <p class="small-text">{defaultAuthHint}</p>
        <p class="small-text">Download root: <code>{downloadRoot}</code></p>

        <div class="actions">
          <Button onclick={startDownload} disabled={isStarting || hasRunningJob}>
            {isStarting ? 'Starting...' : 'Start download'}
          </Button>
          <Button onclick={cancelDownload} disabled={!hasRunningJob}>Cancel running job</Button>
          <Button onclick={openSettings}>Settings</Button>
        </div>
      </section>

      <section class="panel status-panel">
        <h2>Current job</h2>
        <div class="stats-grid">
          <p>Status: <strong>{statusLabel}</strong></p>
          <p>ID: <strong>{jobIdLabel}</strong></p>
          <p>Files: <strong>{filesLabel}</strong></p>
        </div>
        <ProgressBar
          value={progressValue}
          max={100}
          height={16}
          title={`Progress ${progressValue}%`}
          ariaLabel="Download progress"
        />
        <p class="small-text">{progressLabel}</p>
      </section>

      <section class="panel logs-panel">
        <h2>Live logs</h2>
        <pre class="log-output" bind:this={logOutput}>{logLines.join('\n')}</pre>
      </section>

      <section class="panel jobs-panel">
        <h2>Recent jobs</h2>
        {#if recentJobs.length === 0}
          <p class="small-text">No jobs yet.</p>
        {:else}
          <ul class="jobs-list">
            {#each recentJobs.slice(0, uiSettings.recentJobsLimit) as job}
              <li>
                <span><strong>{job.id}</strong> - {job.status}</span>
                <span>{job.identifier}</span>
                <span>{job.completed_files}/{job.total_files} files</span>
                <span>{job.auth_enabled ? `auth: ${job.auth_username || 'set'}` : 'public'}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    </main>
  </div>
</div>

{#if settingsOpen}
  <MovableDialog title="UI Settings" onclose={() => (settingsOpen = false)} width="390px">
    <div class="settings-form">
      <div class="s7-form-group">
        <label for="poll-interval">Poll interval (ms)</label>
        <input
          id="poll-interval"
          class="s7-input"
          type="number"
          min="500"
          max="10000"
          step="100"
          bind:value={draftPollIntervalMs}
        />
      </div>

      <div class="s7-form-group">
        <label for="recent-jobs-limit">Recent jobs shown</label>
        <input
          id="recent-jobs-limit"
          class="s7-input"
          type="number"
          min="3"
          max="25"
          step="1"
          bind:value={draftRecentJobsLimit}
        />
      </div>

      <div class="s7-form-group">
        <label for="default-subdir">Default subdirectory</label>
        <input
          id="default-subdir"
          class="s7-input"
          type="text"
          placeholder="Optional default subdirectory"
          bind:value={draftDefaultSubdir}
        />
      </div>

      <div class="s7-form-group">
        <label for="default-username">Default archive username</label>
        <input
          id="default-username"
          class="s7-input"
          type="text"
          placeholder="Prefill only (no password stored)"
          bind:value={draftDefaultUsername}
        />
      </div>

      <Checkbox
        checked={draftAutoScrollLogs}
        label="Auto-scroll live logs"
        onchange={(checked) => (draftAutoScrollLogs = checked)}
      />

      <p class="small-text">
        Settings are saved in your browser and apply only to this UI. Passwords are never stored.
      </p>

      <div class="actions settings-actions">
        <Button onclick={resetSettings}>Reset</Button>
        <Button onclick={() => (settingsOpen = false)}>Cancel</Button>
        <Button variant="primary" onclick={saveSettings}>Save</Button>
      </div>
    </div>
  </MovableDialog>
{/if}
