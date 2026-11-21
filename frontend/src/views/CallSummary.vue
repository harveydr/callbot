<template>
  <div>
    <h1>通话小结</h1>
    <StateView :status="status" :message="message" @retry="load">
      <p>{{ note }}</p>
      <el-table :data="turns">
        <el-table-column prop="speaker" label="说话方" />
        <el-table-column prop="text" label="内容" />
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
const note = ref("");
const turns = ref([]);

async function load() {
  try {
    const response = await api.get(`/api/t/calls/${route.params.id}/transcript`);
    note.value = response.data.data.note;
    turns.value = response.data.data.body?.turns || [];
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

load();
</script>
