<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

const session = ref(null);
const integrations = ref({
  github: { connected: false, username: null },
  llm: { configured: false, provider: null, model: null },
});

const jobs = ref([]);
const selectedJobId = ref("");
const selectedJob = ref(null);
const events = ref([]);
const nextAfter = ref(0);

const busy = reactive({
  bootstrap: true,
  savingLlm: false,
  creatingJob: false,
  deployingJob: false,
  creatingPreview: false,
  resettingSession: false,
  githubAuthFlow: false,
});

const flash = ref("");
const llmConfigExpanded = ref(true);
const deployToGithub = ref(false);

const llmForm = reactive({
  provider: "perplexity",
  api_key: "",
  model: "sonar-pro",
});

const jobForm = reactive({
  title: "",
  brief: "",
});

const deployForm = reactive({
  repoName: "",
  visibility: "public",
  enablePages: true,
  branch: "main",
  deployPath: "/",
});

const attachmentFiles = ref([]);
const pollHandle = ref(null);
const UI_DRAFT_KEY = "app-creator-ui-draft-v1";

const panelState = reactive({
  step1: true,
  step2: true,
  step3: true,
  history: false,
});

const isCompactScreen = ref(false);
const buildModal = reactive({
  visible: false,
  jobId: "",
  failed: false,
  error: "",
  lines: [],
});
const deployModal = reactive({
  visible: false,
  jobId: "",
  failed: false,
  error: "",
  lines: [],
});
const previewPrompt = reactive({
  visible: false,
  error: "",
});

const buildInFlightStatuses = new Set(["queued", "in_progress", "deploying"]);
const pagesLinkHoverTitle = "GitHub Pages can take a few minutes to become live after deployment.";
const isAnyProgressModalVisible = computed(
  () => buildModal.visible || deployModal.visible || previewPrompt.visible
);
const hasCompletedDeployment = computed(() => Boolean(selectedJob.value?.repo_url || selectedJob.value?.commit_sha));

const canCreateJob = computed(() => integrations.value.llm.configured && !isAnyProgressModalVisible.value);
const hasSavedLlmConfig = computed(() => integrations.value.llm.configured);
const canDownloadSelectedJob = computed(() => Boolean(selectedJob.value?.download_url));
const canDeploySelectedJob = computed(() => {
  if (!selectedJob.value || !selectedJob.value.download_url) {
    return false;
  }
  if (!integrations.value.github.connected) {
    return false;
  }
  if (!deployForm.repoName.trim()) {
    return false;
  }
  if (buildInFlightStatuses.has(selectedJob.value.status)) {
    return false;
  }
  if (hasCompletedDeployment.value) {
    return false;
  }
  return !busy.deployingJob && !isAnyProgressModalVisible.value;
});
const selectedPagesUrl = computed(() => buildPagesUrl(selectedJob.value));
const isSelectedPagesUrlEstimated = computed(
  () => Boolean(selectedJob.value && !selectedJob.value.pages_url && selectedPagesUrl.value)
);
const recentEvents = computed(() => events.value.slice(-200));

function onPanelToggle(key, event) {
  panelState[key] = event.target.open;
}

function applyResponsivePanelDefaults(force = false) {
  const compact = window.innerWidth <= 920;
  if (!force && compact === isCompactScreen.value) {
    return;
  }

  isCompactScreen.value = compact;
  if (compact) {
    panelState.step1 = true;
    panelState.step2 = false;
    panelState.step3 = false;
    if (force) {
      panelState.history = false;
    }
    return;
  }

  panelState.step1 = true;
  panelState.step2 = true;
  panelState.step3 = true;
  if (force) {
    panelState.history = false;
  }
}

function handleResize() {
  applyResponsivePanelDefaults(false);
}

function saveUiDraft() {
  try {
    const draft = {
      selectedJobId: selectedJobId.value,
      deployToGithub: deployToGithub.value,
      jobForm: {
        title: jobForm.title,
        brief: jobForm.brief,
      },
      deployForm: {
        repoName: deployForm.repoName,
        visibility: deployForm.visibility,
        enablePages: deployForm.enablePages,
        branch: deployForm.branch,
        deployPath: deployForm.deployPath,
      },
      panelState: {
        step1: panelState.step1,
        step2: panelState.step2,
        step3: panelState.step3,
        history: panelState.history,
      },
      llmConfigExpanded: llmConfigExpanded.value,
    };
    window.sessionStorage.setItem(UI_DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // ignore storage errors
  }
}

function clearUiDraft() {
  try {
    window.sessionStorage.removeItem(UI_DRAFT_KEY);
  } catch {
    // ignore storage errors
  }
}

function restoreUiDraft() {
  try {
    const raw = window.sessionStorage.getItem(UI_DRAFT_KEY);
    if (!raw) {
      return;
    }
    const draft = JSON.parse(raw);

    if (typeof draft.selectedJobId === "string") {
      selectedJobId.value = draft.selectedJobId;
    }
    if (typeof draft.deployToGithub === "boolean") {
      deployToGithub.value = draft.deployToGithub;
    }

    if (draft.jobForm && typeof draft.jobForm === "object") {
      jobForm.title = typeof draft.jobForm.title === "string" ? draft.jobForm.title : jobForm.title;
      jobForm.brief = typeof draft.jobForm.brief === "string" ? draft.jobForm.brief : jobForm.brief;
    }

    if (draft.deployForm && typeof draft.deployForm === "object") {
      deployForm.repoName =
        typeof draft.deployForm.repoName === "string" ? draft.deployForm.repoName : deployForm.repoName;
      deployForm.visibility =
        draft.deployForm.visibility === "private" || draft.deployForm.visibility === "public"
          ? draft.deployForm.visibility
          : deployForm.visibility;
      deployForm.enablePages =
        typeof draft.deployForm.enablePages === "boolean"
          ? draft.deployForm.enablePages
          : deployForm.enablePages;
      deployForm.branch = typeof draft.deployForm.branch === "string" ? draft.deployForm.branch : deployForm.branch;
      deployForm.deployPath =
        typeof draft.deployForm.deployPath === "string" ? draft.deployForm.deployPath : deployForm.deployPath;
    }

    if (draft.panelState && typeof draft.panelState === "object") {
      panelState.step1 = Boolean(draft.panelState.step1);
      panelState.step2 = Boolean(draft.panelState.step2);
      panelState.step3 = Boolean(draft.panelState.step3);
      panelState.history = Boolean(draft.panelState.history);
    }

    if (typeof draft.llmConfigExpanded === "boolean") {
      llmConfigExpanded.value = draft.llmConfigExpanded;
    }
  } catch {
    // ignore invalid storage payload
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }

  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function dedupeJobs(jobList) {
  const uniqueJobs = [];
  const seen = new Set();
  for (const job of jobList || []) {
    if (seen.has(job.job_id)) {
      continue;
    }
    seen.add(job.job_id);
    uniqueJobs.push(job);
  }
  return uniqueJobs;
}

function syncSelectedJobSelection() {
  if (jobs.value.length === 0) {
    selectedJobId.value = "";
    selectedJob.value = null;
    events.value = [];
    nextAfter.value = 0;
    return;
  }

  if (!selectedJobId.value) {
    selectedJob.value = null;
    events.value = [];
    nextAfter.value = 0;
    return;
  }

  const stillPresent = jobs.value.some((job) => job.job_id === selectedJobId.value);
  if (!stillPresent) {
    selectedJobId.value = "";
    selectedJob.value = null;
    events.value = [];
    nextAfter.value = 0;
  }
}

function syncDeployFormFromSelectedJob() {
  if (!selectedJob.value) {
    return;
  }
  if (selectedJob.value.repo_name) {
    deployForm.repoName = selectedJob.value.repo_name;
  }
  if (selectedJob.value.repo_visibility) {
    deployForm.visibility = selectedJob.value.repo_visibility;
  }
}

function openBuildModal(jobId) {
  buildModal.visible = true;
  buildModal.jobId = jobId;
  buildModal.failed = false;
  buildModal.error = "";
  buildModal.lines = ["> Build accepted. Waiting for worker..."];
}

function closeBuildModal() {
  buildModal.visible = false;
  buildModal.jobId = "";
  buildModal.failed = false;
  buildModal.error = "";
  buildModal.lines = [];
}

function openDeployModal(jobId) {
  deployModal.visible = true;
  deployModal.jobId = jobId;
  deployModal.failed = false;
  deployModal.error = "";
  deployModal.lines = ["> Deployment accepted. Waiting for worker..."];
}

function closeDeployModal() {
  deployModal.visible = false;
  deployModal.jobId = "";
  deployModal.failed = false;
  deployModal.error = "";
  deployModal.lines = [];
}

function openPreviewPrompt() {
  if (!selectedJobId.value || !canDownloadSelectedJob.value || isAnyProgressModalVisible.value) {
    return;
  }
  previewPrompt.visible = true;
  previewPrompt.error = "";
}

function closePreviewPrompt() {
  previewPrompt.visible = false;
  previewPrompt.error = "";
}

function appendBuildModalEvents(newEvents) {
  if (!buildModal.visible || !selectedJob.value || buildModal.jobId !== selectedJob.value.job_id) {
    return;
  }

  for (const event of newEvents) {
    buildModal.lines.push(`> ${event.message}`);
  }

  if (buildModal.lines.length > 80) {
    buildModal.lines = buildModal.lines.slice(-80);
  }
}

function appendDeployModalEvents(newEvents) {
  if (!deployModal.visible || !selectedJob.value || deployModal.jobId !== selectedJob.value.job_id) {
    return;
  }

  for (const event of newEvents) {
    deployModal.lines.push(`> ${event.message}`);
  }

  if (deployModal.lines.length > 80) {
    deployModal.lines = deployModal.lines.slice(-80);
  }
}

function updateBuildModalStatus() {
  if (!buildModal.visible || !selectedJob.value || buildModal.jobId !== selectedJob.value.job_id) {
    return;
  }

  if (selectedJob.value.status === "failed") {
    buildModal.failed = true;
    buildModal.error = selectedJob.value.error_message || "Build failed.";
    return;
  }

  if (selectedJob.value.status === "completed") {
    closeBuildModal();
    flash.value = "Build complete. Continue to Step 3 to download or deploy.";
  }
}

function updateDeployModalStatus() {
  if (!deployModal.visible || !selectedJob.value || deployModal.jobId !== selectedJob.value.job_id) {
    return;
  }

  if (selectedJob.value.status === "deploy_failed" || selectedJob.value.status === "failed") {
    deployModal.failed = true;
    deployModal.error = selectedJob.value.error_message || "Deployment failed.";
    return;
  }

  if (selectedJob.value.status === "completed" && hasCompletedDeployment.value) {
    closeDeployModal();
    deployToGithub.value = false;
    flash.value = "GitHub deployment completed. Links are now available in Step 3.";
  }
}

async function bootstrap() {
  busy.bootstrap = true;
  try {
    session.value = await api("/api/session");
    integrations.value = await api("/api/integrations");

    llmConfigExpanded.value = !integrations.value.llm.configured;
    if (integrations.value.llm.model) {
      llmForm.model = integrations.value.llm.model;
    }

    const jobResponse = await api("/api/jobs");
    jobs.value = dedupeJobs(jobResponse.jobs);
    syncSelectedJobSelection();

    if (selectedJobId.value) {
      await refreshSelectedJob();
      syncDeployFormFromSelectedJob();
    }
  } catch (error) {
    flash.value = `Failed to initialize: ${error.message}`;
  } finally {
    busy.bootstrap = false;
  }
}

async function refreshIntegrations() {
  integrations.value = await api("/api/integrations");
  if (integrations.value.llm.model) {
    llmForm.model = integrations.value.llm.model;
  }
  if (!integrations.value.llm.configured) {
    llmConfigExpanded.value = true;
  }
}

async function startGithubAuth() {
  busy.githubAuthFlow = true;
  flash.value = "";
  try {
    saveUiDraft();
    const data = await api("/api/auth/github/start");
    window.location.href = data.url;
  } catch (error) {
    flash.value = `GitHub App authorization start failed: ${error.message}`;
  } finally {
    busy.githubAuthFlow = false;
  }
}

async function installGithubApp() {
  busy.githubAuthFlow = true;
  flash.value = "";
  try {
    saveUiDraft();
    const data = await api("/api/auth/github/start");
    if (!data.install_url) {
      flash.value =
        "Install URL is not available. Set GITHUB_APP_SLUG in server environment and restart the app.";
      return;
    }
    window.location.href = data.install_url;
  } catch (error) {
    flash.value = `GitHub App install launch failed: ${error.message}`;
  } finally {
    busy.githubAuthFlow = false;
  }
}

async function disconnectGithub() {
  flash.value = "";
  try {
    integrations.value = await api("/api/auth/github/disconnect", { method: "POST" });
  } catch (error) {
    flash.value = `Failed to disconnect GitHub: ${error.message}`;
  }
}

async function saveLlmConfig() {
  busy.savingLlm = true;
  flash.value = "";
  try {
    integrations.value = await api("/api/integrations/llm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: llmForm.provider,
        api_key: llmForm.api_key,
        model: llmForm.model,
      }),
    });
    llmForm.api_key = "";
    llmConfigExpanded.value = false;
    flash.value = "LLM provider configured for this session.";
  } catch (error) {
    flash.value = `Failed to save LLM config: ${error.message}`;
  } finally {
    busy.savingLlm = false;
  }
}

function startLlmConfigChange() {
  llmConfigExpanded.value = true;
}

function onAttachmentChange(event) {
  attachmentFiles.value = [...event.target.files];
}

async function createJob() {
  busy.creatingJob = true;
  flash.value = "";

  try {
    const payload = {
      title: jobForm.title,
      brief: jobForm.brief,
    };

    const formData = new FormData();
    formData.append("payload", JSON.stringify(payload));
    for (const file of attachmentFiles.value) {
      formData.append("files", file);
    }

    const result = await api("/api/jobs", {
      method: "POST",
      body: formData,
    });

    selectedJobId.value = result.job_id;
    events.value = [];
    nextAfter.value = 0;
    openBuildModal(result.job_id);

    const jobResponse = await api("/api/jobs");
    jobs.value = dedupeJobs(jobResponse.jobs);
    await refreshSelectedJob();
    syncDeployFormFromSelectedJob();
  } catch (error) {
    closeBuildModal();
    flash.value = `Failed to create build: ${error.message}`;
  } finally {
    busy.creatingJob = false;
  }
}

async function deploySelectedJob() {
  if (!selectedJobId.value) {
    return;
  }

  busy.deployingJob = true;
  flash.value = "";
  try {
    await api(`/api/jobs/${selectedJobId.value}/deploy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repo: {
          name: deployForm.repoName,
          visibility: deployForm.visibility,
        },
        deployment: {
          enable_pages: deployForm.enablePages,
          branch: deployForm.branch,
          path: deployForm.deployPath,
        },
      }),
    });

    openDeployModal(selectedJobId.value);
    const jobResponse = await api("/api/jobs");
    jobs.value = dedupeJobs(jobResponse.jobs);
    await refreshSelectedJob();
  } catch (error) {
    closeDeployModal();
    flash.value = `Failed to deploy build: ${error.message}`;
  } finally {
    busy.deployingJob = false;
  }
}

async function confirmPreviewOpen() {
  if (!selectedJobId.value) {
    return;
  }

  let previewTab = null;
  try {
    previewTab = window.open("about:blank", "_blank", "noopener");
  } catch {
    previewTab = null;
  }

  busy.creatingPreview = true;
  previewPrompt.error = "";
  try {
    const payload = await api(`/api/jobs/${selectedJobId.value}/preview`, {
      method: "POST",
    });
    const previewUrl = payload.preview_url;
    if (!previewUrl) {
      throw new Error("Preview URL missing from server response");
    }

    if (previewTab && !previewTab.closed) {
      previewTab.location.href = previewUrl;
    } else {
      window.open(previewUrl, "_blank", "noopener");
    }
    closePreviewPrompt();
  } catch (error) {
    if (previewTab && !previewTab.closed) {
      previewTab.close();
    }
    previewPrompt.error = `Failed to create preview: ${error.message}`;
  } finally {
    busy.creatingPreview = false;
  }
}

async function refreshJobs() {
  try {
    const jobResponse = await api("/api/jobs");
    jobs.value = dedupeJobs(jobResponse.jobs);
    syncSelectedJobSelection();
  } catch (error) {
    flash.value = `Failed to refresh jobs: ${error.message}`;
  }
}

async function refreshSelectedJob() {
  if (!selectedJobId.value) {
    selectedJob.value = null;
    events.value = [];
    nextAfter.value = 0;
    return;
  }

  try {
    selectedJob.value = await api(`/api/jobs/${selectedJobId.value}`);
    const eventResult = await api(`/api/jobs/${selectedJobId.value}/events?after=${nextAfter.value}`);
    if (eventResult.events.length > 0) {
      events.value = [...events.value, ...eventResult.events];
      if (events.value.length > 400) {
        events.value = events.value.slice(-400);
      }
      appendBuildModalEvents(eventResult.events);
      appendDeployModalEvents(eventResult.events);
      nextAfter.value = eventResult.next_after;
    }
    updateBuildModalStatus();
    updateDeployModalStatus();
  } catch (error) {
    flash.value = `Failed to refresh selected job: ${error.message}`;
  }
}

function startPolling() {
  stopPolling();
  pollHandle.value = setInterval(async () => {
    await refreshJobs();
    await refreshSelectedJob();
  }, 4000);
}

function stopPolling() {
  if (pollHandle.value) {
    clearInterval(pollHandle.value);
    pollHandle.value = null;
  }
}

function buildPagesUrl(job) {
  if (!job) {
    return null;
  }
  if (job.pages_url) {
    return job.pages_url;
  }

  let repoFullName = job.repo_full_name;
  if (!repoFullName && job.repo_url && job.repo_url.startsWith("https://github.com/")) {
    repoFullName = job.repo_url.replace("https://github.com/", "");
  }
  if (!repoFullName || !repoFullName.includes("/")) {
    return null;
  }

  const parts = repoFullName.split("/");
  if (parts.length !== 2 || !parts[0] || !parts[1]) {
    return null;
  }
  return `https://${parts[0]}.github.io/${parts[1]}/`;
}

async function resetSession() {
  busy.resettingSession = true;
  flash.value = "";
  try {
    session.value = await api("/api/session/reset", { method: "POST" });
    jobs.value = [];
    selectedJobId.value = "";
    selectedJob.value = null;
    events.value = [];
    nextAfter.value = 0;
    llmForm.api_key = "";
    deployToGithub.value = false;
    deployForm.repoName = "";
    deployForm.visibility = "public";
    deployForm.enablePages = true;
    deployForm.branch = "main";
    deployForm.deployPath = "/";
    clearUiDraft();
    closeBuildModal();
    closeDeployModal();
    closePreviewPrompt();
    await refreshIntegrations();
    llmConfigExpanded.value = true;
    flash.value = "Session reset complete. Integrations and pending state were cleared.";
  } catch (error) {
    flash.value = `Failed to reset session: ${error.message}`;
  } finally {
    busy.resettingSession = false;
  }
}

watch(selectedJobId, async (newValue, oldValue) => {
  if (newValue !== oldValue) {
    events.value = [];
    nextAfter.value = 0;
    deployToGithub.value = false;
    await refreshSelectedJob();
    syncDeployFormFromSelectedJob();
  }
});

watch(
  () => deployForm.visibility,
  (visibility) => {
    if (visibility === "private") {
      deployForm.enablePages = false;
    }
  }
);

onMounted(async () => {
  restoreUiDraft();

  const params = new URLSearchParams(window.location.search);
  if (params.get("github") === "connected") {
    flash.value = "GitHub connected for current session.";
    window.history.replaceState({}, "", window.location.pathname);
  }
  if (params.get("github") === "error") {
    flash.value = `GitHub connection failed (${params.get("reason") || "unknown"}).`;
    window.history.replaceState({}, "", window.location.pathname);
  }

  applyResponsivePanelDefaults(true);
  window.addEventListener("resize", handleResize);

  await bootstrap();
  startPolling();
});

onBeforeUnmount(() => {
  stopPolling();
  window.removeEventListener("resize", handleResize);
});
</script>

<template>
  <div class="shell">
    <header class="hero">
      <div>
        <p class="eyebrow">Self-Hosted Builder</p>
        <h1>App Creator Console</h1>
        <p class="subtext">Generate app files once, then download or deploy when you are ready.</p>
      </div>
      <div class="session-card">
        <p class="label">Session ID</p>
        <code>{{ session?.session_id || "loading" }}</code>
        <button class="ghost" :disabled="busy.resettingSession || isAnyProgressModalVisible" @click="resetSession">
          {{ busy.resettingSession ? "Resetting..." : "Reset Session" }}
        </button>
      </div>
    </header>

    <p v-if="flash" class="flash">{{ flash }}</p>

    <div class="desktop-layout">
      <main class="step-stack step-primary">
        <details class="panel step-panel" :open="panelState.step1" @toggle="onPanelToggle('step1', $event)">
        <summary class="step-summary">
          <span class="step-index">Step 1</span>
          <h2>Choose LLM provider</h2>
        </summary>

        <div class="stack llm-section">
          <div class="llm-head">
            <h3>LLM Provider</h3>
            <button
              v-if="hasSavedLlmConfig && !llmConfigExpanded"
              class="ghost"
              type="button"
              @click="startLlmConfigChange"
            >
              Change LLM Config
            </button>
          </div>

          <p v-if="hasSavedLlmConfig && !llmConfigExpanded" class="hint">
            Configured: <strong>{{ integrations.llm.provider }}</strong> ({{ integrations.llm.model }})
          </p>

          <form v-if="llmConfigExpanded" class="stack" @submit.prevent="saveLlmConfig">
            <label>
              Provider
              <select v-model="llmForm.provider" disabled>
                <option value="perplexity">Perplexity</option>
              </select>
            </label>
            <label>
              API Key
              <input v-model="llmForm.api_key" type="password" required placeholder="pplx-..." />
            </label>
            <label>
              Model
              <input v-model="llmForm.model" type="text" placeholder="sonar-pro" />
            </label>
            <button :disabled="busy.savingLlm || isAnyProgressModalVisible">
              {{ busy.savingLlm ? "Saving..." : "Save LLM Config" }}
            </button>
          </form>
        </div>
        </details>

        <details class="panel step-panel" :open="panelState.step2" @toggle="onPanelToggle('step2', $event)">
        <summary class="step-summary">
          <span class="step-index">Step 2</span>
          <h2>Build your app</h2>
        </summary>

        <form class="stack" @submit.prevent="createJob">
          <label>
            Title
            <input v-model="jobForm.title" type="text" required />
          </label>

          <label>
            Describe your application
            <textarea v-model="jobForm.brief" rows="7" required></textarea>
          </label>

          <label>
            Attachments
            <input type="file" multiple @change="onAttachmentChange" />
          </label>

          <button :disabled="!canCreateJob || busy.creatingJob || busy.bootstrap">
            {{ busy.creatingJob ? "Submitting..." : "Build your app" }}
          </button>
        </form>
        </details>
      </main>

      <aside class="step-stack step-secondary">
        <details class="panel step-panel" :open="panelState.step3" @toggle="onPanelToggle('step3', $event)">
        <summary class="step-summary">
          <span class="step-index">Step 3</span>
          <h2>Get your app!</h2>
        </summary>

        <div v-if="!selectedJob" class="stack">
          <p class="hint">Create a build in Step 2 first. Delivery options will appear here.</p>
        </div>

        <div v-else class="stack">
          <p>
            <strong>Selected build:</strong>
            {{ selectedJob.title }} ({{ selectedJob.status }})
          </p>

          <div class="delivery-actions">
            <p>
              <strong>Download:</strong>
              <a v-if="canDownloadSelectedJob" :href="selectedJob.download_url">Download ZIP package</a>
              <span v-else>Available after build completes.</span>
            </p>
            <button class="ghost" type="button" :disabled="!canDownloadSelectedJob" @click="openPreviewPrompt">
              Preview this app!
            </button>
          </div>

          <div v-if="hasCompletedDeployment" class="stack delivery-box">
            <p><strong>Deployment complete.</strong></p>
            <p>
              <strong>Repository:</strong>
              <a :href="selectedJob.repo_url" target="_blank">{{ selectedJob.repo_url }}</a>
            </p>
            <p>
              <strong>Pages:</strong>
              <a v-if="selectedPagesUrl" :href="selectedPagesUrl" target="_blank" :title="pagesLinkHoverTitle">
                {{ selectedPagesUrl }}{{ isSelectedPagesUrlEstimated ? " (estimated)" : "" }}
              </a>
              <span v-else>-</span>
            </p>
            <p><strong>Commit:</strong> <code>{{ selectedJob.commit_sha || "-" }}</code></p>
          </div>

          <template v-else>
            <label class="check-row deploy-choice">
              <input v-model="deployToGithub" type="checkbox" :disabled="!canDownloadSelectedJob" />
              Deploy this to my GitHub account
            </label>

            <div v-if="deployToGithub" class="stack delivery-box">
              <div class="integration-row compact-row">
                <div>
                  <h3>GitHub App</h3>
                  <p v-if="integrations.github.connected">
                    Connected as <strong>{{ integrations.github.username }}</strong>
                  </p>
                  <p v-else>Not connected</p>
                </div>
                <div class="row-actions">
                  <template v-if="!integrations.github.connected">
                    <button class="ghost" :disabled="busy.githubAuthFlow" @click="installGithubApp">Install App</button>
                    <button :disabled="busy.githubAuthFlow" @click="startGithubAuth">Connect GitHub</button>
                  </template>
                  <button v-else class="ghost" @click="disconnectGithub">Disconnect</button>
                </div>
              </div>

              <form v-if="integrations.github.connected" class="stack" @submit.prevent="deploySelectedJob">
                <label>
                  GitHub Repository Name
                  <input v-model="deployForm.repoName" type="text" required />
                </label>
                <label>
                  Visibility
                  <select v-model="deployForm.visibility">
                    <option value="public">Public</option>
                    <option value="private">Private</option>
                  </select>
                </label>

                <p v-if="deployForm.visibility === 'private'" class="private-pages-note">
                  Private repositories may not support GitHub Pages on your current plan. Pages is disabled.
                </p>

                <div class="split">
                  <label>
                    Branch
                    <input v-model="deployForm.branch" type="text" />
                  </label>
                  <label>
                    Pages Path
                    <input v-model="deployForm.deployPath" type="text" />
                  </label>
                </div>

                <label class="check-row">
                  <input
                    v-model="deployForm.enablePages"
                    type="checkbox"
                    :disabled="deployForm.visibility === 'private'"
                  />
                  Enable GitHub Pages after push
                </label>

                <button :disabled="!canDeploySelectedJob">
                  {{ busy.deployingJob ? "Deploying..." : "Deploy to GitHub" }}
                </button>
              </form>
            </div>
          </template>
        </div>
        </details>

        <details class="panel step-panel past-jobs" :open="panelState.history" @toggle="onPanelToggle('history', $event)">
          <summary class="step-summary">
            <span class="step-index">History</span>
            <h2>Past jobs</h2>
          </summary>

          <div class="jobs-list">
            <button
              v-for="job in jobs"
              :key="job.job_id"
              class="job-pill"
              :class="{ selected: selectedJobId === job.job_id }"
              @click="selectedJobId = job.job_id"
            >
              <span>{{ job.title }}</span>
              <strong>{{ job.status }}</strong>
            </button>
            <p v-if="jobs.length === 0">No jobs yet.</p>
          </div>

          <div v-if="selectedJob" class="job-detail">
            <h3>Selected Job</h3>
            <p><strong>ID:</strong> {{ selectedJob.job_id }}</p>
            <p><strong>Status:</strong> {{ selectedJob.status }}</p>
            <p><strong>Artifact:</strong> {{ selectedJob.artifact_name || "-" }}</p>
            <p>
              <strong>Repo:</strong>
              <a v-if="selectedJob.repo_url" :href="selectedJob.repo_url" target="_blank">{{ selectedJob.repo_url }}</a>
              <span v-else>-</span>
            </p>
            <p>
              <strong>Pages:</strong>
              <a v-if="selectedPagesUrl" :href="selectedPagesUrl" target="_blank" :title="pagesLinkHoverTitle">
                {{ selectedPagesUrl }}{{ isSelectedPagesUrlEstimated ? " (estimated)" : "" }}
              </a>
              <span v-else>-</span>
            </p>
            <p><strong>Commit:</strong> <code>{{ selectedJob.commit_sha || "-" }}</code></p>
            <p><strong>Error:</strong> {{ selectedJob.error_message || "-" }}</p>

            <h4>Events</h4>
            <ul class="events">
              <li v-for="event in recentEvents" :key="event.id">
                <span :class="event.level">{{ event.message }}</span>
              </li>
            </ul>
          </div>
        </details>
      </aside>
    </div>
  </div>

  <div v-if="buildModal.visible" class="build-overlay">
    <div class="build-popup">
      <div class="build-head">
        <div v-if="!buildModal.failed" class="spinner" aria-hidden="true"></div>
        <h3>{{ buildModal.failed ? "Build failed" : "Building your app" }}</h3>
      </div>

      <pre class="build-log"><code>{{ buildModal.lines.join("\n") || "> waiting for worker output..." }}</code></pre>

      <p v-if="buildModal.failed" class="error">{{ buildModal.error }}</p>
      <button v-if="buildModal.failed" @click="closeBuildModal">Close</button>
    </div>
  </div>

  <div v-if="deployModal.visible" class="build-overlay">
    <div class="build-popup">
      <div class="build-head">
        <div v-if="!deployModal.failed" class="spinner" aria-hidden="true"></div>
        <h3>{{ deployModal.failed ? "Deployment failed" : "Deploying to GitHub" }}</h3>
      </div>

      <pre class="build-log"><code>{{ deployModal.lines.join("\n") || "> waiting for worker output..." }}</code></pre>

      <p v-if="deployModal.failed" class="error">{{ deployModal.error }}</p>
      <button v-if="deployModal.failed" @click="closeDeployModal">Close</button>
    </div>
  </div>

  <div v-if="previewPrompt.visible" class="build-overlay">
    <div class="build-popup preview-popup">
      <h3>Preview this app?</h3>
      <p>
        This creates a temporary static preview on the server. It will stay live for
        <strong>1 hour</strong> and then expire automatically.
      </p>
      <p v-if="previewPrompt.error" class="error">{{ previewPrompt.error }}</p>
      <div class="preview-actions">
        <button class="ghost" type="button" :disabled="busy.creatingPreview" @click="closePreviewPrompt">
          Cancel
        </button>
        <button type="button" :disabled="busy.creatingPreview" @click="confirmPreviewOpen">
          {{ busy.creatingPreview ? "Preparing..." : "OK, open preview" }}
        </button>
      </div>
    </div>
  </div>
</template>
