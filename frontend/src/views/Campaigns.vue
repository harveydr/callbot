<template>
  <div>
    <h1>外呼任务</h1>
    <StateView :status="status" :message="message" @retry="load">
      <el-table :data="items" @row-click="open">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="status" label="状态" />
        <el-table-column prop="voice_mode" label="模式" />
        <el-table-column prop="version" label="版本" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import StateView from "../components/StateView.vue";
import { api } from "../api/client";

const status = ref("loading");
const message = ref("");
const items = ref([]);
const router = useRouter();

async function load() {
  status.value = "loading";
  try {
    const response = await api.get("/api/t/campaigns", { params: { page: 1, page_size: 20 } });
    items.value = response.data.data.items;
    status.value = items.value.length ? "ready" : "empty";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

function open(row) {
  router.push(`/t/campaigns/${row.id}`);
}

load();
</script>
