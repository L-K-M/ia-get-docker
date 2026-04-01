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

  const UI_SETTINGS_KEY = 'ia-get-ui-settings-v2';
  const DEFAULT_POLL_INTERVAL_MS = 1200;
  const DEFAULT_RECENT_JOBS_LIMIT = 12;

  let jobs = [];
  let activeJobId = null;
  let selectedJobId = null;
  let queueStats = {
    total_jobs: 0,
    queued_jobs: 0,
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

  let downloadRoot = '/downloads';
  let defaultAuthHint = 'Loading container defaults...';
  let containerDefaultUsername = '';

  let showAddDialog = false;
  let addUrl = '';
  let addSubdir = '';
  let addUsername = '';
  let addPassword = '';
  let addError = '';
  let addSubmitting = false;

  let showDetailsDialog = false;
  let detailJobId = null;
  let detailJob = null;
  let detailLogs = [];
  let detailLogOffset = 0;
  let detailLoading = false;
  let detailLogOutput;

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

  const columns = [
    { key: 'status', label: 'Status', width: '15%', className: 'col-status' },
    { key: 'identifier', label: 'Identifier', width: '22%', className: 'col-identifier' },
    { key: 'destination', label: 'Destination', width: '20%', className: 'col-destination' },
    { key: 'progress', label: 'Progress', width: '15%', className: 'col-progress' },
    { key: 'files', label: 'Files', width: '11%', className: 'col-files' },
    { key: 'created', label: 'Created', width: '17%', className: 'col-created' }
  ];

  $: selectedJob = jobs.find((item) => item.id === selectedJobId) || null;
  $: footerSummary =
    queueStats.total_jobs === 0
      ? 'No downloads queued yet.'
      : `${queueStats.terminal_jobs}/${queueStats.total_jobs} finished  |  ${queueStats.running_jobs} running  |  ${queueStats.queued_jobs} queued`;

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
      recentJobsLimit: clampInteger(draftRecentJobsLimit, 3, 50, DEFAULT_RECENT_JOBS_LIMIT),
      defaultSubdir: String(draftDefaultSubdir || '').trim(),
      defaultUsername: String(draftDefaultUsername || '').trim()
    };

    persistUiSettings();
    settingsOpen = false;
    setFlash('UI settings saved.');
    startPolling();
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
    draftDefaultSubdir = uiSettings.defaultSubdir;
    draftDefaultUsername = uiSettings.defaultUsername;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const body = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(body.error || `Request failed (${response.status})`);
    }

    return body;
  }

  function applyConfig(config) {
    downloadRoot = config.download_dir || '/downloads';
    containerDefaultUsername = String(config.default_username || '').trim();
    const hasDefaultUser = Boolean(containerDefaultUsername);
    const hasDefaultPass = Boolean(config.has_default_password);

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

    if (selectedJobId && !jobs.find((item) => item.id === selectedJobId)) {
      selectedJobId = null;
    }

    if (!selectedJobId && jobs.length > 0) {
      selectedJobId = jobs[0].id;
    }

    isLoading = false;
  }

  function selectJob(jobId) {
    selectedJobId = jobId;
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
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function describeStatus(job) {
    if (job.status === 'queued' && job.queue_position) {
      return `queued #${job.queue_position}`;
    }

    return job.status;
  }

  function describeProgress(job) {
    if (job.status === 'queued') {
      return job.queue_position ? `waiting #${job.queue_position}` : 'waiting';
    }

    if (job.status === 'running') {
      if (job.total_files > 0) {
        return `${job.progress_percent}%`;
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

    return job.status === 'queued' || job.status === 'running';
  }

  function openAddDownloadDialog() {
    addUrl = '';
    addSubdir = uiSettings.defaultSubdir || '';
    addUsername = uiSettings.defaultUsername || containerDefaultUsername || '';
    addPassword = '';
    addError = '';
    showAddDialog = true;
  }

  async function submitNewDownload() {
    const url = addUrl.trim();
    if (!url) {
      addError = 'Archive URL is required.';
      return;
    }

    addSubmitting = true;
    addError = '';

    const requestBody = {
      url,
      subdir: addSubdir.trim()
    };

    if (addUsername.trim().length > 0 || addPassword.length > 0) {
      requestBody.username = addUsername.trim();
      requestBody.password = addPassword;
    }

    try {
      const payload = await api('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });

      showAddDialog = false;
      selectedJobId = payload.job.id;
      addPassword = '';
      setFlash(`Queued download ${payload.job.id}.`);
      await refreshJobsList();
    } catch (error) {
      addError = error.message;
    } finally {
      addSubmitting = false;
    }
  }

  async function cancelSelectedJob() {
    if (!selectedJobId || !selectedJob) {
      return;
    }

    try {
      await api(`/api/jobs/${selectedJobId}/cancel`, { method: 'POST' });
      setFlash(`Cancellation requested for ${selectedJobId}.`);
      await refreshJobsList();

      if (showDetailsDialog && detailJobId === selectedJobId) {
        await refreshDetailDialog(false);
      }
    } catch (error) {
      setFlash(error.message, true);
    }
  }

  async function openDetailsDialog(jobId) {
    if (!jobId) {
      return;
    }

    detailJobId = jobId;
    detailJob = jobs.find((item) => item.id === jobId) || null;
    detailLogs = [];
    detailLogOffset = 0;
    showDetailsDialog = true;
    await refreshDetailDialog(true);
  }

  async function refreshDetailDialog(resetLogs = false) {
    if (!detailJobId) {
      return;
    }

    if (resetLogs) {
      detailLogs = [];
      detailLogOffset = 0;
    }

    detailLoading = true;

    try {
      const [jobPayload, logsPayload] = await Promise.all([
        api(`/api/jobs/${detailJobId}`),
        api(`/api/jobs/${detailJobId}/logs?offset=${detailLogOffset}`)
      ]);

      detailJob = jobPayload.job;
      const newLines = logsPayload.lines || [];

      if (resetLogs) {
        detailLogs = newLines.length > 0 ? [...newLines] : ['No logs yet.'];
      } else if (newLines.length > 0) {
        if (detailLogs.length === 1 && detailLogs[0] === 'No logs yet.') {
          detailLogs = [];
        }
        detailLogs = [...detailLogs, ...newLines];
      }

      detailLogOffset = logsPayload.next_offset || detailLogOffset;

      if (uiSettings.autoScrollLogs && newLines.length > 0 && detailLogOutput) {
        await tick();
        detailLogOutput.scrollTop = detailLogOutput.scrollHeight;
      }
    } catch (error) {
      setFlash(error.message, true);
    } finally {
      detailLoading = false;
    }
  }

  async function pollData() {
    try {
      await refreshJobsList();

      if (showDetailsDialog && detailJobId) {
        await refreshDetailDialog(false);
      }
    } catch (error) {
      setFlash(error.message, true);
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
      const config = await api('/api/config');
      applyConfig(config);
    } catch (error) {
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
      {:else if flashMessage}
        <p class="status-message">{flashMessage}</p>
      {/if}

      <div class="toolbar">
        <Button onclick={openAddDownloadDialog}>Add Download...</Button>
        <Button onclick={() => openDetailsDialog(selectedJobId)} disabled={!selectedJob}>Details</Button>
        <Button onclick={cancelSelectedJob} disabled={!canCancel(selectedJob)}>Cancel</Button>
        <Button onclick={pollData}>Refresh</Button>
        <Button onclick={openSettings}>Settings</Button>
      </div>

      <div class="table-wrap">
        <DataTable
          class="jobs-table"
          columns={columns}
          loading={isLoading && jobs.length === 0}
          loadingText="Loading queue..."
          empty={!isLoading && jobs.length === 0}
          emptyText="No downloads queued yet. Use 'Add Download...' to start."
          emptyColspan={6}
        >
          {#each jobs.slice(0, uiSettings.recentJobsLimit) as job}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <tr
              class:selected={selectedJobId === job.id}
              onclick={() => selectJob(job.id)}
              ondblclick={() => openDetailsDialog(job.id)}
            >
              <td class="col-status">{describeStatus(job)}</td>
              <td class="col-identifier">{job.identifier}</td>
              <td class="col-destination">{job.output_subdir}</td>
              <td class="col-progress">{describeProgress(job)}</td>
              <td class="col-files">{job.completed_files}/{job.total_files}</td>
              <td class="col-created">{formatTime(job.created_at)}</td>
            </tr>
          {/each}
        </DataTable>
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
  <MovableDialog title="Queue Download" onclose={() => (showAddDialog = false)} width="520px">
    <div class="dialog-form">
      <div class="s7-form-group">
        <label for="add-url">Archive URL</label>
        <input
          id="add-url"
          class="s7-input"
          type="url"
          placeholder="https://archive.org/details/En-ROMs"
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

      <div class="s7-form-group">
        <label for="add-username">Archive.org username (optional)</label>
        <input id="add-username" class="s7-input" type="text" bind:value={addUsername} />
      </div>

      <div class="s7-form-group">
        <label for="add-password">Archive.org password (optional)</label>
        <input id="add-password" class="s7-input" type="password" bind:value={addPassword} />
      </div>

      <p class="hint">{defaultAuthHint}</p>
      <p class="hint">Download root: <code>{downloadRoot}</code></p>

      {#if addError}
        <p class="dialog-error">{addError}</p>
      {/if}

      <div class="dialog-actions">
        <Button onclick={() => (showAddDialog = false)}>Cancel</Button>
        <Button variant="primary" onclick={submitNewDownload} disabled={addSubmitting}>
          {addSubmitting ? 'Queueing...' : 'Add to Queue'}
        </Button>
      </div>
    </div>
  </MovableDialog>
{/if}

{#if showDetailsDialog}
  <MovableDialog
    title={detailJob ? `Download ${detailJob.id}` : 'Download Details'}
    onclose={() => (showDetailsDialog = false)}
    width="760px"
  >
    <div class="details-dialog">
      {#if detailJob}
        <div class="details-grid">
          <p><strong>Status:</strong> {describeStatus(detailJob)}</p>
          <p><strong>Identifier:</strong> {detailJob.identifier}</p>
          <p><strong>URL:</strong> {detailJob.url}</p>
          <p><strong>Output:</strong> {detailJob.output_subdir}</p>
          <p><strong>Progress:</strong> {describeProgress(detailJob)}</p>
          <p><strong>Files:</strong> {detailJob.completed_files}/{detailJob.total_files}</p>
          <p><strong>Created:</strong> {formatTime(detailJob.created_at)}</p>
          <p><strong>Started:</strong> {formatTime(detailJob.started_at)}</p>
          <p><strong>Finished:</strong> {formatTime(detailJob.finished_at)}</p>
          <p><strong>Auth:</strong> {detailJob.auth_enabled ? detailJob.auth_username : 'public'}</p>
          <p><strong>Message:</strong> {detailJob.message || '--'}</p>
        </div>

        <div class="details-progress">
          <ProgressBar
            value={Number(detailJob.progress_percent || 0)}
            max={100}
            height={16}
            title="Job progress"
            ariaLabel="Job progress"
          />
        </div>

        <pre class="details-logs" bind:this={detailLogOutput}>{detailLogs.join('\n')}</pre>
      {:else}
        <p class="hint">Loading details...</p>
      {/if}

      <div class="dialog-actions">
        <Button onclick={() => refreshDetailDialog(true)} disabled={detailLoading}>Refresh</Button>
        <Button onclick={() => (showDetailsDialog = false)}>Close</Button>
      </div>
    </div>
  </MovableDialog>
{/if}

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
        <label for="recent-jobs-limit">Rows shown in table</label>
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
        label="Auto-scroll detail logs"
        onchange={(checked) => (draftAutoScrollLogs = checked)}
      />

      <p class="hint">
        Settings are stored in your browser only. Passwords are never saved.
      </p>

      <div class="dialog-actions settings-actions">
        <Button onclick={resetSettings}>Reset</Button>
        <Button onclick={() => (settingsOpen = false)}>Cancel</Button>
        <Button variant="primary" onclick={saveSettings}>Save</Button>
      </div>
    </div>
  </MovableDialog>
{/if}
