<template>
  <div>
    <PageHeader :title="campaign.name || '任务详情'" description="按状态机推进：草稿 → 待审 → 批准 → 执行。">
      <template #actions>
        <el-button @click="$router.push('/t/campaigns')">返回列表</el-button>
      </template>
    </PageHeader>
    <StateView :status="status" :message="message" @retry="load">
      <el-steps :active="stepIndex" finish-status="success" align-center style="margin-bottom: 16px">
        <el-step v-for="s in steps" :key="s" :title="campaignStatusLabels[s]" />
      </el-steps>
      <div class="ed-card">
        <p>
          当前状态：<EnumTag :value="campaign.status" :labels="campaignStatusLabels" />
          · 话术版本 {{ campaign.template_version }} · 任务数 {{ campaign.tasks }}
        </p>
        <div v-if="writable" class="ed-table-actions" style="margin-top: 12px">
          <el-button v-if="campaign.status === 'draft'" @click="submitReview">提交审核</el-button>
          <el-button v-if="campaign.status === 'pending_review' && canApprove" type="primary" @click="approve">批准</el-button>
          <el-button v-if="campaign.status === 'approved'" type="primary" @click="confirmAct('start', '确认启动外呼？将按合规窗口与频次闸门执行。')">启动</el-button>
          <el-button v-if="campaign.status === 'running'" @click="confirmAct('pause', '确认暂停任务？在途通话不受影响。')">暂停</el-button>
          <el-button
            v-if="['running', 'paused', 'approved'].includes(campaign.status)"
            type="danger"
            @click="confirmAct('abort', '确认终止任务？此操作不可恢复。')"
          >终止</el-button>
        </div>
        <el-alert v-else type="info" :closable="false" title="当前为只读视图（审计角色不可操作）" style="margin-top: 12px" />
        <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" style="margin-top: 12px" />
      </div>

      <h2 style="margin-top: 20px">呼叫统计</h2>
      <div class="ed-metric-grid">
        <div v-for="(count, key) in stats" :key="key" class="ed-metric">
          <span>{{ callStatusLabels[key] || key }}</span>
          <strong>{{ count }}</strong>
        </div>
      </div>
      <el-alert
        type="info"
        :closable="false"
        title="接口限制：尚无按 campaign_id 过滤的呼叫列表，此处仅展示本任务统计，不展示租户全量通话明细。"
        style="margin-top: 12px"
      />
    </StateView>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { ElMessageBox } from "element-plus";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import { callStatusLabels, campaignStatusLabels, campaignStatusOrder } from "../utils/enums";
import { canApproveCampaign, canWriteCampaign } from "../utils/permissions";

const route = useRoute();
const session = useSession();
const status = ref("loading");
const message = ref("");
const campaign = ref({});
const stats = ref({});
const actionError = ref("");
const writable = computed(() => canWriteCampaign(session.role));
const canApprove = computed(() => canApproveCampaign(session.role));
const steps = campaignStatusOrder.filter((s) => s !== "aborted");
const stepIndex = computed(() => Math.max(0, steps.indexOf(campaign.value.status)));

async function load() {
  status.value = "loading";
  actionError.value = "";
  try {
    const response = await api.get(`/api/t/campaigns/${route.params.id}`);
    campaign.value = response.data.data;
    const statRes = await api.get(`/api/t/campaigns/${route.params.id}/stats`);
    stats.value = statRes.data.data.by_status || {};
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

async function submitReview() {
  try {
    await api.post(`/api/t/campaigns/${route.params.id}/submit-review`);
    await load();
  } catch (err) {
    actionError.value = err.response?.data?.message || "提交审核失败";
  }
}

async function approve() {
  try {
    await api.patch(`/api/t/campaigns/${route.params.id}`, {
      version: campaign.value.version,
      status: "approved",
    });
    await load();
  } catch (err) {
    actionError.value = err.response?.data?.message || "批准失败";
  }
}

async function confirmAct(name, tip) {
  try {
    await ElMessageBox.confirm(tip, "操作确认", { type: "warning" });
  } catch {
    return;
  }
  try {
    await api.post(`/api/t/campaigns/${route.params.id}/${name}`, {}, { headers: { "Idempotency-Key": crypto.randomUUID() } });
    await load();
  } catch (err) {
    actionError.value = err.response?.data?.message || "操作失败";
  }
}

load();
</script>