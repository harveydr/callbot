<template>
  <div>
    <h1>节点编辑</h1>
    <el-alert v-if="draftSaved" :title="`本地草稿已保存 ${draftSaved}`" type="info" :closable="false" />
    <el-input v-model="text" type="textarea" :rows="12" />
    <el-button type="primary" @click="save">提交</el-button>
    <p v-if="error">{{ error }}</p>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/client";

const route = useRoute();
const text = ref("[]");
const error = ref("");
const draftSaved = ref("");
const key = `draft:template:${route.params.id}`;
let timer = 0;

onMounted(async () => {
  const draft = localStorage.getItem(key);
  if (draft) text.value = draft;
  try {
    const response = await api.get(`/api/t/templates/${route.params.id}`);
    if (!draft) text.value = JSON.stringify(response.data.data.nodes, null, 2);
  } catch (err) {
    error.value = err.response?.data?.message || "无法加载话术";
  }
  timer = window.setInterval(() => {
    localStorage.setItem(key, text.value);
    draftSaved.value = new Date().toLocaleTimeString();
  }, 30000);
});

onBeforeUnmount(() => window.clearInterval(timer));

async function save() {
  error.value = "";
  try {
    await api.put(`/api/t/templates/${route.params.id}/nodes`, { nodes: JSON.parse(text.value) });
    localStorage.removeItem(key);
  } catch (err) {
    localStorage.setItem(key, text.value);
    error.value = err.response?.data?.message || "提交失败，草稿已保留";
  }
}
</script>
