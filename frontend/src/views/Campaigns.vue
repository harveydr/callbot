<template>
  <div>
    <PageHeader title="外呼任务" description="创建任务、提交审核并在批准后安全启动。">
      <template #actions>
        <el-input v-model="search" placeholder="搜索任务名称" clearable style="width: 200px" @input="onSearch" />
        <el-button v-if="canWrite" type="primary" @click="showCreate = true">新建任务</el-button>
      </template>
    </PageHeader>
    <StateView :status="status" :message="message" :stale="stale" empty-text="暂无外呼任务" @retry="load(0)">
      <template #empty>
        <el-button v-if="canWrite" type="primary" @click="showCreate = true">新建外呼任务</el-button>
        <el-button @click="load(0)">刷新</el-button>
      </template>
      <el-table :data="items" row-key="id" highlight-current-row @row-click="open">
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <EnumTag :value="row.status" :labels="campaignStatusLabels" :type-map="statusTypeMap" />
          </template>
        </el-table-column>
        <el-table-column prop="voice_mode" label="语音模式" width="120" />
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="open(row)">详情</el-button>
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

    <el-dialog v-model="showCreate" title="新建外呼任务" width="560px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="任务名称" required><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="场景代码" required><el-input v-model="form.scenario_code" placeholder="如 notification" /></el-form-item>
        <el-form-item label="话术模板" required>
          <el-select v-model="form.template_id" filterable style="width: 100%">
            <el-option v-for="t in templates" :key="t.id" :label="`${t.name} (${t.status})`" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名单 ID" required>
          <el-input v-model="form.list_id" placeholder="在名单导入页创建后填入，或使用最近名单" />
          <p v-if="lastListId" class="ed-muted">最近创建：{{ lastListId }} <el-button link @click="form.list_id = lastListId">填入</el-button></p>
        </el-form-item>
        <el-form-item label="线路" required>
          <el-select v-model="form.trunk_id" filterable style="width: 100%">
            <el-option v-for="t in trunks" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="外呼窗口"><el-input v-model="form.call_window" placeholder="09:00-19:00" /></el-form-item>
        <el-row :gutter="8">
          <el-col :span="12"><el-form-item label="开始时间"><el-date-picker v-model="form.start_at" type="datetime" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="结束时间"><el-date-picker v-model="form.end_at" type="datetime" style="width: 100%" /></el-form-item></el-col>
        </el-row>
      </el-form>
      <el-alert v-if="createError" :title="createError" type="error" :closable="false" />
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createCampaign">创建草稿</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { api } from "../api/client";
import { useListFetch } from "../composables/useListFetch";
import { useSession } from "../stores/session";
import { campaignStatusLabels } from "../utils/enums";
import { canWriteCampaign } from "../utils/permissions";

const router = useRouter();
const session = useSession();
const canWrite = computed(() => canWriteCampaign(session.role));
const lastListId = ref(localStorage.getItem("echodesk-last-list-id") || "");
const showCreate = ref(false);
const creating = ref(false);
const createError = ref("");
const templates = ref([]);
const trunks = ref([]);

const statusTypeMap = {
  running: "success",
  approved: "success",
  paused: "warning",
  aborted: "danger",
  pending_review: "info",
  draft: "info",
};

const {
  PAGE_SIZES,
  status,
  message,
  stale,
  items,
  total,
  page,
  pageSize,
  search,
  load,
  onSearch,
  setPage,
  setPageSize,
} = useListFetch("/api/t/campaigns", {
  clientFilter: (row, q) => String(row.name || "").toLowerCase().includes(q),
});

const form = ref({
  name: "",
  scenario_code: "notification",
  template_id: "",
  list_id: "",
  trunk_id: "",
  call_window: "09:00-19:00",
  start_at: new Date(),
  end_at: new Date(Date.now() + 7 * 86400000),
  max_concurrency: 2,
  freq_per_phone_daily: 1,
  freq_per_phone_7d: 3,
  freq_per_phone_30d: 6,
});

onMounted(async () => {
  try {
    const [tpl, trk] = await Promise.all([api.get("/api/t/templates"), api.get("/api/t/trunks")]);
    templates.value = tpl.data.data.items.filter((t) => t.status === "approved");
    trunks.value = trk.data.data.items;
  } catch {
    /* ignore */
  }
});

function open(row) {
  router.push(`/t/campaigns/${row.id}`);
}

function toIso(v) {
  if (!v) return null;
  const d = v instanceof Date ? v : new Date(v);
  return d.toISOString();
}

async function createCampaign() {
  createError.value = "";
  creating.value = true;
  try {
    const body = { ...form.value, start_at: toIso(form.value.start_at), end_at: toIso(form.value.end_at) };
    const response = await api.post("/api/t/campaigns", body, { headers: { "Idempotency-Key": crypto.randomUUID() } });
    ElMessage.success("任务草稿已创建");
    showCreate.value = false;
    router.push(`/t/campaigns/${response.data.data.id}`);
  } catch (err) {
    createError.value = err.response?.data?.message || "创建失败";
  } finally {
    creating.value = false;
  }
}
</script>