<template>
  <div>
    <PageHeader title="通话小结" description="坐席只读查看 AI 小结与轮次；无写入小结 API。" />
    <StateView :status="status" :message="message" @retry="load">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="通话状态"><EnumTag :value="call.status" :labels="callStatusLabels" /></el-descriptions-item>
        <el-descriptions-item label="时长(秒)">{{ call.duration_sec ?? "—" }}</el-descriptions-item>
        <el-descriptions-item label="AI 小结" :span="2">{{ summary || "生成中" }}</el-descriptions-item>
      </el-descriptions>
      <template v-if="canTranscript">
        <p v-if="explicitLabel" class="ed-muted">{{ explicitLabel }}</p>
        <p v-if="recordingMissing">录音未落盘，需人工跟进</p>
        <audio v-if="audioSrc" :src="audioSrc" controls style="margin: 12px 0" />
      </template>
      <el-alert v-else type="info" :closable="false" title="当前角色不可访问录音与完整转写接口，仅展示通话详情字段。" style="margin: 12px 0" />
      <h3>对话轮次</h3>
      <el-table :data="turns">
        <el-table-column prop="speaker" label="说话方" width="120" />
        <el-table-column prop="text" label="内容" />
      </el-table>
    </StateView>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import StateView from "../components/StateView.vue";
import PageHeader from "../components/PageHeader.vue";
import EnumTag from "../components/EnumTag.vue";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import { callStatusLabels } from "../utils/enums";

const route = useRoute();
const session = useSession();
const status = ref("loading");
const message = ref("");
const call = ref({});
const summary = ref("");
const turns = ref([]);
const audioSrc = ref("");
const explicitLabel = ref("");
const recordingMissing = ref(false);

const canTranscript = computed(() => ["tenant_admin", "operator", "auditor"].includes(session.role));

async function load() {
  status.value = "loading";
  try {
    const response = await api.get(`/api/t/calls/${route.params.id}`);
    call.value = response.data.data;
    summary.value = call.value.summary || "";
    turns.value = call.value.turns || [];
    if (canTranscript.value) {
      try {
        const tr = await api.get(`/api/t/calls/${route.params.id}/transcript`);
        if (tr.data.data.summary) summary.value = tr.data.data.summary;
        turns.value = tr.data.data.body?.turns || turns.value;
        const recording = tr.data.data.recording;
        explicitLabel.value = recording?.explicit_label || "";
        recordingMissing.value = Boolean(tr.data.data.body) && !recording;
        audioSrc.value = recording?.media_base64 ? `data:audio/wav;base64,${recording.media_base64}` : "";
      } catch {
        /* agent 403 expected */
      }
    }
    status.value = "ready";
  } catch (err) {
    status.value = err.response?.status === 403 ? "forbidden" : "error";
    message.value = err.response?.data?.message || "加载失败";
  }
}

load();
</script>