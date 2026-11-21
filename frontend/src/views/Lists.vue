<template>
  <div>
    <h1>名单与授权</h1>
    <el-form inline @submit.prevent="createList">
      <el-input v-model="name" placeholder="名单名称" />
      <el-button native-type="submit">创建名单</el-button>
    </el-form>
    <input type="file" accept=".csv,text/csv" @change="onFile" />
    <el-button :disabled="!listId || !file" @click="upload">导入</el-button>
    <StateView :status="status" :message="message" @retry="() => {}">
      <el-descriptions v-if="result" :column="2" border>
        <el-descriptions-item label="可拨打">{{ result.dialable }}</el-descriptions-item>
        <el-descriptions-item label="无授权">{{ result.no_consent }}</el-descriptions-item>
        <el-descriptions-item label="禁呼">{{ result.dnd_hit }}</el-descriptions-item>
        <el-descriptions-item label="格式错误">{{ result.invalid_format }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="result?.errors || []">
        <el-table-column prop="row" label="行" />
        <el-table-column prop="reason" label="原因" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import { ref } from "vue";
import StateView from "../components/StateView.vue";
import { api } from "../api/client";

const name = ref("");
const listId = ref("");
const file = ref(null);
const status = ref("empty");
const message = ref("");
const result = ref(null);

async function createList() {
  const response = await api.post("/api/t/lists", { name: name.value, scenario_code: "notification" });
  listId.value = response.data.data.id;
  status.value = "empty";
}

function onFile(event) {
  file.value = event.target.files[0] || null;
}

async function upload() {
  status.value = "loading";
  const body = new FormData();
  body.append("file", file.value);
  try {
    const response = await api.post(`/api/t/lists/${listId.value}/import`, body);
    result.value = response.data.data;
    status.value = "ready";
  } catch (err) {
    status.value = "error";
    message.value = err.response?.data?.message || "导入失败";
  }
}
</script>
