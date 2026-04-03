<script>
  import { onDestroy, onMount, tick } from 'svelte';
  import {
    Button,
    Checkbox,
    DataTable,
    ErrorBanner,
    MovableDialog,
    ProgressBar,
    TitleBar
  } from '@lkmc/system7-ui';

  const UI_SETTINGS_KEY = 'ia-get-ui-settings-v3';
  const API_KEY_STORAGE_KEY = 'ia-get-api-key';
  const DEFAULT_POLL_INTERVAL_MS = 1200;
  const DEFAULT_RECENT_JOBS_LIMIT = 12;

  let jobs = [];
  let activeJobId = null;
  let selectedJobId = null;
  let queueStats = {
    total_jobs: 0,
    queued_jobs: 0,
    retry_wait_jobs: 0,
    pending_jobs: 0,
    running_jobs: 0,
    completed_jobs: 0,
    failed_jobs: 0,
    cancelled_jobs: 0,
    terminal_jobs: 0,
    progress_percent: 0
  };

  let isLoading = true;
  let flashMessage = '';
  let flashError = false;
  let pollTimer = null;
  let pollInFlight = false;
  let clearingFinished = false;
  let cancellingJobIds = new Set();
  let retryingJobIds = new Set();

  let authRequired = false;
  let apiKey = '';
  let showApiKeyDialog = false;
  let draftApiKey = '';
  let apiKeyError = '';

  let downloadRoot = '/downloads';
  let defaultAuthHint = 'Loading container defaults...';
  let containerDefaultUsername = '';
  let hasContainerDefaultPassword = false;
  let hasContainerAuthDefaults = false;

  let showAddDialog = false;
  let addUrl = '';
  let addSubdir = '';
  let addUsername = '';
  let addPassword = '';
  let forceAuthInput = false;
  let restartingJobId = null;
  let addError = '';
  let addSubmitting = false;

  let detailLogs = ['Select a download to view details and logs.'];
  let detailLogOffset = 0;
  let detailLogOutput;
  let addUrlInput;

  let uiSettings = {
    pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
    autoScrollLogs: true,
    recentJobsLimit: DEFAULT_RECENT_JOBS_LIMIT,
    defaultSubdir: '',
    defaultUsername: '',
    retryDelayMinutes: 10,
    retryMaxAttempts: 3
  };

  let settingsOpen = false;
  let draftPollIntervalMs = DEFAULT_POLL_INTERVAL_MS;
  let draftRecentJobsLimit = DEFAULT_RECENT_JOBS_LIMIT;
  let draftDefaultSubdir = '';
  let draftDefaultUsername = '';
  let draftRetryDelayMinutes = 10;
  let draftRetryMaxAttempts = 3;

  const columns = [
    { key: 'name', label: 'Name', width: '31%', className: 'col-name' },
    { key: 'progress', label: 'Progress', width: '41%', className: 'col-progress' },
    { key: 'files', label: 'Files', width: '12%', className: 'col-files' },
    { key: 'actions', label: 'Actions', width: '16%', className: 'col-actions' }
  ];

  $: selectedJob = jobs.find((item) => item.id === selectedJobId) || null;
  $: hasFinishedJobs = jobs.some((item) => ['completed', 'failed', 'cancelled'].includes(item.status));
  $: visibleJobs = jobs.slice(0, uiSettings.recentJobsLimit);
  $: hiddenJobCount = Math.max(0, jobs.length - visibleJobs.length);
  $: queuedJobsCount = Number(queueStats.queued_jobs || 0);
  $: retryWaitJobsCount = Number(queueStats.retry_wait_jobs || 0);
  $: pendingJobsCount = Number(
    queueStats.pending_jobs ?? queuedJobsCount + retryWaitJobsCount
  );
  $: waitingSummary =
    retryWaitJobsCount > 0
      ? `${pendingJobsCount} waiting (${queuedJobsCount} queued, ${retryWaitJobsCount} retry wait)`
      : `${pendingJobsCount} waiting`;
  $: footerSummary =
    queueStats.total_jobs === 0
      ? 'No downloads queued yet.'
      : `${queueStats.terminal_jobs}/${queueStats.total_jobs} finished | ${queueStats.running_jobs} running | ${waitingSummary}`;

  function setFlash(message, isError = false) {
    flashMessage = message;
    flashError = isError;
  }

  function clampInteger(value, min, max, fallback) {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(min, Math.min(max, parsed));
  }

  function resetDetailLogs(message = 'Select a download to view details and logs.') {
    detailLogs = [message];
    detailLogOffset = 0;
  }

  function focusAddUrlInput() {
    tick().then(() => {
      setTimeout(() => addUrlInput?.focus(), 0);
    });
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
        recentJobsLimit: clampInteger(parsed.recentJobsLimit, 3, 50, DEFAULT_RECENT_JOBS_LIMIT),
        defaultSubdir: String(parsed.defaultSubdir || '').trim(),
        defaultUsername: String(parsed.defaultUsername || '').trim(),
        retryDelayMinutes: clampInteger(parsed.retryDelayMinutes, 0, 1440, 10),
        retryMaxAttempts: clampInteger(parsed.retryMaxAttempts, 0, 100, 3)
      };
    } catch {
      uiSettings = {
        pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
        autoScrollLogs: true,
        recentJobsLimit: DEFAULT_RECENT_JOBS_LIMIT,
        defaultSubdir: '',
        defaultUsername: '',
        retryDelayMinutes: 10,
        retryMaxAttempts: 3
      };
    }
  }

  function persistUiSettings() {
    window.localStorage.setItem(UI_SETTINGS_KEY, JSON.stringify(uiSettings));
  }

  function openSettings() {
    draftPollIntervalMs = uiSettings.pollIntervalMs;
    draftRecentJobsLimit = uiSettings.recentJobsLimit;
    draftDefaultSubdir = uiSettings.defaultSubdir;
    draftDefaultUsername = uiSettings.defaultUsername;
    draftRetryDelayMinutes = uiSettings.retryDelayMinutes;
    draftRetryMaxAttempts = uiSettings.retryMaxAttempts;
    settingsOpen = true;
  }

  function saveSettings() {
    uiSettings = {
      pollIntervalMs: clampInteger(draftPollIntervalMs, 500, 10000, DEFAULT_POLL_INTERVAL_MS),
      autoScrollLogs: uiSettings.autoScrollLogs,
      recentJobsLimit: clampInteger(draftRecentJobsLimit, 3, 50, DEFAULT_RECENT_JOBS_LIMIT),
      defaultSubdir: String(draftDefaultSubdir || '').trim(),
      defaultUsername: String(draftDefaultUsername || '').trim(),
      retryDelayMinutes: clampInteger(draftRetryDelayMinutes, 0, 1440, 10),
      retryMaxAttempts: clampInteger(draftRetryMaxAttempts, 0, 100, 3)
    };

    persistUiSettings();
    settingsOpen = false;
    startPolling();
  }

  function setAutoScrollLogs(enabled) {
    uiSettings = {
      ...uiSettings,
      autoScrollLogs: Boolean(enabled)
    };

    persistUiSettings();

    if (uiSettings.autoScrollLogs && detailLogOutput) {
      detailLogOutput.scrollTop = detailLogOutput.scrollHeight;
    }
  }

  async function api(path, options = {}) {
    if (apiKey) {
      options.headers = { ...options.headers, 'X-API-Key': apiKey };
    }
    const response = await fetch(path, options);

    if (response.status === 401 && authRequired) {
      showApiKeyPrompt();
      throw new Error('API key required. Enter a valid key to continue.');
    }

    const body = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(body.error || `Request failed (${response.status})`);
    }

    return body;
  }

  function showApiKeyPrompt() {
    draftApiKey = '';
    apiKeyError = '';
    showApiKeyDialog = true;
    stopPolling();
  }

  async function submitApiKey() {
    const candidateKey = draftApiKey.trim();
    if (!candidateKey) {
      apiKeyError = 'Please enter an API key.';
      return;
    }

    try {
      const response = await fetch('/api/config', {
        headers: { 'X-API-Key': candidateKey }
      });
      if (response.status === 401) {
        apiKeyError = 'Invalid API key.';
        return;
      }
      if (!response.ok) {
        apiKeyError = `Server error (${response.status}).`;
        return;
      }

      apiKey = candidateKey;
      window.sessionStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
      showApiKeyDialog = false;
      apiKeyError = '';
      draftApiKey = '';

      const config = await response.json();
      applyConfig(config);
      await pollData();
      startPolling();
    } catch (error) {
      apiKeyError = error.message || 'Unable to reach server.';
    }
  }

  function handleApiKeySubmit(event) {
    event.preventDefault();
    void submitApiKey();
  }

  function applyConfig(config) {
    downloadRoot = config.download_dir || '/downloads';
    containerDefaultUsername = String(config.default_username || '').trim();
    hasContainerDefaultPassword = Boolean(config.has_default_password);
    const hasDefaultUser = Boolean(containerDefaultUsername);
    const hasDefaultPass = hasContainerDefaultPassword;
    hasContainerAuthDefaults = hasDefaultUser && hasDefaultPass;

    if (hasContainerAuthDefaults) {
      defaultAuthHint = `Using container defaults for authenticated downloads (${containerDefaultUsername}).`;
      return;
    }

    if (!hasDefaultUser && !hasDefaultPass) {
      defaultAuthHint = 'No container auth defaults configured.';
      return;
    }

    const userState = hasDefaultUser ? `set (${containerDefaultUsername})` : 'not set';
    const passState = hasDefaultPass ? 'set' : 'not set';
    defaultAuthHint = `Container defaults: username ${userState}, password ${passState}.`;
  }

  async function refreshJobsList() {
    const payload = await api('/api/jobs');
    jobs = payload.jobs || [];
    activeJobId = payload.active_job_id || null;
    queueStats = payload.queue_stats || queueStats;

    let selectionChanged = false;

    if (selectedJobId && !jobs.find((item) => item.id === selectedJobId)) {
      selectedJobId = null;
      selectionChanged = true;
    }

    if (!selectedJobId && jobs.length > 0) {
      selectedJobId = jobs[0].id;
      selectionChanged = true;
    }

    if (!selectedJobId) {
      resetDetailLogs();
    }

    isLoading = false;
    return selectionChanged;
  }

  function formatTime(isoDate) {
    if (!isoDate) {
      return '--';
    }

    const date = new Date(isoDate);
    if (Number.isNaN(date.getTime())) {
      return '--';
    }

    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}Z`;
  }

  function handleRowKeydown(event, jobId) {
    if (event.target !== event.currentTarget) {
      return;
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      void handleRowClick(jobId);
    }
  }

  function describeStatus(job) {
    if (job.status === 'queued' && job.queue_position) {
      return `queued #${job.queue_position}`;
    }

    if (job.status === 'retry_wait') {
      const retryAt = job.next_retry_at ? new Date(job.next_retry_at) : null;
      if (retryAt && !Number.isNaN(retryAt.getTime())) {
        const remainingMinutes = Math.max(0, Math.ceil((retryAt.getTime() - Date.now()) / 60000));
        return `retry in ${remainingMinutes}m`;
      }
      return 'retry wait';
    }

    return job.status;
  }

  function getJobProgressNumber(job) {
    if (!job) {
      return 0;
    }

    if (job.status === 'completed') {
      return 100;
    }

    if (job.total_files > 0) {
      return Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
    }

    return 0;
  }

  function describeProgress(job) {
    if (job.status === 'queued') {
      return job.queue_position ? `waiting #${job.queue_position}` : 'waiting';
    }

    if (job.status === 'retry_wait') {
      return 'retry waiting';
    }

    if (job.status === 'running') {
      const percent = getJobProgressNumber(job);
      if (percent > 0) {
        return `${percent.toFixed(1)}%`;
      }
      return 'running';
    }

    if (job.status === 'completed') {
      return '100%';
    }

    if (job.status === 'cancelled') {
      return 'cancelled';
    }

    if (job.status === 'failed') {
      return 'failed';
    }

    return '--';
  }

  function canCancel(job) {
    if (!job) {
      return false;
    }

    return job.status === 'queued' || job.status === 'running' || job.status === 'retry_wait';
  }

  function markJobActionPending(setRef, jobId, isPending) {
    const next = new Set(setRef);
    if (isPending) {
      next.add(jobId);
    } else {
      next.delete(jobId);
    }
    return next;
  }

  async function loadSelectedJobLogs(reset = false) {
    if (!selectedJobId) {
      return;
    }

    if (reset) {
      detailLogs = [];
      detailLogOffset = 0;
    }

    const logsPayload = await api(`/api/jobs/${selectedJobId}/logs?offset=${detailLogOffset}`);
    const newLines = logsPayload.lines || [];

    if (reset) {
      detailLogs = newLines.length > 0 ? [...newLines] : ['No logs yet.'];
    } else if (newLines.length > 0) {
      if (detailLogs.length === 1 && detailLogs[0] === 'No logs yet.') {
        detailLogs = [];
      }
      detailLogs = [...detailLogs, ...newLines];
    } else if (detailLogs.length === 0) {
      detailLogs = ['No logs yet.'];
    }

    detailLogOffset = logsPayload.next_offset || detailLogOffset;

    if (uiSettings.autoScrollLogs && newLines.length > 0 && detailLogOutput) {
      await tick();
      detailLogOutput.scrollTop = detailLogOutput.scrollHeight;
    }
  }

  async function cancelJob(jobId) {
    await api(`/api/jobs/${jobId}/cancel`, { method: 'POST' });

    const selectionChanged = await refreshJobsList();
    if (selectedJobId) {
      await loadSelectedJobLogs(selectionChanged || selectedJobId === jobId);
    }
  }

  async function handleRowCancel(event, jobId) {
    event.preventDefault();
    event.stopPropagation();

    if (cancellingJobIds.has(jobId)) {
      return;
    }

    cancellingJobIds = markJobActionPending(cancellingJobIds, jobId, true);

    try {
      await cancelJob(jobId);
    } catch (error) {
      setFlash(error.message, true);
    } finally {
      cancellingJobIds = markJobActionPending(cancellingJobIds, jobId, false);
    }
  }

  async function queueDownload(requestBody) {
    const payload = await api('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });

    selectedJobId = payload.job.id;
    await refreshJobsList();
    await loadSelectedJobLogs(true);
  }

  async function restartJob(jobId, extraBody = {}) {
    const payload = await api(`/api/jobs/${jobId}/restart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        retry_delay_minutes: uiSettings.retryDelayMinutes,
        max_retry_attempts: uiSettings.retryMaxAttempts,
        ...extraBody
      })
    });

    selectedJobId = payload.job.id;
    await refreshJobsList();
    await loadSelectedJobLogs(true);
  }

  async function handleRowRetry(event, job) {
    event.preventDefault();
    event.stopPropagation();

    if (!job || (job.status !== 'failed' && job.status !== 'cancelled')) {
      return;
    }

    if (retryingJobIds.has(job.id)) {
      return;
    }

    if (
      job.auth_enabled &&
      (!hasContainerDefaultPassword || containerDefaultUsername !== (job.auth_username || ''))
    ) {
      restartingJobId = job.id;
      forceAuthInput = true;
      addUrl = job.url;
      addSubdir = job.output_subdir || '';
      addUsername = job.auth_username || '';
      addPassword = '';
      addError = 'Enter password, then click Add to Queue to retry this download.';
      showAddDialog = true;
      focusAddUrlInput();
      return;
    }

    retryingJobIds = markJobActionPending(retryingJobIds, job.id, true);

    try {
      await restartJob(job.id);
    } catch (error) {
      setFlash(error.message, true);
    } finally {
      retryingJobIds = markJobActionPending(retryingJobIds, job.id, false);
    }
  }

  async function handleRowClick(jobId) {
    if (selectedJobId !== jobId) {
      selectedJobId = jobId;
      await loadSelectedJobLogs(true);
      return;
    }

    await loadSelectedJobLogs(false);
  }

  function closeAddDownloadDialog() {
    showAddDialog = false;
    forceAuthInput = false;
    restartingJobId = null;
  }

  function openAddDownloadDialog() {
    forceAuthInput = false;
    restartingJobId = null;
    addUrl = '';
    addSubdir = uiSettings.defaultSubdir || '';
    addUsername = hasContainerAuthDefaults ? '' : uiSettings.defaultUsername || containerDefaultUsername || '';
    addPassword = '';
    addError = '';
    showAddDialog = true;
    focusAddUrlInput();
  }

  async function submitNewDownload() {
    const url = addUrl.trim();
    if (!url) {
      addError = 'Archive URL is required.';
      return;
    }

    addSubmitting = true;
    addError = '';

    try {
      if (restartingJobId) {
        const extraBody = {};
        if (addUsername.trim().length > 0 || addPassword.length > 0) {
          extraBody.username = addUsername.trim();
          extraBody.password = addPassword;
        }
        await restartJob(restartingJobId, extraBody);
      } else {
        const requestBody = {
          url,
          subdir: addSubdir.trim(),
          retry_delay_minutes: uiSettings.retryDelayMinutes,
          max_retry_attempts: uiSettings.retryMaxAttempts
        };

        if ((!hasContainerAuthDefaults || forceAuthInput) && (addUsername.trim().length > 0 || addPassword.length > 0)) {
          requestBody.username = addUsername.trim();
          requestBody.password = addPassword;
        }

        await queueDownload(requestBody);
      }
      closeAddDownloadDialog();
      addPassword = '';
    } catch (error) {
      addError = error.message;
    } finally {
      addSubmitting = false;
    }
  }

  function handleAddDialogSubmit(event) {
    event.preventDefault();
    void submitNewDownload();
  }

  async function pollData() {
    if (pollInFlight) {
      return;
    }

    pollInFlight = true;

    try {
      const selectionChanged = await refreshJobsList();
      if (selectedJobId) {
        await loadSelectedJobLogs(selectionChanged);
      }
    } catch (error) {
      setFlash(error.message, true);
    } finally {
      pollInFlight = false;
      if (isLoading) {
        isLoading = false;
      }
    }
  }

  async function clearFinishedJobs() {
    if (clearingFinished || !hasFinishedJobs) {
      return;
    }

    clearingFinished = true;
    try {
      await api('/api/jobs/clear-finished', { method: 'POST' });
      const selectionChanged = await refreshJobsList();
      if (selectedJobId) {
        await loadSelectedJobLogs(selectionChanged);
      } else {
        resetDetailLogs();
      }
    } catch (error) {
      setFlash(error.message, true);
    } finally {
      clearingFinished = false;
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
      void pollData();
    }, uiSettings.pollIntervalMs);
  }

  function stopPolling() {
    if (!pollTimer) {
      return;
    }

    clearInterval(pollTimer);
    pollTimer = null;
  }

  onMount(async () => {
    loadStoredUiSettings();

    try {
      const authRes = await fetch('/api/auth-status');
      const authData = await authRes.json().catch(() => ({}));
      authRequired = Boolean(authData.auth_required);
    } catch {
      authRequired = false;
    }

    if (authRequired) {
      const storedKey = window.sessionStorage.getItem(API_KEY_STORAGE_KEY) || '';
      if (storedKey) {
        apiKey = storedKey;
      } else {
        showApiKeyPrompt();
        return;
      }
    }

    try {
      const config = await api('/api/config');
      applyConfig(config);
    } catch (error) {
      if (authRequired && !apiKey) {
        return;
      }
      setFlash(error.message, true);
      defaultAuthHint = 'Unable to load container defaults.';
    }

    await pollData();
    startPolling();
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
      {/if}

      <div class="toolbar">
        <Button onclick={openAddDownloadDialog}>Add Download...</Button>
        <Button onclick={clearFinishedJobs} disabled={!hasFinishedJobs || clearingFinished}>
          {clearingFinished ? 'Clearing...' : 'Clear Inactive'}
        </Button>
        <div class="toolbar-spacer"></div>
        <Button onclick={openSettings}>Settings</Button>
      </div>

      <div class="workspace">
        <section class="table-pane">
          <DataTable
            class="jobs-table"
            columns={columns}
            loading={isLoading && jobs.length === 0}
            loadingText="Loading queue..."
            empty={!isLoading && jobs.length === 0}
            emptyText="No downloads queued yet. Use 'Add Download...' to start."
            emptyColspan={4}
          >
            {#each visibleJobs as job}
              <tr
                class="job-row"
                class:selected={selectedJobId === job.id}
                role="button"
                tabindex="0"
                aria-label={`Select ${job.identifier}`}
                aria-pressed={selectedJobId === job.id}
                onclick={() => handleRowClick(job.id)}
                onkeydown={(event) => handleRowKeydown(event, job.id)}
              >
                <td class="col-name">{job.identifier}</td>
                <td class="col-progress">
                  <div class="row-progressbar">
                    <ProgressBar
                      value={getJobProgressNumber(job)}
                      max={100}
                      height={16}
                      title={describeProgress(job)}
                      ariaLabel="Download progress"
                    />
                  </div>
                </td>
                <td class="col-files">{job.completed_files}/{job.total_files}</td>
                <td class="col-actions">
                  {#if job.status === 'failed' || job.status === 'cancelled'}
                    <Button
                      onclick={(event) => handleRowRetry(event, job)}
                      disabled={retryingJobIds.has(job.id)}
                    >
                      {#if retryingJobIds.has(job.id)}
                        {job.status === 'cancelled' ? 'Restarting...' : 'Retrying...'}
                      {:else}
                        {job.status === 'cancelled' ? 'Restart' : 'Try Again'}
                      {/if}
                    </Button>
                  {:else}
                    <Button
                      onclick={(event) => handleRowCancel(event, job.id)}
                      disabled={!canCancel(job) || cancellingJobIds.has(job.id)}
                    >
                      {cancellingJobIds.has(job.id) ? 'Cancelling...' : 'Cancel'}
                    </Button>
                  {/if}
                </td>
              </tr>
            {/each}
          </DataTable>

          {#if hiddenJobCount > 0}
            <p class="table-hidden-indicator">
              {hiddenJobCount} older {hiddenJobCount === 1 ? 'download is' : 'downloads are'} hidden. Increase
              "Rows shown in table" in Settings to view more.
            </p>
          {/if}
        </section>

        <aside class="details-pane">
          <h2>Download Details</h2>

          {#if selectedJob}
            <div class="details-grid">
              <p><strong>Status:</strong> {describeStatus(selectedJob)}</p>
              <p><strong>Name:</strong> {selectedJob.identifier}</p>
              <p><strong>URL:</strong> {selectedJob.url}</p>
              <p><strong>Output:</strong> {selectedJob.output_subdir}</p>
              <p><strong>Progress:</strong> {describeProgress(selectedJob)}</p>
              <p><strong>Files:</strong> {selectedJob.completed_files}/{selectedJob.total_files}</p>
              <p><strong>Retry Delay:</strong> {selectedJob.retry_delay_minutes || 0} minute(s)</p>
              <p><strong>Retry Max:</strong> {selectedJob.max_retry_attempts || 0}</p>
              <p><strong>Retry Count:</strong> {selectedJob.retry_count || 0}</p>
              <p><strong>Next Retry:</strong> {formatTime(selectedJob.next_retry_at)}</p>
              <p><strong>Created:</strong> {formatTime(selectedJob.created_at)}</p>
              <p><strong>Started:</strong> {formatTime(selectedJob.started_at)}</p>
              <p><strong>Finished:</strong> {formatTime(selectedJob.finished_at)}</p>
              <p><strong>Auth:</strong> {selectedJob.auth_enabled ? selectedJob.auth_username : 'public'}</p>
              <p><strong>Message:</strong> {selectedJob.message || '--'}</p>
            </div>

            <ProgressBar
              value={getJobProgressNumber(selectedJob)}
              max={100}
              height={16}
              title="Selected job progress"
              ariaLabel="Selected job progress"
            />

            <pre class="details-logs" bind:this={detailLogOutput}>{detailLogs.join('\n')}</pre>
            <div class="logs-controls">
              <Checkbox
                checked={uiSettings.autoScrollLogs}
                label="Auto-scroll logs"
                onchange={setAutoScrollLogs}
              />
            </div>
          {:else}
            <p class="hint">Select a download to view details and logs.</p>
          {/if}
        </aside>
      </div>
    </main>

    <footer class="footer-bar">
      <div class="footer-progress">
        <ProgressBar
          value={Number(queueStats.progress_percent || 0)}
          max={100}
          height={16}
          title="Overall queue progress"
          ariaLabel="Overall queue progress"
        />
      </div>
      <span class="footer-text">{footerSummary}</span>
    </footer>
  </div>
</div>

{#if showAddDialog}
  <MovableDialog title="Queue Download" onclose={closeAddDownloadDialog} width="540px">
    <form class="dialog-form" onsubmit={handleAddDialogSubmit}>
      <div class="s7-form-group">
        <label for="add-url">Archive URL</label>
        <input
          id="add-url"
          class="s7-input"
          type="url"
          placeholder="https://archive.org/details/Something"
          bind:this={addUrlInput}
          bind:value={addUrl}
        />
      </div>

      <div class="s7-form-group">
        <label for="add-subdir">Subdirectory (optional)</label>
        <input
          id="add-subdir"
          class="s7-input"
          type="text"
          placeholder="Defaults to archive identifier"
          bind:value={addSubdir}
        />
      </div>

      {#if !hasContainerAuthDefaults || forceAuthInput}
        <div class="s7-form-group">
          <label for="add-username">Archive.org username (optional)</label>
          <input id="add-username" class="s7-input" type="text" bind:value={addUsername} />
        </div>

        <div class="s7-form-group">
          <label for="add-password">Archive.org password (optional)</label>
          <input id="add-password" class="s7-input" type="password" bind:value={addPassword} />
        </div>
      {/if}

      <p class="hint">{defaultAuthHint}</p>
      <p class="hint">Download root: <code>{downloadRoot}</code></p>

      {#if addError}
        <p class="dialog-error">{addError}</p>
      {/if}

      <div class="dialog-actions">
        <Button type="button" onclick={closeAddDownloadDialog}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={addSubmitting}>
          {addSubmitting ? 'Queueing...' : 'Add to Queue'}
        </Button>
      </div>
    </form>
  </MovableDialog>
{/if}

{#if settingsOpen}
  <MovableDialog title="UI Settings" onclose={() => (settingsOpen = false)} width="420px">
    <div class="settings-form">
      <div class="s7-form-group">
        <label for="poll-interval" class="settings-label">
          Poll interval (ms)
          <span
            class="balloon-help"
            title="How often the UI refreshes queue and log data. Lower values feel more live but poll more often."
            >?</span>
        </label>
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
        <label for="recent-jobs-limit" class="settings-label">
          Rows shown in table
          <span
            class="balloon-help"
            title="Maximum queue rows shown at once. Older rows are still kept and available through polling updates."
            >?</span>
        </label>
        <input
          id="recent-jobs-limit"
          class="s7-input"
          type="number"
          min="3"
          max="50"
          step="1"
          bind:value={draftRecentJobsLimit}
        />
      </div>

      <div class="s7-form-group">
        <label for="default-subdir" class="settings-label">
          Default subdirectory
          <span
            class="balloon-help"
            title="Pre-fills the Subdirectory field for new downloads. Leave blank to default to the archive identifier."
            >?</span>
        </label>
        <input
          id="default-subdir"
          class="s7-input"
          type="text"
          placeholder="Optional default subdirectory"
          bind:value={draftDefaultSubdir}
        />
      </div>

      {#if !hasContainerAuthDefaults}
        <div class="s7-form-group">
          <label for="default-username" class="settings-label">
            Default archive username
            <span
              class="balloon-help"
              title="Pre-fills the username field for new downloads when container-wide auth is not configured."
              >?</span>
          </label>
          <input
            id="default-username"
            class="s7-input"
            type="text"
            placeholder="Prefill only (no password stored)"
            bind:value={draftDefaultUsername}
          />
        </div>
      {/if}

      <div class="s7-form-group">
        <label for="retry-delay-minutes" class="settings-label">
          Auto-retry delay (minutes, 0 = off)
          <span
            class="balloon-help"
            title="Wait time before retrying a failed download. Set to 0 to disable automatic retries."
            >?</span>
        </label>
        <input
          id="retry-delay-minutes"
          class="s7-input"
          type="number"
          min="0"
          max="1440"
          step="1"
          bind:value={draftRetryDelayMinutes}
        />
      </div>

      <div class="s7-form-group">
        <label for="retry-max-attempts" class="settings-label">
          Auto-retry max times
          <span
            class="balloon-help"
            title="Maximum automatic retry attempts after failures. Set to 0 for unlimited retries."
            >?</span>
        </label>
        <input
          id="retry-max-attempts"
          class="s7-input"
          type="number"
          min="0"
          max="100"
          step="1"
          bind:value={draftRetryMaxAttempts}
        />
      </div>

      <div class="dialog-actions settings-actions">
        <Button onclick={() => (settingsOpen = false)}>Cancel</Button>
        <Button variant="primary" onclick={saveSettings}>Save</Button>
      </div>
    </div>
  </MovableDialog>
{/if}

{#if showApiKeyDialog}
  <MovableDialog title="API Key Required" width="400px">
    <form class="dialog-form" onsubmit={handleApiKeySubmit}>
      <div class="s7-form-group">
        <label for="api-key-input">Enter API key to access this instance:</label>
        <input
          id="api-key-input"
          class="s7-input"
          type="password"
          placeholder="API key"
          bind:value={draftApiKey}
        />
      </div>

      {#if apiKeyError}
        <p class="dialog-error">{apiKeyError}</p>
      {/if}

      <div class="dialog-actions">
        <Button type="submit" variant="primary">Unlock</Button>
      </div>
    </form>
  </MovableDialog>
{/if}
