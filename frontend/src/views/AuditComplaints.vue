<template>
  <div>
    <h1>投诉与调证</h1>
    <el-form @submit.prevent="createComplaint">
      <el-input v-model="phone" placeholder="手机号" />
      <el-input v-model="detail" placeholder="投诉内容" />
      <el-button native-type="submit">登记投诉并写入禁呼</el-button>
    </el-form>
    <el-dialog v-model="confirming" title="导出调证包需要再次确认">
      <el-input v-model="password" type="password" show-password placeholder="当前密码" />
      <template #footer>
        <el-button @click="confirming = false">取消</el-button>
        <el-button type="primary" @click="exportPack">确认导出</el-button>
      </template>
    </el-dialog>
    <el-button @click="confirming = true">导出调证包</el-button>
    <p v-if="pack">一次性链接令牌已生成，30 分钟内有效。</p>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { api } from "../api/client";

const phone = ref("");
const detail = ref("");
const password = ref("");
const confirming = ref(false);
const pack = ref("");

async function createComplaint() {
  await api.post(
    "/api/t/complaints",
    { phone: phone.value, detail: detail.value },
    { headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
}

async function exportPack() {
  const response = await api.post("/api/t/audit/evidence-pack", { password: password.value, confirm: true });
  pack.value = response.data.data.token;
  confirming.value = false;
}
</script>
