import { createRouter, createWebHistory } from "vue-router";
import { useSession } from "../stores/session";

const tenantRoles = ["tenant_admin", "operator", "agent", "auditor"];

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/login" },
    { path: "/login", component: () => import("../views/Login.vue"), meta: { public: true } },
    { path: "/force-password", component: () => import("../views/ForcePassword.vue") },
    { path: "/403", component: () => import("../views/Forbidden.vue") },
    { path: "/t/dashboard", component: () => import("../views/Dashboard.vue"), meta: { roles: tenantRoles } },
    { path: "/t/campaigns", component: () => import("../views/Campaigns.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"] } },
    { path: "/t/campaigns/:id", component: () => import("../views/CampaignDetail.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"] } },
    { path: "/t/lists", component: () => import("../views/Lists.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"] } },
    { path: "/t/consents", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], endpoint: "/api/t/consents", title: "授权台账" } },
    { path: "/t/templates", component: () => import("../views/Templates.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"] } },
    { path: "/t/templates/:id/nodes", component: () => import("../views/TemplateNodes.vue"), meta: { roles: ["tenant_admin", "operator"] } },
    { path: "/t/live", component: () => import("../views/Live.vue"), meta: { roles: tenantRoles } },
    { path: "/agent/queue", component: () => import("../views/DataPage.vue"), meta: { roles: ["agent", "tenant_admin"], endpoint: "/api/t/calls", title: "我的来电" } },
    { path: "/agent/calls/:id", component: () => import("../views/CallSummary.vue"), meta: { roles: ["agent", "tenant_admin", "operator", "auditor"] } },
    { path: "/t/trunk", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], endpoint: "/api/t/trunks", title: "线路与号码" } },
    { path: "/t/members", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin"], endpoint: "/api/t/members", title: "成员与权限" } },
    { path: "/t/reports", component: () => import("../views/DataPage.vue"), meta: { roles: tenantRoles, endpoint: "/api/t/reports/overview", title: "报表" } },
    { path: "/t/billing", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin"], endpoint: "/api/t/billing/summary", title: "账单" } },
    { path: "/t/audit/recordings", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin", "auditor"], endpoint: "/api/t/calls", title: "录音" } },
    { path: "/t/audit/compliance", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin", "auditor", "operator"], endpoint: "/api/t/compliance/events", title: "合规事件" } },
    { path: "/t/audit/dnd", component: () => import("../views/DataPage.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], endpoint: "/api/t/dnd", title: "禁呼名单" } },
    { path: "/t/audit/complaints", component: () => import("../views/AuditComplaints.vue"), meta: { roles: ["tenant_admin", "operator", "auditor", "agent"] } },
    { path: "/p/tenants", component: () => import("../views/DataPage.vue"), meta: { roles: ["platform_admin"], endpoint: "/api/p/tenants", title: "租户审核" } },
    { path: "/p/trunks", component: () => import("../views/DataPage.vue"), meta: { roles: ["platform_admin"], endpoint: "/api/p/circuit-status", title: "熔断状态" } },
    { path: "/p/policies", component: () => import("../views/DataPage.vue"), meta: { roles: ["platform_admin"], endpoint: "/api/p/alerts", title: "全局策略与告警" } },
    { path: "/p/billing", component: () => import("../views/DataPage.vue"), meta: { roles: ["platform_admin"], endpoint: "/api/p/metrics", title: "平台账单与指标" } },
  ],
});

router.beforeEach((to) => {
  const session = useSession();
  session.hydrate();
  if (to.meta.public) return true;
  if (!session.accessToken) return "/login";
  if (session.mustChangePassword && to.path !== "/force-password") return "/force-password";
  const roles = to.meta.roles || [];
  if (roles.length && !roles.includes(session.role)) return "/403";
  return true;
});
