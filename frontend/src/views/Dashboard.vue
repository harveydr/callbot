<template>
  <div>
    <h1>控制台</h1>
    <StateView :status="status" :message="message" @retry="load">
      <el-row :gutter="12">
        <el-col :span="8"><el-statistic title="呼叫" :value="data.calls || 0" /></el-col>
        <el-col :span="8"><el-statistic title="完成" :value="data.completed || 0" /></el-col>
        <el-col :span="8"><el-statistic title="合规拦截" :value="data.blocked_compliance || 0" /></el-col>
      </el-row>
    </StateView>
  </div>
</template>

<script setup>
import { ref } from "vue";
import StateView from "../components/StateView.vue";
import { api } from "../api/client";

const status = ref("loading");
const message = ref("");
const data = ref({});

async function load() {
  status.value = "loading";
  try {
    const response = await api.get("/api/t/reports/overview");
    data.value = response.data.data;
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

load();
</script>
