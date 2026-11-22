import { roleLabels } from "./enums";

export function roleHome(role) {
  if (role === "platform_admin") return "/p/tenants";
  if (role === "agent") return "/agent/queue";
  return "/t/dashboard";
}

export function roleLabel(role) {
  return roleLabels[role] || role;
}

export function navGroups(role) {
  if (role === "platform_admin") {
    return [
      {
        title: "平台运营",
        items: [
          { to: "/p/tenants", label: "租户审核" },
          { to: "/p/trunks", label: "熔断状态" },
          { to: "/p/policies", label: "全局告警" },
          { to: "/p/billing", label: "平台指标" },
        ],
      },
    ];
  }

  const groups = [
    {
      title: "工作台",
      items: [
        { to: "/t/dashboard", label: "控制台" },
        { to: "/t/campaigns", label: "外呼任务", roles: ["tenant_admin", "operator", "auditor"] },
        { to: "/t/lists", label: "名单导入", roles: ["tenant_admin", "operator", "auditor"] },
        { to: "/t/templates", label: "话术模板", roles: ["tenant_admin", "operator", "auditor"] },
        { to: "/t/live", label: "实时通话" },
      ],
    },
  ];

  if (["tenant_admin", "operator", "auditor"].includes(role)) {
    groups.push({
      title: "线路与号码",
      items: [
        { to: "/t/trunk", label: "线路配置" },
        { to: "/t/consents", label: "授权台账" },
      ],
    });
  }

  if (["tenant_admin", "operator", "auditor", "agent"].includes(role)) {
    groups.push({
      title: "合规中心",
      items: [
        { to: "/t/audit/complaints", label: "投诉登记" },
        { to: "/t/audit/recordings", label: "录音稽核", roles: ["tenant_admin", "auditor"] },
        { to: "/t/audit/compliance", label: "合规事件", roles: ["tenant_admin", "auditor", "operator"] },
        { to: "/t/audit/dnd", label: "禁呼名单" },
      ],
    });
  }

  if (role === "tenant_admin") {
    groups.push({
      title: "运营分析",
      items: [
        { to: "/t/reports", label: "报表概览" },
        { to: "/t/billing", label: "账单用量" },
      ],
    });
    groups.push({
      title: "组织",
      items: [{ to: "/t/members", label: "成员与权限" }],
    });
  }

  if (role === "agent" || role === "tenant_admin") {
    groups.push({
      title: "坐席",
      items: [{ to: "/agent/queue", label: "我的来电" }],
    });
  }

  return groups.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.roles || item.roles.includes(role)),
  }));
}

export const routeTitles = {
  "/login": "登录",
  "/force-password": "修改密码",
  "/403": "无权限",
  "/t/dashboard": "控制台",
  "/t/campaigns": "外呼任务",
  "/t/lists": "名单导入",
  "/t/consents": "授权台账",
  "/t/templates": "话术模板",
  "/t/live": "实时通话",
  "/t/reports": "报表概览",
  "/t/billing": "账单用量",
  "/t/members": "成员与权限",
  "/t/trunk": "线路配置",
  "/t/audit/recordings": "录音稽核",
  "/t/audit/compliance": "合规事件",
  "/t/audit/dnd": "禁呼名单",
  "/t/audit/complaints": "投诉登记",
  "/agent/queue": "我的来电",
  "/p/tenants": "租户审核",
  "/p/trunks": "熔断状态",
  "/p/policies": "全局告警",
  "/p/billing": "平台指标",
};