import { createRouter, createWebHistory } from "vue-router";
import { useSession } from "../stores/session";
import { routeTitles } from "../utils/navigation";

const tenantRoles = ["tenant_admin", "operator", "agent", "auditor"];

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/login" },
    { path: "/login", component: () => import("../views/Login.vue"), meta: { public: true, title: routeTitles["/login"] } },
    { path: "/force-password", component: () => import("../views/ForcePassword.vue"), meta: { title: "修改密码" } },
    { path: "/403", component: () => import("../views/Forbidden.vue"), meta: { title: routeTitles["/403"] } },
    { path: "/t/dashboard", component: () => import("../views/Dashboard.vue"), meta: { roles: tenantRoles, title: routeTitles["/t/dashboard"] } },
    { path: "/t/campaigns", component: () => import("../views/Campaigns.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: routeTitles["/t/campaigns"] } },
    { path: "/t/campaigns/:id", component: () => import("../views/CampaignDetail.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: "任务详情" } },
    { path: "/t/lists", component: () => import("../views/Lists.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: routeTitles["/t/lists"] } },
    { path: "/t/consents", component: () => import("../views/Consents.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: routeTitles["/t/consents"] } },
    { path: "/t/templates", component: () => import("../views/Templates.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: routeTitles["/t/templates"] } },
    { path: "/t/templates/:id/nodes", component: () => import("../views/TemplateNodes.vue"), meta: { roles: ["tenant_admin", "operator"], title: "话术节点" } },
    { path: "/t/live", component: () => import("../views/Live.vue"), meta: { roles: tenantRoles, title: routeTitles["/t/live"] } },
    { path: "/agent/queue", component: () => import("../views/AgentQueue.vue"), meta: { roles: ["agent", "tenant_admin"], title: routeTitles["/agent/queue"] } },
    { path: "/agent/calls/:id", component: () => import("../views/CallSummary.vue"), meta: { roles: ["agent", "tenant_admin", "operator", "auditor"], title: "通话小结" } },
    { path: "/t/trunk", component: () => import("../views/Trunk.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: routeTitles["/t/trunk"] } },
    { path: "/t/members", component: () => import("../views/Members.vue"), meta: { roles: ["tenant_admin"], title: routeTitles["/t/members"] } },
    { path: "/t/reports", component: () => import("../views/Reports.vue"), meta: { roles: tenantRoles, title: routeTitles["/t/reports"] } },
    { path: "/t/billing", component: () => import("../views/Billing.vue"), meta: { roles: ["tenant_admin"], title: routeTitles["/t/billing"] } },
    { path: "/t/audit/recordings", component: () => import("../views/AuditRecordings.vue"), meta: { roles: ["tenant_admin", "auditor"], title: routeTitles["/t/audit/recordings"] } },
    { path: "/t/audit/compliance", component: () => import("../views/AuditCompliance.vue"), meta: { roles: ["tenant_admin", "auditor", "operator"], title: routeTitles["/t/audit/compliance"] } },
    { path: "/t/audit/dnd", component: () => import("../views/AuditDnd.vue"), meta: { roles: ["tenant_admin", "operator", "auditor"], title: routeTitles["/t/audit/dnd"] } },
    { path: "/t/audit/complaints", component: () => import("../views/AuditComplaints.vue"), meta: { roles: ["tenant_admin", "operator", "auditor", "agent"], title: routeTitles["/t/audit/complaints"] } },
    { path: "/p/tenants", component: () => import("../views/PlatformTenants.vue"), meta: { roles: ["platform_admin"], title: routeTitles["/p/tenants"] } },
    { path: "/p/trunks", component: () => import("../views/PlatformCircuit.vue"), meta: { roles: ["platform_admin"], title: routeTitles["/p/trunks"] } },
    { path: "/p/policies", component: () => import("../views/PlatformAlerts.vue"), meta: { roles: ["platform_admin"], title: routeTitles["/p/policies"] } },
    { path: "/p/billing", component: () => import("../views/PlatformMetrics.vue"), meta: { roles: ["platform_admin"], title: routeTitles["/p/billing"] } },
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