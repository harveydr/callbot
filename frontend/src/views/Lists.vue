<template>
  <div>
    <PageHeader
      title="名单导入"
      description="三步向导：创建名单 → 上传 CSV → 轮询导入结果。后端暂无名单列表接口，仅能操作本次会话创建的名单。"
    />
    <el-steps :active="step" finish-status="success" align-center style="margin-bottom: 20px">
      <el-step title="创建名单" />
      <el-step title="上传文件" />
      <el-step title="查看结果" />
    </el-steps>

    <div v-if="step === 0" class="ed-card">
      <el-form label-position="top" @submit.prevent="createList">
        <el-form-item label="名单名称" required><el-input v-model="name" placeholder="如 11月通知批次" /></el-form-item>
        <el-form-item label="场景代码" required>
          <el-input v-model="scenario" placeholder="notification / telesales 等" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="creating">创建并继续</el-button>
      </el-form>
      <el-alert v-if="createError" :title="createError" type="error" :closable="false" style="margin-top: 12px" />
    </div>

    <div v-else-if="step === 1" class="ed-card">
      <p class="ed-muted">当前名单 ID：{{ listId }}</p>
      <el-upload :auto-upload="false" :limit="1" accept=".csv,text/csv" :on-change="onFile">
        <el-button>选择 CSV 文件</el-button>
      </el-upload>
      <el-button type="primary" style="margin-top: 12px" :disabled="!file" :loading="uploading" @click="upload">开始导入</el-button>
      <el-alert v-if="uploadError" :title="uploadError" type="error" :closable="false" style="margin-top: 12px" />
    </div>

    <StateView :status="status" :message="message" empty-text="上传文件后将显示导入进度" @retry="poll">
      <el-progress v-if="result" :percentage="progressPct" :status="progressStatus" style="margin-bottom: 12px" />
      <el-descriptions v-if="result" :column="2" border>
        <el-descriptions-item label="任务状态">{{ importStatusLabels[result.status] || result.status }}</el-descriptions-item>
        <el-descriptions-item label="总行数">{{ result.total ?? "—" }}</el-descriptions-item>
        <el-descriptions-item label="可拨打">{{ result.dialable ?? 0 }}</el-descriptions-item>
        <el-descriptions-item label="无授权">{{ result.no_consent ?? 0 }}</el-descriptions-item>
        <el-descriptions-item label="禁呼">{{ result.dnd_hit ?? 0 }}</el-descriptions-item>
        <el-descriptions-item label="格式错误">{{ result.invalid_format ?? 0 }}</el-descriptions-item>
      </el-descriptions>
      <h3 v-if="result?.errors?.length" style="margin-top: 16px">错误行</h3>
      <el-table v-if="result?.errors?.length" :data="result.errors">
        <el-table-column prop="row" label="行号" width="80" />
        <el-table-column prop="reason" label="原因" />
      </el-table>
      <el-button v-if="result && result.status === 'failed'" style="margin-top: 12px" @click="step = 1">重新上传</el-button>
    </StateView>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";
import { importStatusLabels } from "../utils/enums";

const step = ref(0);
const name = ref("");
const scenario = ref("notification");
const listId = ref("");
const file = ref(null);
const taskId = ref("");
const creating = ref(false);
const uploading = ref(false);
const createError = ref("");
const uploadError = ref("");
const status = ref("empty");
const message = ref("");
const result = ref(null);
let pollTimer = 0;

const progressPct = computed(() => {
  if (!result.value) return 0;
  if (result.value.status === "done") return 100;
  if (result.value.status === "failed") return 100;
  if (result.value.total) {
    const processed = (result.value.dialable || 0) + (result.value.no_consent || 0) + (result.value.dnd_hit || 0) + (result.value.invalid_format || 0);
    return Math.min(95, Math.round((processed / result.value.total) * 100));
  }
  return result.value.status === "running" ? 40 : 10;
});

const progressStatus = computed(() => {
  if (result.value?.status === "failed") return "exception";
  if (result.value?.status === "done") return "success";
  return "";
});

function onFile(uploadFile) {
  file.value = uploadFile.raw;
}

async function createList() {
  createError.value = "";
  creating.value = true;
  try {
    const response = await api.post("/api/t/lists", { name: name.value, scenario_code: scenario.value });
    listId.value = response.data.data.id;
    localStorage.setItem("echodesk-last-list-id", listId.value);
    step.value = 1;
  } catch (err) {
    createError.value = err.response?.data?.message || "创建名单失败";
  } finally {
    creating.value = false;
  }
}

async function upload() {
  uploadError.value = "";
  uploading.value = true;
  status.value = "loading";
  const body = new FormData();
  body.append("file", file.value);
  try {
    const response = await api.post(`/api/t/lists/${listId.value}/import`, body);
    taskId.value = response.data.data.task_id;
    result.value = response.data.data;
    step.value = 2;
    await poll();
    pollTimer = window.setInterval(poll, 1500);
  } catch (err) {
    status.value = "error";
    uploadError.value = err.response?.data?.message || "导入失败";
  } finally {
    uploading.value = false;
  }
}

async function poll() {
  if (!taskId.value) return;
  try {
    const response = await api.get(`/api/t/imports/${taskId.value}`);
    result.value = response.data.data;
    if (result.value.status === "done" || result.value.status === "failed") {
      window.clearInterval(pollTimer);
      status.value = "ready";
    } else {
      status.value = "loading";
    }
  } catch (err) {
    status.value = "error";
    message.value = err.response?.data?.message || "轮询失败";
    window.clearInterval(pollTimer);
  }
}
</script>