<template>
  <div>
    <PageHeader title="禁呼名单" description="手动录入与删除（删除需二次确认）。">
      <template #actions>
        <el-button v-if="canWrite" type="primary" @click="showAdd = true">添加禁呼</el-button>
      </template>
    </PageHeader>
    <StateView :status="status" :message="message" :stale="stale" @retry="() => load(0)">
      <el-table :data="items">
        <el-table-column prop="phone_last4" label="后四位" width="100" />
        <el-table-column prop="source" label="来源" />
        <el-table-column v-if="isAdmin" label="操作" width="100">
          <template #default="{ row }">
            <el-button v-if="row.source === 'operator_manual'" link type="danger" @click="remove(row)">删除</el-button>
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
    <el-dialog v-model="showAdd" title="添加禁呼号码" width="420px">
      <el-input v-model="phone" placeholder="11 位手机号" />
      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" @click="add">添加</el-button>
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
import { canManageDnd } from "../utils/permissions";

const session = useSession();
const canWrite = computed(() => canManageDnd(session.role));
const isAdmin = computed(() => session.role === "tenant_admin");
const showAdd = ref(false);
const phone = ref("");

const { PAGE_SIZES, status, message, stale, items, total, page, pageSize, load, setPage, setPageSize } = useListFetch("/api/t/dnd");

async function add() {
  try {
    await api.post("/api/t/dnd", { phone: phone.value, source: "operator_manual" });
    showAdd.value = false;
    phone.value = "";
    load(0);
  } catch (err) {
    await ElMessageBox.alert(err.response?.data?.message || "添加失败", "错误");
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm("再次确认：删除该手动禁呼条目？", "删除确认", { type: "warning" });
    await api.delete("/api/t/dnd", { params: { entry_id: row.id } });
    load(0);
  } catch {
    /* cancelled */
  }
}
</script>