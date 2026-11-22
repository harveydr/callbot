<template>
  <div>
    <PageHeader title="话术节点编辑" description="结构化编辑 mandatory / llm_free / gate 节点，保留 30 秒本地草稿。" />
    <el-alert v-if="draftSaved" :title="`本地草稿已保存于 ${draftSaved}`" type="info" :closable="false" style="margin-bottom: 12px" />
    <el-collapse v-model="openKeys">
      <el-collapse-item v-for="(node, index) in nodes" :key="index" :name="String(index)" :title="node.node_key || `节点 ${index + 1}`">
        <el-form label-position="top">
          <el-form-item label="node_key" required><el-input v-model="node.node_key" /></el-form-item>
          <el-form-item label="类型" required>
            <el-select v-model="node.type" style="width: 100%">
              <el-option value="mandatory" :label="nodeTypeLabels.mandatory" />
              <el-option value="llm_free" :label="nodeTypeLabels.llm_free" />
              <el-option value="gate" :label="nodeTypeLabels.gate" />
            </el-select>
          </el-form-item>
          <el-form-item label="内容"><el-input v-model="node.content" type="textarea" :rows="4" /></el-form-item>
        </el-form>
        <el-button type="danger" link @click="removeNode(index)">删除节点</el-button>
      </el-collapse-item>
    </el-collapse>
    <el-button style="margin-top: 12px" @click="addNode">添加节点</el-button>
    <el-button type="primary" style="margin-top: 12px; margin-left: 8px" :loading="saving" @click="save">提交保存</el-button>
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
    <el-alert v-if="success" title="保存成功" type="success" :closable="false" style="margin-top: 12px" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { onBeforeRouteLeave, useRoute } from "vue-router";
import { ElMessageBox } from "element-plus";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";
import { nodeTypeLabels } from "../utils/enums";

const route = useRoute();
const nodes = ref([]);
const openKeys = ref(["0"]);
const error = ref("");
const success = ref(false);
const saving = ref(false);
const dirty = ref(false);
const draftSaved = ref("");
const key = `draft:template:${route.params.id}`;
let timer = 0;

function syncDraft() {
  localStorage.setItem(key, JSON.stringify(nodes.value));
  draftSaved.value = new Date().toLocaleTimeString();
  dirty.value = true;
}

onMounted(async () => {
  const draft = localStorage.getItem(key);
  if (draft) {
    try {
      nodes.value = JSON.parse(draft);
    } catch {
      nodes.value = [];
    }
  }
  try {
    const response = await api.get(`/api/t/templates/${route.params.id}`);
    if (!draft) nodes.value = response.data.data.nodes || [];
  } catch (err) {
    error.value = err.response?.data?.message || "无法加载话术";
  }
  timer = window.setInterval(syncDraft, 30000);
});

onBeforeUnmount(() => window.clearInterval(timer));

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm("有未提交的节点修改，确定离开？", "离开确认");
    return true;
  } catch {
    return false;
  }
});

function addNode() {
  nodes.value.push({ node_key: "", type: "mandatory", content: "" });
  openKeys.value = [String(nodes.value.length - 1)];
}

function removeNode(index) {
  nodes.value.splice(index, 1);
}

function validate() {
  for (const node of nodes.value) {
    if (!node.node_key?.trim()) return "node_key 不能为空";
    if (!["mandatory", "llm_free", "gate"].includes(node.type)) return "节点类型无效";
  }
  return "";
}

async function save() {
  const issue = validate();
  if (issue) {
    error.value = issue;
    return;
  }
  error.value = "";
  success.value = false;
  saving.value = true;
  try {
    await api.put(`/api/t/templates/${route.params.id}/nodes`, { nodes: nodes.value });
    localStorage.removeItem(key);
    dirty.value = false;
    success.value = true;
  } catch (err) {
    syncDraft();
    error.value = err.response?.data?.message || "提交失败，草稿已保留";
  } finally {
    saving.value = false;
  }
}
</script>