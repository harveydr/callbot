<template>
  <div>
    <h1>控制台</h1>
    <StateView :status="status" :message="message" @retry="load">
      <el-row :gutter="12">
        <el-col :span="8"><el-statistic title="呼叫" :value="data.calls || 0" /></el-col>
        <el-col :span="8"><el-statistic title="完成" :value="data.completed || 0" /></el-col>
        <el-col :span="8"><el-statistic title="合规拦截" :value="data.blocked_compliance || 0" /></el-col>
      </el-row>
      <el-row :gutter="12" style="margin-top: 16px">
        <el-col :span="8"><p class="metric"><span>接通率</span><strong>{{ percent(data.answer_rate) }}</strong></p></el-col>
        <el-col :span="8"><p class="metric"><span>拒接率</span><strong>{{ percent(data.reject_rate) }}</strong></p></el-col>
        <el-col :span="8"><p class="metric"><span>AI 独立完成率</span><strong>{{ percent(data.ai_complete_rate) }}</strong></p></el-col>
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

function percent(rate) {
  if (rate === null || rate === undefined || rate === "") return "—";
  return `${(Number(rate) * 100).toFixed(1)}%`;
}

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

<style scoped>
.metric {
  margin: 0;
  padding: 12px 0;
}
.metric span {
  display: block;
  color: #909399;
  font-size: 12px;
}
.metric strong {
  font-size: 24px;
  font-weight: 500;
}
</style>
