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
});
const flash = ref("");

const llmForm = reactive({
  provider: "perplexity",
  api_key: "",
  model: "sonar-pro",
});

const jobForm = reactive({
  title: "",
  repoName: "",
  visibility: "public",
  brief: "",
  enablePages: true,
  branch: "main",
  deployPath: "/",
});

const attachmentFiles = ref([]);
const pollHandle = ref(null);

const canCreateJob = computed(() => integrations.value.github.connected && integrations.value.llm.configured);

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

async function bootstrap() {
  busy.bootstrap = true;
  try {
    session.value = await api("/api/session");
    integrations.value = await api("/api/integrations");
    const jobResponse = await api("/api/jobs");
    jobs.value = jobResponse.jobs;

    if (jobs.value.length > 0) {
      selectedJobId.value = jobs.value[0].job_id;
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
}

async function startGithubAuth() {
  flash.value = "";
  try {
    const data = await api("/api/auth/github/start");
    window.location.href = data.url;
  } catch (error) {
    flash.value = `GitHub OAuth start failed: ${error.message}`;
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
    flash.value = "LLM provider configured for this session.";
  } catch (error) {
    flash.value = `Failed to save LLM config: ${error.message}`;
  } finally {
    busy.savingLlm = false;
  }
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
      repo: {
        name: jobForm.repoName,
        visibility: jobForm.visibility,
      },
      deployment: {
        enable_pages: jobForm.enablePages,
        branch: jobForm.branch,
        path: jobForm.deployPath,
      },
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

    const jobResponse = await api("/api/jobs");
    jobs.value = jobResponse.jobs;
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
    jobs.value = jobResponse.jobs;
    if (selectedJobId.value) {
      const stillExists = jobs.value.find((job) => job.job_id === selectedJobId.value);
      if (!stillExists) {
        selectedJobId.value = "";
        selectedJob.value = null;
      }
    }
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
            <h3>GitHub OAuth</h3>
            <p v-if="integrations.github.connected">
              Connected as <strong>{{ integrations.github.username }}</strong>
            </p>
            <p v-else>Not connected</p>
          </div>
          <div class="row-actions">
            <button v-if="!integrations.github.connected" @click="startGithubAuth">
              Connect GitHub
            </button>
            <button v-else class="ghost" @click="disconnectGithub">Disconnect</button>
          </div>
        </div>

        <form class="stack" @submit.prevent="saveLlmConfig">
          <h3>LLM Provider</h3>
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
      </section>

      <section class="panel">
        <h2>Create Job</h2>
        <form class="stack" @submit.prevent="createJob">
          <label>
            Title
            <input v-model="jobForm.title" type="text" required />
          </label>
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
          <label>
            Brief
            <textarea v-model="jobForm.brief" rows="7" required></textarea>
          </label>

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
            <input v-model="jobForm.enablePages" type="checkbox" />
            Enable GitHub Pages after push
          </label>

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
          <button class="ghost" @click="refreshJobs">Refresh</button>
        </div>

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
          <p><strong>Repo:</strong> <a :href="selectedJob.repo_url" target="_blank">{{ selectedJob.repo_url || "-" }}</a></p>
          <p><strong>Pages:</strong> <a :href="selectedJob.pages_url" target="_blank">{{ selectedJob.pages_url || "-" }}</a></p>
          <p><strong>Commit:</strong> <code>{{ selectedJob.commit_sha || "-" }}</code></p>
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
