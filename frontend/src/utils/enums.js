export const roleLabels = {
  platform_admin: "平台管理员",
  tenant_admin: "租户管理员",
  operator: "外呼运营",
  agent: "坐席",
  auditor: "合规审计",
};

export const campaignStatusLabels = {
  draft: "草稿",
  pending_review: "待审核",
  approved: "已批准",
  running: "执行中",
  paused: "已暂停",
  completed: "已完成",
  aborted: "已终止",
};

export const campaignStatusOrder = ["draft", "pending_review", "approved", "running", "paused", "completed", "aborted"];

export const templateStatusLabels = {
  draft: "草稿",
  pending_review: "待审核",
  approved: "已发布",
  retired: "已下线",
};

export const callStatusLabels = {
  queued: "排队中",
  dialing: "拨号中",
  ringing: "振铃",
  answered: "已接通",
  completed: "已完成",
  rejected: "拒接",
  blocked_compliance: "合规拦截",
  failed: "失败",
  transferring: "转接中",
  transfer_failed: "转接失败",
};

export const trunkStatusLabels = {
  inactive: "未激活",
  active: "已激活",
  blocked: "已封禁",
};

export const tenantStatusLabels = {
  pending: "待审核",
  active: "正常",
  suspended: "已暂停",
  closed: "已关闭",
};

export const importStatusLabels = {
  pending: "排队中",
  running: "处理中",
  done: "完成",
  failed: "失败",
};

export const nodeTypeLabels = {
  mandatory: "固定话术",
  llm_free: "大模型自由应答",
  gate: "合规闸门",
};

export const complaintStatusLabels = {
  open: "处理中",
  closed: "已结案",
};

export function labelOf(map, value) {
  if (value === null || value === undefined || value === "") return "—";
  return map[value] || String(value);
}

export function tagTypeForCampaign(status) {
  if (status === "running") return "success";
  if (status === "aborted" || status === "paused") return "warning";
  if (status === "draft" || status === "pending_review") return "info";
  return "";
}