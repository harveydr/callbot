<template>
  <div>
    <h1>任务详情</h1>
    <StateView :status="status" :message="message" @retry="load">
      <p>状态：{{ campaign.status }}，话术版本 {{ campaign.template_version }}，任务数 {{ campaign.tasks }}</p>
      <el-button v-if="campaign.status === 'approved'" type="primary" @click="act('start')">启动</el-button>
      <el-button v-if="campaign.status === 'running'" @click="act('pause')">暂停</el-button>
      <el-button v-if="campaign.status === 'running' || campaign.status === 'paused'" type="danger" @click="act('abort')">终止</el-button>
      <h2>呼叫</h2>
      <el-table :data="calls">
        <el-table-column prop="status" label="状态" />
        <el-table-column prop="block_reason" label="拦截原因" />
        <el-table-column prop="template_version" label="话术版本" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRoute } from "vue-router";
import StateView from "../components/StateView.vue";
import { api } from "../api/client";

const route = useRoute();
const status = ref("loading");
const message = ref("");
const campaign = ref({});
const calls = ref([]);

async function load() {
  status.value = "loading";
  try {
    const response = await api.get(`/api/t/campaigns/${route.params.id}`);
    campaign.value = response.data.data;
    const stats = await api.get("/api/t/calls", { params: { page: 1, page_size: 20 } });
    calls.value = stats.data.data.items;
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

async function act(name) {
  await api.post(`/api/t/campaigns/${route.params.id}/${name}`, {}, { headers: { "Idempotency-Key": crypto.randomUUID() } });
  await load();
}

load();
</script>
