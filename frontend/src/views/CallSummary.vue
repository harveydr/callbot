<template>
  <div>
    <h1>通话小结</h1>
    <StateView :status="status" :message="message" @retry="load">
      <p v-if="summary">{{ summary }}</p>
      <p v-else>{{ note }}</p>
      <p v-if="explicitLabel" class="aigc">{{ explicitLabel }}</p>
      <audio v-if="audioSrc" :src="audioSrc" controls></audio>
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
const summary = ref("");
const audioSrc = ref("");
const explicitLabel = ref("");
const turns = ref([]);

async function load() {
  try {
    const response = await api.get(`/api/t/calls/${route.params.id}/transcript`);
    note.value = response.data.data.note;
    summary.value = response.data.data.summary || "";
    const recording = response.data.data.recording;
    const media = recording?.media_base64;
    explicitLabel.value = recording?.explicit_label || "";
    audioSrc.value = media ? `data:audio/wav;base64,${media}` : "";
    turns.value = response.data.data.body?.turns || [];
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

load();
</script>

<style scoped>
.aigc {
  color: #606266;
}
</style>
