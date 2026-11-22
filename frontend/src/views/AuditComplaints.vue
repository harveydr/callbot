<template>
  <div>
    <h1>投诉与调证</h1>
    <el-form @submit.prevent="createComplaint">
      <el-input v-model="phone" placeholder="手机号" />
      <el-input v-model="detail" placeholder="投诉内容" />
      <el-button native-type="submit">登记投诉并写入禁呼</el-button>
    </el-form>
    <p v-if="ticket">请在 {{ respondLabel }}（北京时间）前响应，并在 {{ resolveLabel }}（北京时间）前反馈处置结果。</p>
    <el-form v-if="ticket && ticket.status !== 'closed'" @submit.prevent="closeComplaint">
      <el-input v-model="disposition" placeholder="处置结果" />
      <el-button native-type="submit">反馈处置结果并结案</el-button>
    </el-form>
    <p v-if="ticket && ticket.status === 'closed'">已结案：{{ ticket.disposition }}</p>
    <p v-if="error">{{ error }}</p>
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
import { computed, ref } from "vue";
import { api } from "../api/client";

const phone = ref("");
const detail = ref("");
const disposition = ref("");
const password = ref("");
const confirming = ref(false);
const pack = ref("");
const ticket = ref(null);
const error = ref("");

function shanghai(iso) {
  return new Date(iso).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

const respondLabel = computed(() => (ticket.value ? shanghai(ticket.value.respond_due_at) : ""));
const resolveLabel = computed(() => (ticket.value ? shanghai(ticket.value.resolve_due_at) : ""));

async function createComplaint() {
  error.value = "";
  try {
    const response = await api.post(
      "/api/t/complaints",
      { phone: phone.value, detail: detail.value },
      { headers: { "Idempotency-Key": crypto.randomUUID() } },
    );
    ticket.value = response.data.data;
  } catch (exc) {
    error.value = exc.response?.data?.message || "登记失败";
  }
}

async function closeComplaint() {
  error.value = "";
  try {
    const response = await api.patch(`/api/t/complaints/${ticket.value.id}`, {
      version: ticket.value.version,
      status: "closed",
      disposition: disposition.value,
    });
    ticket.value = response.data.data;
  } catch (exc) {
    error.value = exc.response?.data?.message || "结案失败";
  }
}

async function exportPack() {
  const response = await api.post("/api/t/audit/evidence-pack", { password: password.value, confirm: true });
  pack.value = response.data.data.token;
  confirming.value = false;
}
</script>
