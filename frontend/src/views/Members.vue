<template>
  <div>
    <PageHeader title="成员与权限" description="邀请租户成员并分配角色。">
      <template #actions>
        <el-button type="primary" @click="showInvite = true">邀请成员</el-button>
      </template>
    </PageHeader>
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="username" label="账号" />
        <el-table-column label="角色">
          <template #default="{ row }">{{ roleLabels[row.role] || row.role }}</template>
        </el-table-column>
        <el-table-column prop="id" label="用户 ID" min-width="200" />
        <el-table-column prop="status" label="状态" width="100" />
      </el-table>
      <el-pagination
        style="margin-top: 12px"
        layout="total, sizes, prev, pager, next"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        :page-sizes="PAGE_SIZES"
        @current-change="setPage"
        @size-change="setPageSize"
      />
    </StateView>
    <el-dialog v-model="showInvite" title="邀请成员" width="480px">
      <el-form label-position="top">
        <el-form-item label="账号"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option value="operator" label="外呼运营" />
            <el-option value="agent" label="坐席" />
            <el-option value="auditor" label="合规审计" />
            <el-option value="tenant_admin" label="租户管理员" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
      </el-form>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" />
      <template #footer>
        <el-button @click="showInvite = false">取消</el-button>
        <el-button type="primary" @click="invite">邀请</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from "vue";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";
import { useListFetch } from "../composables/useListFetch";
import { roleLabels } from "../utils/enums";

const showInvite = ref(false);
const formError = ref("");
const form = ref({ username: "", display_name: "", role: "operator", password: "" });

const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch("/api/t/members");

async function invite() {
  formError.value = "";
  try {
    await api.post("/api/t/members", form.value, { headers: { "Idempotency-Key": crypto.randomUUID() } });
    showInvite.value = false;
    load(0);
  } catch (err) {
    formError.value = err.response?.data?.message || "邀请失败";
  }
}
</script>