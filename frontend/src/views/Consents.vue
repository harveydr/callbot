<template>
  <div>
    <PageHeader title="授权台账" description="登记与撤销外呼授权（仅展示后四位）。">
      <template #actions>
        <el-button v-if="canWrite" type="primary" @click="showCreate = true">登记授权</el-button>
      </template>
    </PageHeader>
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="phone_last4" label="手机号后四位" width="140" />
        <el-table-column prop="scenario_code" label="场景" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><el-tag :type="row.revoked ? 'danger' : 'success'" size="small">{{ row.revoked ? "已撤销" : "有效" }}</el-tag></template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="操作" width="100">
          <template #default="{ row }">
            <el-button v-if="!row.revoked" link type="danger" @click="revoke(row)">撤销</el-button>
          </template>
        </el-table-column>
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
    <el-dialog v-model="showCreate" title="登记授权" width="480px">
      <el-form label-position="top">
        <el-form-item label="手机号"><el-input v-model="form.phone" /></el-form-item>
        <el-form-item label="场景"><el-input v-model="form.scenario_code" /></el-form-item>
        <el-form-item label="来源"><el-input v-model="form.source" placeholder="web_form / ivr 等" /></el-form-item>
        <el-form-item label="授权时间"><el-date-picker v-model="form.consented_at" type="datetime" style="width: 100%" /></el-form-item>
      </el-form>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" />
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="register">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { ElMessageBox } from "element-plus";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import { api } from "../api/client";
import { useListFetch } from "../composables/useListFetch";
import { useSession } from "../stores/session";
import { canImportLists } from "../utils/permissions";

const session = useSession();
const canWrite = computed(() => canImportLists(session.role));
const showCreate = ref(false);
const formError = ref("");
const form = ref({ phone: "", scenario_code: "notification", source: "operator_manual", consented_at: new Date() });

const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch("/api/t/consents");

async function register() {
  formError.value = "";
  try {
    await api.post("/api/t/consents", {
      ...form.value,
      consented_at: new Date(form.value.consented_at).toISOString(),
    });
    showCreate.value = false;
    load(0);
  } catch (err) {
    formError.value = err.response?.data?.message || "登记失败";
  }
}

async function revoke(row) {
  try {
    await ElMessageBox.confirm("确认撤销该授权记录？", "撤销确认", { type: "warning" });
    await api.post(`/api/t/consents/${row.id}/revoke`);
    load(0);
  } catch {
    /* cancel */
  }
}
</script>