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
  resettingSession: false,
  githubAuthFlow: false,
});

const flash = ref("");
const llmConfigExpanded = ref(true);
const showActiveOnlyJobs = ref(true);

const llmForm = reactive({
  provider: "perplexity",
  api_key: "",
  model: "sonar-pro",
});

const jobForm = reactive({
  title: "",
  deliveryMode: "github",
  repoName: "",
  visibility: "public",
  brief: "",
  enablePages: true,
  branch: "main",
  deployPath: "/",
});

const attachmentFiles = ref([]);
const pollHandle = ref(null);

const ACTIVE_JOB_STATUSES = new Set(["queued", "in_progress", "deployed"]);
const pagesLinkHoverTitle = "GitHub Pages can take a few minutes to become live after deployment.";

const canCreateJob = computed(() => {
  if (!integrations.value.llm.configured) {
    return false;
  }
  if (jobForm.deliveryMode === "zip") {
    return true;
  }
  return integrations.value.github.connected;
});
const hasSavedLlmConfig = computed(() => integrations.value.llm.configured);
const selectedPagesUrl = computed(() => buildPagesUrl(selectedJob.value));
const isSelectedPagesUrlEstimated = computed(
  () => Boolean(selectedJob.value && !selectedJob.value.pages_url && selectedPagesUrl.value)
);
const displayedJobs = computed(() =>
  showActiveOnlyJobs.value
    ? jobs.value.filter((job) => ACTIVE_JOB_STATUSES.has(job.status))
    : jobs.value
);
const hiddenJobCount = computed(() =>
  showActiveOnlyJobs.value ? Math.max(jobs.value.length - displayedJobs.value.length, 0) : 0
);

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

function syncSelectedJobWithFilters() {
  if (!selectedJobId.value) {
    return;
  }
  const stillVisible = displayedJobs.value.some((job) => job.job_id === selectedJobId.value);
  if (!stillVisible) {
    selectedJobId.value = "";
    selectedJob.value = null;
    events.value = [];
    nextAfter.value = 0;
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

    if (displayedJobs.value.length > 0) {
      selectedJobId.value = displayedJobs.value[0].job_id;
      await refreshSelectedJob();
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
      delivery_mode: jobForm.deliveryMode,
    };
    if (jobForm.deliveryMode === "github") {
      payload.repo = {
        name: jobForm.repoName,
        visibility: jobForm.visibility,
      };
      payload.deployment = {
        enable_pages: jobForm.enablePages,
        branch: jobForm.branch,
        path: jobForm.deployPath,
      };
    }

    const formData = new FormData();
    formData.append("payload", JSON.stringify(payload));
    for (const file of attachmentFiles.value) {
      formData.append("files", file);
    }

    const result = await api("/api/jobs", {
      method: "POST",
      body: formData,
    });

    const jobResponse = await api("/api/jobs");
    jobs.value = dedupeJobs(jobResponse.jobs);
    selectedJobId.value = result.job_id;
    await refreshSelectedJob();
    flash.value = `Job ${result.job_id} queued.`;
  } catch (error) {
    flash.value = `Failed to create job: ${error.message}`;
  } finally {
    busy.creatingJob = false;
  }
}

async function refreshJobs() {
  try {
    const jobResponse = await api("/api/jobs");
    jobs.value = dedupeJobs(jobResponse.jobs);
    syncSelectedJobWithFilters();
  } catch (error) {
    flash.value = `Failed to refresh jobs: ${error.message}`;
  }
}

async function refreshJobsPanel() {
  jobs.value = [];
  selectedJobId.value = "";
  selectedJob.value = null;
  events.value = [];
  nextAfter.value = 0;
  await refreshJobs();
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
      if (events.value.length > 200) {
        events.value = events.value.slice(-200);
      }
      nextAfter.value = eventResult.next_after;
    }
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

function toggleJobFilter() {
  showActiveOnlyJobs.value = !showActiveOnlyJobs.value;
  syncSelectedJobWithFilters();
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
    await refreshSelectedJob();
  }
});

watch(showActiveOnlyJobs, () => {
  syncSelectedJobWithFilters();
});

watch(
  () => jobForm.visibility,
  (visibility) => {
    if (visibility === "private") {
      jobForm.enablePages = false;
    }
  }
);

watch(
  () => jobForm.deliveryMode,
  (mode) => {
    if (mode === "zip") {
      jobForm.enablePages = false;
    }
  }
);

onMounted(async () => {
  const params = new URLSearchParams(window.location.search);
  if (params.get("github") === "connected") {
    flash.value = "GitHub connected for current session.";
    window.history.replaceState({}, "", window.location.pathname);
  }
  if (params.get("github") === "error") {
    flash.value = `GitHub connection failed (${params.get("reason") || "unknown"}).`;
    window.history.replaceState({}, "", window.location.pathname);
  }

  await bootstrap();
  startPolling();
});

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<template>
  <div class="shell">
    <header class="hero">
      <div>
        <p class="eyebrow">Self-Hosted Builder</p>
        <h1>App Creator Console</h1>
        <p class="subtext">
          Generate, publish, and track static apps with your own GitHub and LLM credentials.
        </p>
      </div>
      <div class="session-card">
        <p class="label">Session ID</p>
        <code>{{ session?.session_id || "loading" }}</code>
        <button class="ghost" :disabled="busy.resettingSession" @click="resetSession">
          {{ busy.resettingSession ? "Resetting..." : "Reset Session" }}
        </button>
      </div>
    </header>

    <p v-if="flash" class="flash">{{ flash }}</p>

    <main class="grid">
      <section class="panel">
        <h2>Integrations</h2>

        <div class="integration-row">
          <div>
            <h3>GitHub App</h3>
            <p v-if="integrations.github.connected">
              Connected as <strong>{{ integrations.github.username }}</strong>
            </p>
            <p v-else>Not connected</p>
          </div>
          <div class="row-actions">
            <template v-if="!integrations.github.connected">
              <button class="ghost" :disabled="busy.githubAuthFlow" @click="installGithubApp">
                Install App
              </button>
              <button :disabled="busy.githubAuthFlow" @click="startGithubAuth">Connect GitHub</button>
            </template>
            <button v-else class="ghost" @click="disconnectGithub">Disconnect</button>
          </div>
        </div>

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
            <button :disabled="busy.savingLlm">{{ busy.savingLlm ? "Saving..." : "Save LLM Config" }}</button>
          </form>
        </div>
      </section>

      <section class="panel">
        <h2>Create Job</h2>
        <form class="stack" @submit.prevent="createJob">
          <label>
            Title
            <input v-model="jobForm.title" type="text" required />
          </label>
          <label>
            Delivery Mode
            <select v-model="jobForm.deliveryMode">
              <option value="github">Deploy to GitHub</option>
              <option value="zip">Generate ZIP package</option>
            </select>
          </label>

          <div v-if="jobForm.deliveryMode === 'github'" class="stack">
            <label>
              GitHub Repository Name
              <input v-model="jobForm.repoName" type="text" required />
            </label>
            <label>
              Visibility
              <select v-model="jobForm.visibility">
                <option value="public">Public</option>
                <option value="private">Private</option>
              </select>
            </label>

            <p v-if="jobForm.visibility === 'private'" class="private-pages-note">
              Private repositories may not support GitHub Pages on your current plan. Pages is disabled.
            </p>
          </div>
          <p v-else class="private-pages-note">This job will generate a ZIP package you can download directly.</p>

          <label>
            Brief
            <textarea v-model="jobForm.brief" rows="7" required></textarea>
          </label>

          <template v-if="jobForm.deliveryMode === 'github'">
            <div class="split">
              <label>
                Branch
                <input v-model="jobForm.branch" type="text" />
              </label>
              <label>
                Pages Path
                <input v-model="jobForm.deployPath" type="text" />
              </label>
            </div>

            <label class="check-row">
              <input v-model="jobForm.enablePages" type="checkbox" :disabled="jobForm.visibility === 'private'" />
              Enable GitHub Pages after push
            </label>
          </template>

          <label>
            Attachments
            <input type="file" multiple @change="onAttachmentChange" />
          </label>

          <button :disabled="!canCreateJob || busy.creatingJob || busy.bootstrap">
            {{ busy.creatingJob ? "Submitting..." : "Create Job" }}
          </button>
        </form>
      </section>

      <section class="panel">
        <div class="jobs-head">
          <h2>Jobs</h2>
          <div class="jobs-actions">
            <button class="ghost" type="button" @click="toggleJobFilter">
              {{ showActiveOnlyJobs ? "Show All" : "Show Active Only" }}
            </button>
            <button class="ghost" type="button" @click="refreshJobsPanel">Refresh</button>
          </div>
        </div>

        <p v-if="hiddenJobCount > 0" class="hint">
          Showing active jobs only. {{ hiddenJobCount }} completed/failed job(s) hidden.
        </p>

        <div class="jobs-list">
          <button
            v-for="job in displayedJobs"
            :key="job.job_id"
            class="job-pill"
            :class="{ selected: selectedJobId === job.job_id }"
            @click="selectedJobId = job.job_id"
          >
            <span>{{ job.title }}</span>
            <strong>{{ job.status }}</strong>
          </button>
          <p v-if="displayedJobs.length === 0">No jobs in current filter.</p>
        </div>

        <div v-if="selectedJob" class="job-detail">
          <h3>Selected Job</h3>
          <p><strong>ID:</strong> {{ selectedJob.job_id }}</p>
          <p><strong>Status:</strong> {{ selectedJob.status }}</p>
          <p>
            <strong>Repo:</strong>
            <a :href="selectedJob.repo_url" target="_blank">{{ selectedJob.repo_url || "-" }}</a>
          </p>
          <p>
            <strong>Pages:</strong>
            <a v-if="selectedPagesUrl" :href="selectedPagesUrl" target="_blank" :title="pagesLinkHoverTitle">
              {{ selectedPagesUrl }}{{ isSelectedPagesUrlEstimated ? " (estimated)" : "" }}
            </a>
            <span v-else>-</span>
          </p>
          <p><strong>Commit:</strong> <code>{{ selectedJob.commit_sha || "-" }}</code></p>
          <p v-if="selectedJob.download_url">
            <strong>Download:</strong>
            <a :href="selectedJob.download_url">Download ZIP package</a>
          </p>
          <p v-if="selectedJob.error_message" class="error">{{ selectedJob.error_message }}</p>

          <h4>Events</h4>
          <ul class="events">
            <li v-for="event in events" :key="event.id">
              <time>{{ new Date(event.created_at).toLocaleTimeString() }}</time>
              <span :class="event.level">{{ event.message }}</span>
            </li>
          </ul>
        </div>
      </section>
    </main>
  </div>
</template>
